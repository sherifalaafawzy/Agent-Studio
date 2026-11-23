"""Guardrails: the shapes every guard shares. A guard looks at one piece of text (what the user typed, what a tool returned,
what a model answered, the arguments of a tool call) and says whether it is acceptable. It never edits the text itself."""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

STAGES = ("input", "output", "tool")           # what the user (or a tool result) sends the model / what the model answers / what a tool is asked to do
MODES = ("off", "monitor", "block")


class GuardError(Exception):
    """The guard could not give a verdict (not installed, unreachable, bad credentials, an answer it cannot read). Safe to show."""


class GuardPreparing(GuardError):
    """The guard is not ready YET: a safety model is downloading or the decision model is loading. This is not a failure. Until it is
    ready the check is skipped, or blocked when the profile stops on "cannot answer". `notice` is the short line shown to people."""

    def __init__(self, notice: str, what: str, state: dict | None = None):
        super().__init__(notice)
        self.notice, self.what, self.state = notice, what, state or {}         # what: "downloading" | "loading"


class GuardNotInstalled(GuardError):
    """The guard needs something that was never installed. `install` names where to install it (the UI links there)."""

    def __init__(self, message: str, install: str):
        super().__init__(message)
        self.install = install


# True while a person is pressing a Test / Try it button: a guard may then wait for a model to load instead of skipping the check.
WAIT_FOR_LOAD: contextvars.ContextVar[bool] = contextvars.ContextVar("guard_wait_for_load", default=False)


@dataclass
class Verdict:
    flagged: bool
    category: str = ""                 # short machine-friendly label: pii, secret, injection, S1, harm, ...
    reason: str = ""                   # one plain sentence
    score: float | None = None
    guard: str = ""                    # filled in by the service: which profile said so

    def public(self) -> dict:
        return {"flagged": self.flagged, "category": self.category, "reason": self.reason, "score": self.score, "guard": self.guard}


OK = Verdict(False)


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    type: str = "text"                 # text | secret | bool | choice | number | textarea
    required: bool = False
    default: Any = ""
    placeholder: str = ""
    choices: tuple = ()
    help: str = ""


@dataclass
class Env:
    """What a guard may use from the outside world. Tests replace every member with a fake."""
    http: Callable[[], Any] | None = None                                  # () -> httpx.AsyncClient
    aws_clients: Callable[..., Callable[[str], Any]] | None = None         # aws_clients(region, ...) -> (service -> boto3 client)
    google_token: Callable[[str], Callable[[], str]] | None = None         # google_token(service_account_json) -> () -> token
    decide: Callable[[str, str, dict, float], Awaitable[dict]] | None = None   # d1: (model, state, questions, timeout) -> reply
    pulls: Any = None                                                      # GuardPuller: downloads a missing Ollama model in the background (None = no auto-pull)


class Guard:
    """One configured guard. `check` returns a Verdict or raises GuardError."""
    kind = ""

    async def check(self, text: str, stage: str) -> Verdict:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    summary: str
    where: str                                   # where the text goes: "stays on this PC" / "your own model server" / "leaves this PC (cloud)"
    leaves_pc: bool
    fields: tuple[Field, ...]
    secrets: tuple[str, ...] = ()                # names of fields kept write-only
    make: Callable[[dict, dict, Env], Guard] | None = None
    cost: str = "free"                           # free | model | cloud


@dataclass
class Profile:
    """A named guard setup: which guard, how it is configured, where it looks, and what it does on a hit."""
    id: str
    name: str
    kind: str
    params: dict = field(default_factory=dict)
    stages: dict = field(default_factory=lambda: {"input": True, "output": True, "tool": True})
    mode: str = "monitor"                        # off | monitor (record only) | block
    fail: str = "open"                           # open: if the guard cannot answer, carry on | closed: treat it as a block
    default: bool = False                        # applies to agents that did not pick profiles of their own
