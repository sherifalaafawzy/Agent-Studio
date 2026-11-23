"""The Science safety guard as a guardrail kind, so any profile can apply it to ordinary chats too (the Science Lab itself always applies it)."""
from __future__ import annotations

from ..science import safety
from .base import Env, Field, Guard, Kind, OK, Verdict


class ScienceGuard(Guard):
    kind = "science"

    def __init__(self, level: str):
        self.level = level

    async def check(self, text: str, stage: str) -> Verdict:
        d = safety.screen(text, self.level)
        return OK if d.action == "allow" else Verdict(True, d.category, d.reason)


def _make(params: dict, secrets: dict, env: Env) -> Guard:
    level = params.get("level", "standard")
    if level not in safety.LEVELS:
        from .base import GuardError
        raise GuardError(f"level must be one of: {', '.join(safety.LEVELS)}")
    return ScienceGuard(level)


KIND = Kind(
    id="science", label="Science safety (chemistry and biology)", cost="free", leaves_pc=False, where="stays on this PC",
    summary="Flags requests for ways to make, obtain, scale up or enhance chemical-weapon agents, toxins, dangerous pathogens and explosives, and asks for confirmation on wet-lab procedures with biological hazards. A risk reducer, not a guarantee.",
    fields=(Field("level", "Level", "choice", default="standard", choices=safety.LEVELS, help="Strict also blocks wet-lab procedure requests."),),
    make=_make)
