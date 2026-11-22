"""Auto mode: pick the provider and model for each message, so the user does not have to.

Two engines produce the same SIGNALS about a message (how demanding it is, whether it involves code, deep reasoning, tools):
  * "rules"  - built-in heuristics (length, code fences, keywords). Always available, no download.
  * "d1-2b" / "d1-3b" - the local d1 decision model answers typed questions about the message in one forward pass (free,
    milliseconds once warm). If it is not installed, still loading, errors or is too slow, Auto uses the rules and SAYS so.
The signals then choose a TIER (fast "flash" or strong "pro") and the cheapest adequate model of that tier among the
providers that are enabled, configured, allowed and still within their spend cap / credits. Nothing here calls an LLM, and a
route never goes past a cap: the guard still reserves worst-case spend on the real call.

Price is used as a stand-in for capability inside a tier (we have no benchmark data): "quality" picks the priciest known pro
model, every other setting picks the cheapest.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import litellm

from . import d1_spec
from .budget import estimate_tokens
from .d1_client import D1Worker, WorkerUnavailable
from .pricing import cost_usd
from .providers import Provider, context_of, litellm_target, mock_enabled, price_of, tier_model

AUTO_ID = "auto"
ENGINES = ("rules", "d1-2b", "d1-3b")
PREFERENCES = ("cost", "balanced", "quality")
LEVELS = ("simple", "moderate", "demanding")
DEFAULT_CONFIG: dict[str, Any] = dict(enabled=True, engine="rules", preference="balanced", allow=[], deny=[], fallback=True,
                                      timeout_s=4.0, tier_models={"flash": "", "pro": ""})
CONFIG_KEY = "auto_config"
MAX_STATE_CHARS = 4000
CONTEXT_MESSAGES = 3
CONTEXT_CHARS = 400
PROMPT_OVERHEAD = 800           # tokens every plain chat call carries besides the message (system prompt)
AGENT_OVERHEAD = 8000          # ... and an agent's (instructions, tool definitions, skills)
WINDOW_FRACTION = 0.8             # a model is skipped when the prompt alone would fill more than this share of its window


class AutoError(ValueError):
    """Auto cannot route this message. The message is shown to the user."""


class RouterUnavailable(Exception):
    """The chosen engine could not give an answer (not installed, loading, error, timeout). Auto falls back to the rules."""


# ------------------------------------------------------------------------------------------------------- signals
@dataclass
class Signals:
    complexity: int = 0                        # 0 simple, 1 moderate, 2 demanding
    code: bool = False
    reasoning: bool = False
    tools: bool = False
    vision: bool = False
    est_tokens: int = 0
    confidence: float = 0.6
    why: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return dict(complexity=LEVELS[self.complexity], code=self.code, reasoning=self.reasoning, tools=self.tools,
                    vision=self.vision, est_tokens=self.est_tokens)


_CODE = re.compile(r"(```|\bdef \w+\(|\bclass \w+|=>|#include|\bimport \w+|\bselect\b.+\bfrom\b|traceback|stack ?trace|\.(py|tsx?|jsx?|rs|go|java|cs|rb|php|sql|yml|json)\b"
                   r"|\b(refactor|debug|implement|unit tests?|compile|regex|sql|endpoint|bug|algorithm|typescript|python|javascript|dockerfile|function|git)\b)", re.I)
_REASON = re.compile(r"\b(prove|proof|step[- ]by[- ]step|derive|analy[sz]e|trade-?offs?|pros and cons|architecture|strategy|optimi[sz]e|"
                     r"why (does|do|is|are)|explain why|calculate|puzzle|reason about|root cause|evaluate|design a|plan (a|the|for))\b", re.I)


def rule_signals(message: str, context: list[str], agent: bool = False, has_images: bool = False) -> Signals:
    """Heuristics only: cheap, predictable, explainable."""
    text = message.strip()
    n, lines, questions = len(text), text.count("\n"), text.count("?")
    code, reasoning = bool(_CODE.search(text)), bool(_REASON.search(text))
    points = int(n > 400) + 2 * int(n > 1500) + int(lines > 8) + int(questions > 2) + int(code) + int(reasoning)
    complexity = 0 if points == 0 else 1 if points <= 2 else 2
    est = estimate_tokens(n + sum(len(c) for c in context), 2)
    why = [f"{n} characters"] + (["code"] if code else []) + (["reasoning words"] if reasoning else []) + (["several questions"] if questions > 2 else [])
    return Signals(complexity, code, reasoning, agent, has_images, est, 0.6, why)


def d1_questions(message: str, context: list[str]) -> tuple[Any, dict]:
    """The state and typed questions the decision model answers about one message."""
    state: dict[str, Any] = {"user_request": message[:MAX_STATE_CHARS]}
    if context:
        state["earlier_messages"] = [c[:CONTEXT_CHARS] for c in context[-CONTEXT_MESSAGES:]]
    return state, {
        "complexity": {"type": "score", "instructions": "How demanding is the user's request for an AI assistant to answer well?",
                       "criteria": ["Easy: chit-chat, a simple fact, a short rewrite or a one-line answer",
                                    "Moderate: a normal question or task that needs some thought or a few steps",
                                    "Hard: long, multi-part, technical or analytical work that needs careful reasoning"]},
        "code": {"type": "noul", "instructions": "Does the request involve writing, reading, debugging or explaining source code or shell commands?"},
        "reasoning": {"type": "noul", "instructions": "Does the request need careful multi-step reasoning, maths, analysis or planning to answer well?"},
        "tools": {"type": "noul", "instructions": "Does the request ask the assistant to take actions such as reading or editing files, running commands, browsing or searching?"},
    }


def d1_signals(answers: dict, est_tokens: int, agent: bool, has_images: bool) -> Signals:
    """Turn the decision model's answers into signals. Raises RouterUnavailable if they are not in the expected shape."""
    try:
        level = float(answers["complexity"]["score"])
        conf = [float(answers["complexity"]["confidence"])]
        flags = {}
        for name in ("code", "reasoning", "tools"):
            p = float(answers[name]["noul"])
            flags[name] = p >= 0.5
            conf.append(abs(p - 0.5) * 2)
    except (KeyError, TypeError, ValueError) as e:
        raise RouterUnavailable(f"unexpected answer from the decision model ({type(e).__name__})") from e
    complexity = 0 if level < 0.6 else 1 if level < 1.4 else 2
    return Signals(complexity, flags["code"], flags["reasoning"], flags["tools"] or agent, has_images, est_tokens,
                   round(sum(conf) / len(conf), 2), [f"complexity score {level:.1f} of 2"])


