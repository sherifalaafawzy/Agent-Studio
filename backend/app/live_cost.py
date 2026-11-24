"""Live-mode cost visibility: a price table, a running meter, a budget with a warning and a hard stop, and the session history.

Honesty rules this module follows:
* Reported beats estimated. Gemini Live reports `usageMetadata` (the session's tokens so far, per modality) at the end of each turn; that is used as is and
  labelled "reported". Between reports, what has been streamed since (audio you sent, video Google streams, audio it sends back) is ESTIMATED from token
  rates and labelled "estimated". A total made of both says so ("mixed"). Nothing is shown as exact.
* The Gemini price table is DATA, not code: stored as an editable setting with the date it was read and where from, labelled "prices as of <date>, verify".
  Defaults were read from Google's pricing page on 2026-11-23 (Gemini 3.8 Live API, non-global, USD per million tokens).
* The token RATES used for the estimate are also settings. The video rate (about 2,500 tokens per second) and the audio-out rate (about 25 per second) were MEASURED against the real
  service on 2026-11-23 (13,416 video tokens for about 5.3 s; 53 audio tokens for 2.1 s); the audio-in rate is an assumption, shown as such.
* Local engines cost nothing in the cloud, but their usage (audio seconds, tokens) is still counted and shown.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable

import httpx

PRICING_URL = "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing"
SETTING = "live_gemini_prices"
WARN_AT = 0.8

DEFAULT_PRICES: dict[str, Any] = {
    "as_of": "2026-11-23", "source": PRICING_URL, "model": "gemini-3.8-live", "region_class": "non-global",
    "in": {"TEXT": 0.75, "AUDIO": 3.00, "VIDEO": 1.00, "IMAGE": 1.00},          # USD per million tokens
    "out": {"TEXT": 4.50, "AUDIO": 12.00, "VIDEO": 1.00},
    "rates": {"audio_in_tokens_per_s": 25.0, "audio_out_tokens_per_s": 25.0, "video_out_tokens_per_s": 2500.0},
    "rates_note": "video and audio-out rates measured on 2026-11-23 against the real service; the audio-in rate is an assumption - Google's pages that were read do not state it",
}


# ---------------------------------------------------------------- the price table
def load_prices(db: Any) -> dict:
    """The stored table merged over the defaults (so a partial or damaged setting still gives a complete, usable table)."""
    out = json.loads(json.dumps(DEFAULT_PRICES))
    try:
        raw = json.loads(db.get_setting(SETTING, "") or "{}")
    except ValueError:
        raw = {}
    if isinstance(raw, dict):
        for k in ("as_of", "source", "model", "region_class", "rates_note"):
            if isinstance(raw.get(k), str) and raw[k]:
                out[k] = raw[k][:200]
        for d in ("in", "out"):
            for mod, v in (raw.get(d) or {}).items() if isinstance(raw.get(d), dict) else []:
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    out[d][str(mod).upper()[:20]] = float(v)
        for k, v in (raw.get("rates") or {}).items() if isinstance(raw.get("rates"), dict) else []:
            if k in out["rates"] and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                out["rates"][k] = float(v)
    return out


def validate_prices(raw: Any) -> dict:
    """A price table from the API, checked: unknown keys and bad numbers are refused with a message."""
    if not isinstance(raw, dict):
        raise ValueError("the price table must be an object")
    out: dict[str, Any] = {}
    for k in ("as_of", "source", "model", "region_class", "rates_note"):
        if k in raw:
            if not isinstance(raw[k], str) or len(raw[k]) > 200:
                raise ValueError(f"{k} must be text of at most 200 characters")
            out[k] = raw[k]
    for d in ("in", "out"):
        if d in raw:
            if not isinstance(raw[d], dict):
                raise ValueError(f"{d} must map a modality (TEXT, AUDIO, VIDEO, IMAGE) to dollars per million tokens")
            out[d] = {}
            for mod, v in raw[d].items():
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1000:
                    raise ValueError(f"{d}.{mod} must be a number from 0 to 1000 (USD per million tokens)")
                out[d][str(mod).upper()[:20]] = float(v)
    if "rates" in raw:
        if not isinstance(raw["rates"], dict):
            raise ValueError("rates must be an object")
        out["rates"] = {}
        for k, v in raw["rates"].items():
            if k not in DEFAULT_PRICES["rates"]:
                raise ValueError(f"unknown rate '{str(k)[:40]}'")
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 100000:
                raise ValueError(f"rates.{k} must be a number from 0 to 100000")
            out["rates"][k] = float(v)
    unknown = set(raw) - {"as_of", "source", "model", "region_class", "rates_note", "in", "out", "rates"}
    if unknown:
        raise ValueError(f"unknown field '{sorted(unknown)[0][:40]}'")
    return out


def save_prices(db: Any, patch: dict) -> dict:
    cur = load_prices(db)
    for k, v in patch.items():
        if k in ("in", "out", "rates"):
            cur[k].update(v)
        else:
            cur[k] = v
    db.set_setting(SETTING, json.dumps(cur))
    return cur


_LABELS = {"text": ("TEXT",), "video, image": ("VIDEO", "IMAGE"), "audio": ("AUDIO",), "text (response and reasoning)": ("TEXT",), "video (avatar)": ("VIDEO",)}


def parse_pricing(html: str, model_label: str = "Gemini 3.8 Live API") -> dict | None:
    """The Live model's per-million-token prices from Google's pricing page (any HTML), or None when the page does not look as expected."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    i = text.find(model_label)
    if i < 0:
        return None
    block = text[i + len(model_label):]
    j = re.search(r"Gemini \d", block)
    block = block[:j.start()] if j else block[:1500]
    found: dict[str, dict[str, float]] = {"in": {}, "out": {}}
    for m in re.finditer(r"(Input|Output): ([A-Za-z, ()]+?) (?:Non-global|Global|Regional) \$([0-9]+(?:\.[0-9]+)?)", block):
        mods = _LABELS.get(m.group(2).strip().lower())
        if mods:
            for mod in mods:
                found["in" if m.group(1) == "Input" else "out"][mod] = float(m.group(3))
    return found if found["in"] and found["out"] else None


