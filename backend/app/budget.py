"""Cost ledger + per-provider pre-flight budget guard.

Invariant (fail-closed): for every provider P with cap C over its window W,

    spent(P, W) + reserved(P) + projected(call)  <=  C      else the call is REFUSED

`projected` is a worst case: estimated input tokens + the FULL max_output_tokens
the call is allowed to produce (the guard clamps the request to that limit, so
the bound is real, not a guess). Reservations are taken under one lock, so two
concurrent calls cannot jointly overshoot. After the call, the reservation is
settled to the actual usage reported by the provider.

Residual risk (documented, unavoidable app-side): input-token count is an
estimate (deliberately conservative), providers bill with lag, and the app
cannot see spend from other clients sharing the same key/project. The GCP
backstop (docs/gcp-backstop) is the hard stop for the latter.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .db import Database
from .pricing import Price, cost_usd
from .providers import Provider, ProviderStore, price_of

RESERVATION_TTL_S = 3600  # stale reservations (crashed calls) expire after 1 h (long model calls stay reserved)


class BudgetExceeded(Exception):
    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail["message"])


@dataclass
class Reservation:
    id: str
    provider: str
    model: str
    kind: str
    litellm_model: str
    price: Price
    projected_usd: float
    est_input_tokens: int
    max_output_tokens: int


def window_start(period: str, now: float | None = None) -> float:
    now = now or time.time()
    if period == "total":
        return 0.0
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if period == "daily":
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # monthly
        dt = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return dt.timestamp()


def estimate_tokens(text_chars: int, n_messages: int = 1) -> int:
    """Deliberately pessimistic: ~3 chars/token (+15%) plus per-message overhead."""
    return math.ceil(text_chars / 3.0 * 1.15) + 8 * n_messages + 16


class BudgetGuard:
    def __init__(self, db: Database, providers: ProviderStore, credits=None):
        self.db = db
        self.providers = providers
        self.credits = credits          # Credits | None: account balance per provider (see credits.py)

    # ---- accounting views -------------------------------------------------
    def spent(self, p: Provider) -> float:
        r = self.db.one("SELECT COALESCE(SUM(cost_usd),0) s FROM ledger WHERE provider=? AND kind='call' AND ts>=?",
                        (p.id, window_start(p.cap_period)))
        return float(r["s"])

    def reserved(self, provider_id: str) -> float:
        self.db.execute("DELETE FROM reservations WHERE ts < ?", (time.time() - RESERVATION_TTL_S,))
        r = self.db.one("SELECT COALESCE(SUM(amount_usd),0) s FROM reservations WHERE provider=?", (provider_id,))
        return float(r["s"])

    def status(self, p: Provider) -> dict:
        spent, reserved = self.spent(p), self.reserved(p.id)
        refused = self.db.one("SELECT COUNT(*) n FROM ledger WHERE provider=? AND kind='refused' AND ts>=?",
                              (p.id, window_start(p.cap_period)))["n"]
        calls = self.db.one("SELECT COUNT(*) n FROM ledger WHERE provider=? AND kind='call' AND ts>=?",
                            (p.id, window_start(p.cap_period)))["n"]
        credit = self.credits.remaining(p.id) if self.credits else None
        return dict(provider=p.id, label=p.label, enabled=p.enabled, cap_usd=p.cap_usd, cap_period=p.cap_period,
                    spent_usd=spent, reserved_usd=reserved, remaining_usd=max(0.0, p.cap_usd - spent - reserved),
                    credits_remaining_usd=None if credit is None else max(0.0, credit - reserved),
                    credits_strict=bool(self.credits and self.credits.strict(p.id)),
                    spendable_usd=max(0.0, min(p.cap_usd - spent, credit if credit is not None else float("inf")) - reserved),
                    pct_used=(100.0 * spent / p.cap_usd) if p.cap_usd > 0 else (0.0 if spent == 0 else 100.0),
                    calls=calls, refused=refused)

    # ---- the guard ---------------------------------------------------------
    def reserve(self, provider_id: str, model: str, est_input_tokens: int, max_output_tokens: int,
                litellm_model: str) -> Reservation:
        """Pre-flight. Returns a Reservation, or raises BudgetExceeded (and logs a 'refused' row)."""
        with self.db.lock:
            p = self.providers.get(provider_id)
            if p is None:
                raise BudgetExceeded(self._detail(provider_id, model, "unknown_provider", 0, 0, 0, 0, False,
                                                  f"Unknown provider '{provider_id}'"))
            kind = p.kind
            price = price_of(p, model, litellm_model)
            projected = cost_usd(price, est_input_tokens, max_output_tokens)
            spent, reserved = self.spent(p), self.reserved(p.id)
            remaining = p.cap_usd - spent - reserved
            credit = self.credits.remaining(p.id) if self.credits else None
            reason = None
            if not p.enabled:
                reason = "provider_disabled"
            elif projected > remaining + 1e-12:
                reason = "cap_exceeded"
            elif credit is not None and projected > credit - reserved + 1e-12:
                reason, remaining = "credits_insufficient", credit - reserved      # your account can't cover it
            elif credit is None and self.credits and self.credits.strict(p.id) and p.kind != "ollama":
                reason = "credits_unverified"
            if reason:
                msg = self._message(p, model, reason, projected, remaining, price)
                self.db.execute(
                    "INSERT INTO ledger(ts,provider,model,kind,input_tokens,output_tokens,cost_usd,projected_usd,price_known,note)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), p.id, model, "refused", est_input_tokens, 0, 0.0, projected, int(price.known), reason))
                raise BudgetExceeded(self._detail(p.id, model, reason, projected, remaining, p.cap_usd, spent,
                                                  price.known, msg, reserved))
            rid = uuid.uuid4().hex
            self.db.execute("INSERT INTO reservations(id,ts,provider,model,amount_usd) VALUES(?,?,?,?,?)",
                            (rid, time.time(), p.id, model, projected))
            return Reservation(rid, p.id, model, kind, litellm_model, price, projected, est_input_tokens,
                               max_output_tokens)

    def settle(self, r: Reservation, input_tokens: int | None, output_tokens: int | None, note: str | None = None) -> float:
        """Replace the reservation with actual spend. No usage reported -> charge the worst case."""
        with self.db.lock:
            if input_tokens is None or output_tokens is None:
                cost, src = r.projected_usd, "reservation"
                input_tokens, output_tokens = r.est_input_tokens, r.max_output_tokens
            else:
                cost, src = cost_usd(r.price, input_tokens, output_tokens), "usage"
            self.db.execute("DELETE FROM reservations WHERE id=?", (r.id,))
            self.db.execute(
                "INSERT INTO ledger(ts,provider,model,kind,input_tokens,output_tokens,cost_usd,projected_usd,settled_from,price_known,note)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), r.provider, r.model, "call", input_tokens, output_tokens, cost, r.projected_usd, src,
                 int(r.price.known), note))
            return cost

    def release(self, r: Reservation, note: str = "call_failed") -> None:
        """Call errored before producing billable usage. Release reservation; log a zero-cost row."""
        with self.db.lock:
            self.db.execute("DELETE FROM reservations WHERE id=?", (r.id,))
            self.db.execute(
                "INSERT INTO ledger(ts,provider,model,kind,projected_usd,price_known,note) VALUES(?,?,?,?,?,?,?)",
                (time.time(), r.provider, r.model, "refused", r.projected_usd, int(r.price.known), note))

    # ---- helpers -----------------------------------------------------------
    @staticmethod
    def _message(p: Provider, model: str, reason: str, projected: float, remaining: float, price: Price) -> str:
        if reason == "provider_disabled":
            return f"{p.label} is disabled in Settings."
        if reason == "credits_insufficient":
            return (f"Credits guard: call to {p.id}/{model} REFUSED before sending. Projected worst-case ${projected:.4f} "
                    f"exceeds the ${max(remaining, 0):.4f} of account credit left. Top up your {p.label} account, then refresh "
                    f"(or update) the balance in Settings.")
        if reason == "credits_unverified":
            return (f"Credits guard: call to {p.id}/{model} REFUSED - strict credits mode is on for {p.label} and the account "
                    f"balance is unknown. Refresh or enter the balance in Settings, or turn strict mode off.")
        extra = "" if price.known else " (model price unknown - priced at a conservative fallback)"
        return (f"Budget guard: call to {p.id}/{model} REFUSED before sending. Projected worst-case "
                f"${projected:.4f} exceeds remaining ${max(remaining, 0):.4f} of the ${p.cap_usd:.4f} "
                f"{p.cap_period} cap{extra}. Raise the cap in Settings or lower max output tokens.")

    @staticmethod
    def _detail(pid, model, reason, projected, remaining, cap, spent, price_known, message, reserved=0.0) -> dict:
        return dict(error="budget_exceeded" if reason == "cap_exceeded" else reason, reason=reason, provider=pid, model=model,
                    projected_usd=projected, remaining_usd=max(0.0, remaining), cap_usd=cap, spent_usd=spent,
                    reserved_usd=reserved, price_known=price_known, message=message)

    def ledger(self, limit: int = 200, provider: str | None = None) -> list[dict]:
        q, a = "SELECT * FROM ledger", []
        if provider:
            q += " WHERE provider=?"
            a.append(provider)
        q += " ORDER BY id DESC LIMIT ?"
        a.append(limit)
        return [dict(r) for r in self.db.query(q, a)]

    def by_model(self, provider: str | None = None) -> list[dict]:
        q = ("SELECT provider, model, COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,"
             " SUM(cost_usd) cost_usd FROM ledger WHERE kind='call'")
        a: list = []
        if provider:
            q += " AND provider=?"
            a.append(provider)
        q += " GROUP BY provider, model ORDER BY cost_usd DESC"
        return [dict(r) for r in self.db.query(q, a)]