def need_pro(sig: Signals, preference: str) -> bool:
    """Does this message deserve the strong ("pro") tier, given what the user prefers to spend?"""
    need = sig.complexity + 2 * sig.reasoning + (int(sig.code) + int(sig.tools) if sig.complexity >= 1 else 0)
    if preference == "quality":
        return need >= 1
    if preference == "cost":
        return need >= 4 or (sig.complexity == 2 and (sig.reasoning or sig.code))
    return need >= 3 or sig.complexity == 2


# --------------------------------------------------------------------------------------------------- candidates
@dataclass
class Candidate:
    provider: str
    model: str
    tier: str                      # "flash" | "pro"
    kind: str
    label: str
    in_per_m: float
    out_per_m: float
    known: bool
    projected: float
    vision: bool = False

    @property
    def blended(self) -> float:
        return 0.5 * self.in_per_m + 0.5 * self.out_per_m

    def ref(self) -> dict:
        return {"provider": self.provider, "model": self.model, "tier": self.tier}


@dataclass
class Route:
    provider: str
    model: str
    tier: str
    engine: str                    # the engine that produced the signals: "rules" | "d1-2b" | "d1-3b"
    reason: str
    confidence: float
    signals: dict
    price: dict
    alternates: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    requested_engine: str = "rules"
    engine_note: str = ""
    preference: str = "balanced"

    def public(self) -> dict:
        return dict(provider=self.provider, model=self.model, tier=self.tier, engine=self.engine, reason=self.reason,
                    confidence=self.confidence, signals=self.signals, price=self.price, alternates=self.alternates, skipped=self.skipped,
                    requested_engine=self.requested_engine, engine_note=self.engine_note, preference=self.preference)


def _matches(rule: str, provider: str, model: str) -> bool:
    return rule == provider or rule == f"{provider}/{model}"