async def refresh_prices(db: Any, transport: httpx.AsyncBaseTransport | None = None, today: str | None = None) -> dict:
    """Read Google's pricing page now and store what it says. Raises ValueError (with the reason) when the page cannot be read or does not list the model."""
    try:
        async with httpx.AsyncClient(timeout=30, transport=transport, follow_redirects=True) as c:
            r = await c.get(PRICING_URL, headers={"User-Agent": "agent-studio-price-check"})
    except httpx.HTTPError as e:
        raise ValueError(f"could not read Google's pricing page: {type(e).__name__}") from e
    if r.status_code != 200:
        raise ValueError(f"Google's pricing page answered {r.status_code}")
    found = parse_pricing(r.text)
    if found is None:
        raise ValueError("Google's pricing page no longer lists 'Gemini 3.8 Live API' in the layout this reader knows; edit the prices by hand")
    return save_prices(db, {"in": found["in"], "out": found["out"], "as_of": today or time.strftime("%Y-%m-%d"), "source": PRICING_URL})


# ---------------------------------------------------------------- the meter
ROW_LABELS = {("in", "TEXT"): "Text you sent", ("in", "AUDIO"): "Your voice (audio in)", ("in", "VIDEO"): "Video you sent", ("in", "IMAGE"): "Images you sent",
              ("out", "TEXT"): "Text written", ("out", "AUDIO"): "Its voice (audio out)", ("out", "VIDEO"): "Avatar video (out)"}


