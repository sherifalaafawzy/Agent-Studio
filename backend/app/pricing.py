"""Token price lookup. USD per 1M tokens.

Order: in-app overrides -> LiteLLM's bundled cost map -> conservative fallback.
An unknown model is NEVER treated as free: it is priced at a deliberately high
fallback and flagged price_known=False so the guard errs on the side of refusing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import litellm

from .db import data_dir

# Conservative fallback for models we cannot price (roughly frontier-tier output price).
FALLBACK_IN_PER_M = 15.0
FALLBACK_OUT_PER_M = 75.0

# Built-in prices for the keyless mock provider. Deliberately EXPENSIVE so a cap
# demo trips after a couple of calls without any real spend.
BUILTIN: dict[str, tuple[float, float]] = {
    "mock/mock-cheap": (1.0, 2.0),
    "mock/mock-expensive": (1000.0, 2000.0),
}


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float
    known: bool
    source: str


def _overrides() -> dict:
    p = data_dir() / "pricing_overrides.json"
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:  # corrupt file must not silently zero prices
            return {}
    return {}


def price_for(kind: str, model: str, litellm_model: str) -> Price:
    key = f"{kind}/{model}"
    if kind == "ollama":
        return Price(0.0, 0.0, True, "local")
    ov = _overrides().get(key)
    if ov:
        return Price(float(ov["input_per_m"]), float(ov["output_per_m"]), True, "override")
    if key in BUILTIN:
        i, o = BUILTIN[key]
        return Price(i, o, True, "builtin")
    cm = litellm.model_cost
    for cand in (litellm_model, model, model.split("/")[-1]):
        e = cm.get(cand)
        if e and e.get("input_cost_per_token") is not None and e.get("output_cost_per_token") is not None:
            return Price(e["input_cost_per_token"] * 1e6, e["output_cost_per_token"] * 1e6, True, "litellm")
    return Price(FALLBACK_IN_PER_M, FALLBACK_OUT_PER_M, False, "fallback")


def cost_usd(p: Price, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * p.input_per_m + output_tokens * p.output_per_m) / 1e6


def known_models(kind: str) -> list[dict]:
    """Suggestions for the Settings UI, taken from LiteLLM's cost map."""
    out = []
    if kind == "mock":
        return [{"model": m.split("/", 1)[1], "input_per_m": a, "output_per_m": b} for m, (a, b) in BUILTIN.items()]
    prefix = {"vertex_ai": "vertex_ai/", "openai": "", "anthropic": "", "xai": "xai/", "groq": "groq/", "openrouter": "openrouter/"}.get(kind)
    if prefix is None:
        return out
    for k, v in litellm.model_cost.items():
        if v.get("mode") not in ("chat", None):
            continue
        if prefix and not k.startswith(prefix):
            continue
        if kind == "openai" and ("/" in k or not k.startswith(("gpt", "o1", "o3", "o4", "o5"))):
            continue
        if kind == "anthropic" and not k.startswith("claude"):
            continue
        if v.get("input_cost_per_token") is None or v.get("output_cost_per_token") is None:
            continue
        name = k[len(prefix):] if prefix else k
        out.append({"model": name, "input_per_m": v["input_cost_per_token"] * 1e6, "output_per_m": v["output_cost_per_token"] * 1e6})
    out.sort(key=lambda r: r["model"])
    return out[:300]