class AutoRouter:
    def __init__(self, db, providers, guard, d1_ready: Callable[[str], bool] | None = None,
                 worker_factory: Callable[[str], D1Worker] | None = None):
        self.db, self.providers, self.guard = db, providers, guard
        self.d1_ready = d1_ready or (lambda model: d1_spec.is_ready(None, model))
        self._worker_factory = worker_factory or D1Worker
        self._worker: D1Worker | None = None

    # ------------------------------------------------------------ config
    def config(self) -> dict:
        try:
            raw = json.loads(self.db.get_setting(CONFIG_KEY, "{}") or "{}")
        except json.JSONDecodeError:
            raw = {}
        cfg = {**DEFAULT_CONFIG, **{k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if k in DEFAULT_CONFIG}}
        cfg["tier_models"] = {**DEFAULT_CONFIG["tier_models"], **(cfg["tier_models"] if isinstance(cfg["tier_models"], dict) else {})}
        return cfg

    def set_config(self, patch: dict) -> dict:
        cfg = self.config()
        for k, v in patch.items():
            if v is None or k not in DEFAULT_CONFIG:
                continue
            if k == "engine" and v not in ENGINES:
                raise AutoError(f"engine must be one of: {', '.join(ENGINES)}")
            if k == "preference" and v not in PREFERENCES:
                raise AutoError(f"preference must be one of: {', '.join(PREFERENCES)}")
            if k in ("allow", "deny"):
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise AutoError(f"{k} must be a list of 'provider' or 'provider/model' strings")
                v = [x.strip() for x in v if x.strip()]
            if k == "timeout_s":
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.5 <= v <= 30:
                    raise AutoError("timeout must be between 0.5 and 30 seconds")
                v = float(v)
            if k == "tier_models":
                if not isinstance(v, dict) or not set(v) <= {"flash", "pro"}:
                    raise AutoError("tier_models must look like {flash: 'provider/model', pro: 'provider/model'}")
                for tier, ref in v.items():
                    pid = str(ref).split("/", 1)[0]
                    if ref and (self.providers.get(pid) is None or "/" not in str(ref)):
                        raise AutoError(f"the {tier} override must be 'provider/model' with a provider you have added (got '{ref}')")
                v = {**cfg["tier_models"], **{t: str(r) for t, r in v.items()}}
            if k in ("enabled", "fallback"):
                v = bool(v)
            cfg[k] = v
        self.db.set_setting(CONFIG_KEY, json.dumps(cfg))
        if cfg["engine"] in d1_spec.MODELS and cfg["enabled"] and self.d1_ready(cfg["engine"]):
            self._worker_for(cfg["engine"]).warm()       # load in the background so the first message is fast
        return cfg

    # ------------------------------------------------------------ providers and models
    def candidates(self, cfg: dict, message_chars: int = 0, max_tokens: int | None = None, only_provider: str | None = None,
                   need_vision: bool = False, est_tokens: int = 0) -> tuple[list[Candidate], list[dict]]:
        """Every (provider, model, tier) Auto may use for this message, and why the others were left out."""
        out: list[Candidate] = []
        skipped: list[dict] = []
        est_in = est_tokens or estimate_tokens(message_chars, 2)
        for p in self.providers.list():
            if (p.kind == "mock" and not mock_enabled()) or (only_provider and p.id != only_provider):
                continue
            if not p.enabled:
                skipped.append({"provider": p.id, "why": "turned off in Settings"})
                continue
            tiers = [("flash", tier_model(p, "flash"))] if p.kind == "ollama" else [("flash", tier_model(p, "flash")), ("pro", tier_model(p, "pro"))]
            for tier in ("flash", "pro"):                                   # an explicit per-tier override from Settings
                ref = cfg["tier_models"].get(tier) or ""
                if ref and ref.split("/", 1)[0] == p.id:
                    tiers = [t for t in tiers if t[0] != tier] + [(tier, ref.split("/", 1)[1])]
            st = self.guard.status(p)
            for tier, model in tiers:
                why = self._excluded(p, model, cfg, st)
                if why:
                    skipped.append({"provider": p.id, "model": model, "why": why})
                    continue
                try:
                    lm, _ = litellm_target(p, model)
                except ValueError as e:
                    skipped.append({"provider": p.id, "why": str(e)})
                    break                                                   # a provider-level problem: no need to say it twice
                price = price_of(p, model, lm)
                mx = min(max_tokens or p.max_output_tokens, p.max_output_tokens)
                projected = cost_usd(price, est_in, mx)
                if projected > st["spendable_usd"] + 1e-12:
                    skipped.append({"provider": p.id, "model": model, "why": f"over budget: this message could cost ${projected:.4f} at worst, "
                                                                          f"${st['spendable_usd']:.4f} is left under its cap or credits"})
                    continue
                info = litellm.model_cost.get(lm) or {}
                window = info.get("max_input_tokens") or context_of(p, model)
                if window and est_in > WINDOW_FRACTION * window:
                    skipped.append({"provider": p.id, "model": model, "why": f"the message is too long for its {window:,}-token window"})
                    continue
                vision = bool(info.get("supports_vision"))
                if need_vision and not vision:
                    skipped.append({"provider": p.id, "model": model, "why": "cannot read images"})
                    continue
                out.append(Candidate(p.id, model, tier, p.kind, p.label, price.input_per_m, price.output_per_m, price.known, projected, vision))
        for tier in ("flash", "pro"):                                       # an override replaces everything else for its tier
            ref = cfg["tier_models"].get(tier) or ""
            if ref:
                pid, _, model = ref.partition("/")
                mine = [c for c in out if c.tier == tier and c.provider == pid and c.model == model]
                if mine:
                    out = [c for c in out if c.tier != tier] + mine
                else:
                    skipped.append({"provider": pid, "model": model, "why": f"the {tier} override is not usable right now; using the normal choice"})
        return out, skipped

    def _excluded(self, p: Provider, model: str, cfg: dict, st: dict) -> str | None:
        if any(_matches(r, p.id, model) for r in cfg["deny"]):
            return "on your block list"
        if cfg["allow"] and not any(_matches(r, p.id, model) for r in cfg["allow"]):
            return "not on your allow list"
        if st["credits_strict"] and st["credits_remaining_usd"] is None and p.kind != "ollama":
            return "strict credits mode is on and the account balance is unknown"
        return None

    def availability(self) -> dict:
        """Is Auto usable at all, and if not, why. Used by the UI to decide whether to offer it."""
        cfg = self.config()
        if not cfg["enabled"]:
            return {"available": False, "reason": "Auto is turned off in Settings > Auto."}
        cands, skipped = self.candidates(cfg)
        if cands:
            return {"available": True, "reason": "", "models": len({(c.provider, c.model) for c in cands})}
        why = "; ".join(f"{s['provider']}{'/' + s['model'] if s.get('model') else ''}: {s['why']}" for s in skipped[:4])
        return {"available": False, "reason": "No model provider is ready for Auto" + (f" ({why})." if why else
                                                                                         ". Add and enable one in Settings > Model providers.")}

    # ------------------------------------------------------------ the decision model
    def _worker_for(self, model: str) -> D1Worker:
        if self._worker is None or self._worker.model != model:
            if self._worker is not None:
                self._worker.close()
            self._worker = self._worker_factory(model)
        return self._worker

    def d1_states(self) -> dict:
        return {"active": self._worker.public() if self._worker else None}

    def close(self) -> None:
        if self._worker is not None:
            self._worker.close()
            self._worker = None

    async def _d1_signals(self, engine: str, message: str, context: list[str], timeout: float, agent: bool, has_images: bool) -> Signals:
        if not self.d1_ready(engine):
            raise RouterUnavailable("it is not installed (Settings > Auto > Install)")
        worker = self._worker_for(engine)
        state, questions = d1_questions(message, context)
        try:
            reply = await asyncio.to_thread(worker.decide, state, questions, timeout)
        except WorkerUnavailable as e:
            raise RouterUnavailable(str(e)) from e
        if not reply.get("ok"):
            raise RouterUnavailable(str(reply.get("error") or "no answer"))
        est = estimate_tokens(len(message) + sum(len(c) for c in context), 2)
        return d1_signals(reply.get("answers") or {}, est, agent, has_images)

    # ------------------------------------------------------------ routing
    async def route(self, message: str, context: list[str] | None = None, agent: bool = False, has_images: bool = False,
                    max_tokens: int | None = None, only_provider: str | None = None, override: dict | None = None,
                    history_chars: int = 0) -> Route:
        """Choose a provider and model for this message. Raises AutoError when nothing can take it."""
        cfg = {**self.config(), **(override or {})}
        if cfg["engine"] not in ENGINES or cfg["preference"] not in PREFERENCES:
            raise AutoError(f"engine must be one of {', '.join(ENGINES)} and preference one of {', '.join(PREFERENCES)}")
        context = context or []
        engine, note, sig = cfg["engine"], "", None
        if engine in d1_spec.MODELS:
            label = d1_spec.spec(engine).label
            try:
                sig = await self._d1_signals(engine, message, context, float(cfg["timeout_s"]), agent, has_images)
            except RouterUnavailable as e:
                note = f"{label} could not be used ({e}), so the built-in rules decided."
        used = engine if sig else "rules"
        sig = sig or rule_signals(message, context, agent, has_images)
        # what the real call will send: the message, the whole chat so far and the fixed prompt - so a model the guard would
        # refuse (its worst case exceeds the cap or credits left) is never chosen
        est_in = estimate_tokens(len(message) + max(history_chars, sum(len(c) for c in context)), 2) + (AGENT_OVERHEAD if agent else PROMPT_OVERHEAD)
        cands, skipped = self.candidates(cfg, 0, max_tokens, only_provider, sig.vision, est_in)
        if not cands:
            why = "; ".join(f"{s['provider']}{'/' + s['model'] if s.get('model') else ''}: {s['why']}" for s in skipped[:5])
            raise AutoError("Auto could not find a model that can take this message" + (f" - {why}." if why else
                            ". Add and enable a model provider in Settings."))
        pref = cfg["preference"]
        want = "pro" if need_pro(sig, pref) else "flash"
        pick, ordered, tier_note = self._choose(cands, want, pref)
        reason = self._reason(sig, used, pick, want, pref, sum(c.tier == pick.tier for c in cands), tier_note)
        alternates, seen = [], {(pick.provider, pick.model)}
        for c in ordered:
            if (c.provider, c.model) not in seen:
                seen.add((c.provider, c.model))
                alternates.append(c.ref())
        return Route(pick.provider, pick.model, pick.tier, used, reason, sig.confidence, sig.public(),
                     {"input_per_m": pick.in_per_m, "output_per_m": pick.out_per_m, "known": pick.known}, alternates[:3], skipped,
                     engine, note, pref)

    @staticmethod
    def _choose(cands: list[Candidate], want: str, pref: str) -> tuple[Candidate, list[Candidate], str]:
        """The best candidate of the wanted tier (or the other tier when none is usable), and the rest in preference order."""
        def order(pool: list[Candidate], tier: str) -> list[Candidate]:
            if pref == "quality" and tier == "pro":
                return sorted(pool, key=lambda c: (not c.known, -c.blended, c.provider, c.model))
            return sorted(pool, key=lambda c: (not c.known, c.blended, c.provider, c.model))
        other = "flash" if want == "pro" else "pro"
        first = order([c for c in cands if c.tier == want], want)
        second = order([c for c in cands if c.tier == other], other)
        ordered = first + second
        note = "" if first else f"No {want}-tier model is usable right now (caps, credits or settings), so the {other} tier was used."
        return ordered[0], ordered, note

    @staticmethod
    def _reason(sig: Signals, engine: str, pick: Candidate, want: str, pref: str, n: int, tier_note: str) -> str:
        bits = [LEVELS[sig.complexity]] + [b for b, on in (("code", sig.code), ("deep reasoning", sig.reasoning), ("needs tools", sig.tools),
                                                          ("images", sig.vision)) if on]
        who = "built-in rules" if engine == "rules" else d1_spec.spec(engine).label
        tier = {"flash": "the fast tier", "pro": "the strong tier"}[pick.tier]
        how = "the priciest known" if pref == "quality" and pick.tier == "pro" else "the cheapest"
        price = f"${pick.in_per_m:.2f}/${pick.out_per_m:.2f} per M tokens" if pick.known else "price unknown"
        out = (f"{who} read this as {', '.join(bits)}: {tier}, {how} of {n} usable model{'s' if n != 1 else ''} "
               f"({pref} preference; {price}).")
        return (out + " " + tier_note) if tier_note else out


def public_config(cfg: dict) -> dict:
    return {**cfg, "engines": list(ENGINES), "preferences": list(PREFERENCES)}