class CostMeter:
    """Running usage and cost of one live session. `kind` is "cloud" (Gemini: priced from tokens) or "local" (this PC: usage only, plus any cloud model's spend)."""

    def __init__(self, prices: dict, kind: str = "cloud", budget_usd: float = 0.0, clock: Callable[[], float] = time.monotonic, avatar: bool = False):
        self.prices, self.kind, self.budget, self.clock, self.avatar = prices, kind, float(budget_usd or 0), clock, avatar
        self.t0 = clock()
        self.audio_in_s = self.audio_out_s = 0.0
        self.video_since: float | None = None
        self.reported: dict[tuple[str, str], int] = {}
        self.reported_at: tuple[float, float, float] | None = None       # (audio_in_s, audio_out_s, video seconds) when the last usage report arrived
        self.reports = 0
        self.local_tokens = {"in": 0, "out": 0}
        self.local_audio_in_s = self.local_audio_out_s = 0.0
        self.cloud_usd = 0.0                                                # a cloud model's spend (an agent turn or a cloud provider) in a local session
        self.level = "ok"

    # -- inputs
    def add_audio_in(self, seconds: float) -> None:
        self.audio_in_s += seconds

    def add_audio_out(self, seconds: float) -> None:
        self.audio_out_s += seconds

    def video_started(self) -> None:
        if self.video_since is None:
            self.video_since = self.clock()

    def video_s(self) -> float:
        return (self.clock() - self.video_since) if self.video_since is not None else 0.0

    def report(self, usage: dict) -> None:
        """Gemini's `usageMetadata`: the session's tokens so far, per modality. Accepts both camelCase and snake_case and the field names seen from the real service."""
        def g(d: Any, *names: str) -> Any:
            for n in names:
                if isinstance(d, dict) and n in d:
                    return d[n]
            return None
        got: dict[tuple[str, str], int] = {}
        for direction, names in (("in", ("promptTokensDetails", "prompt_tokens_details")),
                                 ("out", ("candidatesTokensDetails", "candidates_tokens_details", "responseTokensDetails", "response_tokens_details"))):
            for row in g(usage, *names) or []:
                mod = str(g(row, "modality") or "").upper().replace("MODALITY_", "")
                n = g(row, "tokenCount", "token_count") or 0
                if mod and isinstance(n, (int, float)):
                    got[(direction, mod)] = got.get((direction, mod), 0) + int(n)
        if got:
            self.reported, self.reports = got, self.reports + 1
            self.reported_at = (self.audio_in_s, self.audio_out_s, self.video_s())

    def add_local(self, tokens_in: int = 0, tokens_out: int = 0, audio_in_s: float = 0.0, audio_out_s: float = 0.0) -> None:
        self.local_tokens["in"] += tokens_in
        self.local_tokens["out"] += tokens_out
        self.local_audio_in_s += audio_in_s
        self.local_audio_out_s += audio_out_s

    def set_cloud(self, usd: float) -> None:
        """The cumulative spend of a cloud model used by a local session (an agent turn, or a cloud provider chosen for live mode)."""
        self.cloud_usd = max(0.0, float(usd))

    # -- outputs
    def _price(self, direction: str, mod: str) -> float:
        table = self.prices[direction]
        return float(table.get(mod, table.get("TEXT", 0.0)))

    def _extra_tokens(self) -> dict[tuple[str, str], float]:
        """Tokens streamed since the last usage report (or since the start), estimated from the rates: audio you sent, audio it sent back, video Google streams."""
        r = self.prices["rates"]
        a_in0, a_out0, v0 = self.reported_at or (0.0, 0.0, 0.0)
        extra = {("in", "AUDIO"): max(0.0, self.audio_in_s - a_in0) * r["audio_in_tokens_per_s"], ("out", "AUDIO"): max(0.0, self.audio_out_s - a_out0) * r["audio_out_tokens_per_s"]}
        if self.avatar:
            extra[("out", "VIDEO")] = max(0.0, self.video_s() - v0) * r["video_out_tokens_per_s"]
        return {k: v for k, v in extra.items() if v > 0}

    def snapshot(self) -> dict:
        elapsed = max(0.0, self.clock() - self.t0)
        if self.kind == "local":
            rows = [{"label": "Your voice (speech recognition)", "tokens": None, "seconds": round(self.local_audio_in_s, 1), "usd": 0.0, "kind": "local"},
                    {"label": "Its voice (speech synthesis)", "tokens": None, "seconds": round(self.local_audio_out_s, 1), "usd": 0.0, "kind": "local"},
                    {"label": "Model, tokens read", "tokens": self.local_tokens["in"], "seconds": None, "usd": 0.0, "kind": "local"},
                    {"label": "Model, tokens written", "tokens": self.local_tokens["out"], "seconds": None, "usd": 0.0, "kind": "local"}]
            if self.cloud_usd:
                rows.append({"label": "A cloud model used by this session", "tokens": None, "seconds": None, "usd": round(self.cloud_usd, 6), "kind": "reported"})
            total, source = self.cloud_usd, ("local" if not self.cloud_usd else "reported")
            tokens = dict(self.local_tokens)
            seconds = {"audio_in": round(self.local_audio_in_s, 1), "audio_out": round(self.local_audio_out_s, 1), "video": 0.0}
        else:
            extra = self._extra_tokens()
            rows, total = [], 0.0
            for key in sorted(set(self.reported) | set(extra)):
                rep, est = self.reported.get(key, 0), extra.get(key, 0.0)
                usd = (rep + est) * self._price(*key) / 1e6
                total += usd
                rows.append({"label": ROW_LABELS.get(key, f"{key[1].title()} ({key[0]})"), "tokens": int(round(rep + est)), "seconds": None, "usd": round(usd, 6),
                             "kind": "reported" if est == 0 else ("estimated" if rep == 0 else "mixed")})
            has_est = any(r["kind"] != "reported" for r in rows)
            source = "none" if not rows else ("estimated" if self.reports == 0 else ("mixed" if has_est else "reported"))
            tokens = {"in": sum(r["tokens"] for r in rows if r["label"] in (ROW_LABELS[("in", m)] for m in ("TEXT", "AUDIO", "VIDEO", "IMAGE"))),
                      "out": sum(r["tokens"] for r in rows if r["label"] in (ROW_LABELS[("out", m)] for m in ("TEXT", "AUDIO", "VIDEO")))}
            seconds = {"audio_in": round(self.audio_in_s, 1), "audio_out": round(self.audio_out_s, 1), "video": round(self.video_s(), 1)}
        pct = (total / self.budget) if self.budget > 0 else 0.0
        return {"elapsed_s": round(elapsed, 1), "cost_usd": round(total, 6), "per_min_usd": round(total / (elapsed / 60), 4) if elapsed >= 5 and total else None, "source": source,
                "cloud": self.kind == "cloud" or bool(self.cloud_usd), "tokens": tokens, "seconds": seconds, "rows": rows, "budget_usd": self.budget or None,
                "budget_pct": round(pct * 100, 1), "level": self.level, "reports": self.reports, "prices_as_of": self.prices.get("as_of")}

    def check(self) -> str | None:
        """None, "warn" (first time 80% of the budget is reached) or "stop" (100%). Each is returned once."""
        if self.budget <= 0:
            return None
        total = self.snapshot()["cost_usd"]
        if total >= self.budget and self.level != "stop":
            self.level = "stop"
            return "stop"
        if total >= self.budget * WARN_AT and self.level == "ok":
            self.level = "warn"
            return "warn"
        return None


