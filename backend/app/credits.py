"""Account credits per model provider.

Agent Studio's spend caps are local limits. This adds the provider ACCOUNT side: how much credit you actually have.
  * auto   - fetched from the provider's API (only OpenRouter exposes a balance today; refreshed when you connect,
             on demand, and before chats when older than 10 minutes)
  * manual - you type your balance; Agent Studio subtracts every call it makes since then
  * off    - not tracked
The budget guard then refuses a call BEFORE sending it if its worst-case cost exceeds the credits left (minus
in-flight reservations). With `strict` on, calls are refused whenever credits can't be verified at all.
Remaining credit = balance at `as_of` - spend Agent Studio recorded since `as_of` (usage outside Agent Studio is only
reflected after the next auto refresh, or when you update a manual balance).
"""
from __future__ import annotations

import time

import httpx

from .db import Database
from .providers import Provider, _key

AUTO_KINDS = {"openrouter"}            # providers whose API returns an account balance
STALE_S = 600
WHY_NOT = {
    "openai": "OpenAI has no balance API (only an admin cost report) - enter your balance manually.",
    "anthropic": "Anthropic has no balance API (the Admin API reports cost, not credit) - enter your balance manually.",
    "vertex_ai": "Google Cloud billing has no simple credit-balance API - enter your remaining credit/budget manually.",
    "gemini": "The Gemini API has no balance API - enter your balance manually (free tier: rate limits apply instead).",
    "bedrock": "AWS has no credit-balance API for Bedrock - enter your remaining credit/budget manually.",
    "xai": "xAI's public API has no balance endpoint - enter your balance manually.",
    "groq": "Groq has no balance API - enter your balance manually.",
    "ollama": "Local models cost nothing.",
}


class Credits:
    def __init__(self, db: Database):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS provider_credits(
            provider TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'off', balance_usd REAL, as_of REAL, source TEXT,
            strict INTEGER NOT NULL DEFAULT 0, checked_at REAL, error TEXT)""")

    def _row(self, pid: str) -> dict:
        r = self.db.one("SELECT * FROM provider_credits WHERE provider=?", (pid,))
        return dict(r) if r else dict(provider=pid, mode="off", balance_usd=None, as_of=None, source=None, strict=0, checked_at=None, error=None)

    def _save(self, row: dict) -> None:
        self.db.execute("INSERT INTO provider_credits(provider,mode,balance_usd,as_of,source,strict,checked_at,error) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(provider) DO UPDATE SET mode=excluded.mode, balance_usd=excluded.balance_usd, as_of=excluded.as_of, "
                        "source=excluded.source, strict=excluded.strict, checked_at=excluded.checked_at, error=excluded.error",
                        (row["provider"], row["mode"], row["balance_usd"], row["as_of"], row["source"], int(bool(row["strict"])),
                         row["checked_at"], row["error"]))

    def spent_since(self, pid: str, ts: float) -> float:
        r = self.db.one("SELECT COALESCE(SUM(cost_usd),0) s FROM ledger WHERE provider=? AND kind='call' AND ts>=?", (pid, ts))
        return float(r["s"])

    def remaining(self, pid: str) -> float | None:
        """Credits left right now (before in-flight reservations), or None if not tracked/known."""
        r = self._row(pid)
        if r["mode"] == "off" or r["balance_usd"] is None or r["as_of"] is None:
            return None
        return r["balance_usd"] - self.spent_since(pid, r["as_of"])

    def strict(self, pid: str) -> bool:
        return bool(self._row(pid)["strict"])

    def public(self, p: Provider) -> dict:
        r = self._row(p.id)
        rem = self.remaining(p.id)
        return dict(mode=r["mode"], balance_usd=r["balance_usd"], as_of=r["as_of"], source=r["source"], strict=bool(r["strict"]),
                    remaining_usd=None if rem is None else round(rem, 6), checked_at=r["checked_at"], error=r["error"],
                    auto_available=p.kind in AUTO_KINDS, why_not=WHY_NOT.get(p.kind) if p.kind not in AUTO_KINDS else None,
                    spent_since_usd=None if r["as_of"] is None else round(self.spent_since(p.id, r["as_of"]), 6))

    def update(self, p: Provider, mode: str | None = None, balance_usd: float | None = None, strict: bool | None = None) -> None:
        r = self._row(p.id)
        if mode is not None:
            if mode not in ("auto", "manual", "off"):
                raise ValueError("mode must be auto, manual or off")
            if mode == "auto" and p.kind not in AUTO_KINDS:
                raise ValueError(WHY_NOT.get(p.kind, "this provider has no balance API"))
            r["mode"] = mode
        if balance_usd is not None:
            if balance_usd < 0:
                raise ValueError("balance must be >= 0")
            r.update(mode="manual", balance_usd=float(balance_usd), as_of=time.time(), source="entered by you", error=None)
        if strict is not None:
            r["strict"] = int(bool(strict))
        self._save(r)

    async def refresh(self, p: Provider) -> dict:
        """Fetch the balance from the provider (auto kinds). Never raises; errors are stored and shown."""
        r = self._row(p.id)
        if p.kind not in AUTO_KINDS:
            return self.public(p)
        r["checked_at"] = time.time()
        try:
            key = _key(p, p.label)[0]
            base = (p.api_base or "https://openrouter.ai/api/v1").rstrip("/")
            async with httpx.AsyncClient(timeout=15) as c:
                cr = await c.get(f"{base}/credits", headers={"Authorization": f"Bearer {key}"})
                if cr.status_code == 200:
                    d = cr.json().get("data", {})
                    bal, src = float(d["total_credits"]) - float(d["total_usage"]), "OpenRouter account credits"
                else:                                    # /credits needs a management key: fall back to this key's own limit
                    kr = await c.get(f"{base}/key", headers={"Authorization": f"Bearer {key}"})
                    kr.raise_for_status()
                    d = kr.json().get("data", {})
                    if d.get("limit_remaining") is None:
                        raise RuntimeError("this key has no credit limit and can't read the account balance "
                                           "(use a management key, or enter the balance manually)")
                    bal, src = float(d["limit_remaining"]), "OpenRouter key limit remaining"
            r.update(mode="auto" if r["mode"] in ("off", "auto") else r["mode"], error=None)
            if r["mode"] == "auto":
                r.update(balance_usd=max(bal, 0.0), as_of=time.time(), source=src)
        except Exception as e:  # noqa: BLE001
            r["error"] = f"{type(e).__name__}: {e}"[:300]
        self._save(r)
        return self.public(p)

    async def refresh_if_stale(self, p: Provider) -> None:
        r = self._row(p.id)
        if p.kind in AUTO_KINDS and r["mode"] in ("auto", "off") and (not r["checked_at"] or time.time() - r["checked_at"] > STALE_S):
            await self.refresh(p)
