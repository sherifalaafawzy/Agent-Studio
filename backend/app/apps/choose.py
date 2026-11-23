"""Which model the builder uses for an app: "best available" (the strongest model on the providers you have switched on and signed in to) or one you pick.

"Best" is a plain ranking, not a benchmark: providers whose top model is generally the strongest for writing code come first. A provider that is off, or
not signed in yet, is never chosen. Nothing here calls a model.
"""
from __future__ import annotations

RANK = ("anthropic", "openai", "vertex_ai", "gemini", "bedrock", "xai", "openrouter", "openai_compatible", "groq", "ollama", "mock")


def ready(p) -> bool:
    return bool(p.enabled and p.public().get("auth_ready"))


def option(p, tier_model) -> dict:
    return dict(provider=p.id, label=p.label, kind=p.kind, model=tier_model(p, "pro"), ready=ready(p))


def options(providers, tier_model) -> list[dict]:
    """Every provider that is switched on, best first."""
    on = [p for p in providers if p.enabled]
    return [option(p, tier_model) for p in sorted(on, key=lambda p: RANK.index(p.kind) if p.kind in RANK else len(RANK))]


def best(providers, tier_model) -> dict | None:
    """The first ready provider in the ranking, or None when nothing is ready."""
    return next((o for o in options(providers, tier_model) if o["ready"]), None)


def effective(saved: dict, providers, tier_model) -> dict:
    """What a chat should use now: the saved custom choice if it is still usable, otherwise the best one. `source` says which."""
    if saved.get("mode") == "custom":
        p = next((x for x in providers if x.id == saved.get("provider")), None)
        if p is not None and ready(p):
            return dict(provider=p.id, label=p.label, kind=p.kind, model=saved.get("model") or tier_model(p, "pro"), source="custom")
    b = best(providers, tier_model)
    if b is None:
        return dict(provider="", label="", kind="", model="", source="none")
    return dict(provider=b["provider"], label=b["label"], kind=b["kind"], model=b["model"], source="best" if saved.get("mode") != "custom" else "best (your choice is not available)")