def budget_message(level: str, snap: dict) -> str:
    cap, cost, kind = snap["budget_usd"], snap["cost_usd"], snap["source"]
    how = {"reported": "as reported by Google", "estimated": "estimated", "mixed": "reported plus an estimate since the last report", "none": "estimated"}.get(kind, "")
    if level == "warn":
        return f"This session has used about ${cost:.2f} of its ${cap:.2f} budget ({'80%; ' + how if how else '80%'}). It will be stopped at ${cap:.2f}."
    return f"Stopped: this session reached its ${cap:.2f} budget (about ${cost:.2f}{', ' + how if how else ''}). Start a new session, or raise the budget in the agent or in the Live settings."


async def enforce(session: Any, snap: dict | None = None) -> None:
    """Check the budget now: warn once at 80%, stop the session at 100% with a clear message."""
    snap = snap or session.meter.snapshot()
    level = session.meter.check()
    if level == "warn":
        await session.send_json({"type": "budget", "level": "warn", "message": budget_message("warn", snap)})
    elif level == "stop":
        await session.send_json({"type": "budget", "level": "stop", "message": budget_message("stop", snap)})
        await session.send_json({"type": "error", "message": budget_message("stop", snap)})
        await session.close()


async def watch(session: Any, interval: float = 1.0) -> None:
    """While a session is open: once a second send `usage` to the browser, warn at 80% of the budget, and end the session at 100%."""
    while not session.closed:
        await asyncio.sleep(interval)
        snap = session.meter.snapshot()
        await session.send_json({"type": "usage", **snap})
        await enforce(session, snap)


# ---------------------------------------------------------------- history
HISTORY_SCHEMA = """CREATE TABLE IF NOT EXISTS live_sessions(
  id TEXT PRIMARY KEY, started REAL NOT NULL, ended REAL NOT NULL, agent TEXT NOT NULL DEFAULT '', engine TEXT NOT NULL, mode TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', voice TEXT NOT NULL DEFAULT '', seconds REAL NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
  cost_source TEXT NOT NULL DEFAULT 'none', budget_usd REAL, ended_by TEXT NOT NULL DEFAULT 'user', detail TEXT NOT NULL DEFAULT '{}')"""


def ensure_history(db: Any) -> None:
    db.execute(HISTORY_SCHEMA)


def record_session(db: Any, sid: str, started: float, info: dict, snap: dict, ended_by: str) -> None:
    """One row per session: what it was, how long, what it cost (and whether that is reported or estimated), and why it ended."""
    db.execute("INSERT OR REPLACE INTO live_sessions(id,started,ended,agent,engine,mode,provider,model,voice,seconds,cost_usd,cost_source,budget_usd,ended_by,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, started, time.time(), info.get("agent", ""), info.get("engine", ""), info.get("mode", "voice"), info.get("provider", ""), info.get("model", ""), info.get("voice", ""),
                snap["elapsed_s"], snap["cost_usd"], snap["source"], snap["budget_usd"], ended_by,
                json.dumps({"tokens": snap["tokens"], "seconds": snap["seconds"], "rows": snap["rows"], "prices_as_of": snap["prices_as_of"], "reports": snap["reports"]})))
    if info.get("engine") == "gemini" and snap["cost_usd"] > 0:      # into the spend ledger too, so Billing and the provider's cap see it
        db.execute("INSERT INTO ledger(ts,provider,model,kind,input_tokens,output_tokens,cost_usd,projected_usd,settled_from,price_known,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (time.time(), info.get("provider", "") or "vertex_ai", info.get("model", "") or "gemini-3.8-live", "call", int(snap["tokens"].get("in") or 0), int(snap["tokens"].get("out") or 0),
                    snap["cost_usd"], snap["cost_usd"], "usage" if snap["source"] == "reported" else "estimate", 1, f"live_session:{sid} ({snap['source']})"))


def list_sessions(db: Any, limit: int = 50) -> list[dict]:
    rows = db.query("SELECT * FROM live_sessions ORDER BY started DESC LIMIT ?", (max(1, min(int(limit), 500)),))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"] or "{}")
        except ValueError:
            d["detail"] = {}
        out.append(d)
    return out
