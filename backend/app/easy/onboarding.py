"""First-run onboarding: where the user is, what they chose, and a live checklist computed from the real state of the system."""
from __future__ import annotations

import json
import time

from ..db import Database

STEPS = ["welcome", "power", "ollama", "model", "key", "workspace", "agents", "assistant", "first", "done"]
STATUSES = ("new", "in_progress", "done", "skipped", "existing")
CHOICES = ("local", "online", "both")
KEY = "onboarding"


class Onboarding:
    def __init__(self, db: Database):
        self.db = db

    def get(self) -> dict:
        raw = self.db.get_setting(KEY)
        if raw:
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    return d
            except ValueError:
                pass
        # Nothing saved. Someone who already chats here is not a first-time user: do not interrupt them.
        used = self.db.one("SELECT 1 FROM chat_sessions LIMIT 1") is not None
        return dict(status="existing" if used else "new", step="welcome", choice="", completed=[], skipped=[], updated=None)

    def update(self, patch: dict) -> dict:
        cur = self.get()
        if "status" in patch:
            if patch["status"] not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            cur["status"] = patch["status"]
        if "step" in patch:
            if patch["step"] not in STEPS:
                raise ValueError(f"unknown step '{patch['step']}'")
            cur["step"] = patch["step"]
            if cur["status"] in ("new", "existing"):
                cur["status"] = "in_progress"
        if "choice" in patch:
            if patch["choice"] not in CHOICES + ("",):
                raise ValueError("choice must be local, online or both")
            cur["choice"] = patch["choice"]
        for field, key in (("completed", "completed"), ("skipped", "skipped")):
            for s in patch.get(field) or []:
                if s not in STEPS:
                    raise ValueError(f"unknown step '{s}'")
                if s not in cur[key]:
                    cur[key].append(s)
        cur["updated"] = time.time()
        self.db.set_setting(KEY, json.dumps(cur))
        return cur

    def reset(self) -> dict:
        self.db.set_setting(KEY, json.dumps(dict(status="in_progress", step="welcome", choice="", completed=[], skipped=[], updated=time.time())))
        return self.get()


def checklist(*, providers: list[dict], ollama: dict, installed_models: int, workspace: str, acp_installed: int, assistant_ready: bool,
              chats: int, custom_agents: int) -> list[dict]:
    """Each step with a live state: done (already true), todo, or optional. `detail` says what was found."""
    cloud = [p for p in providers if p["enabled"] and p["kind"] not in ("ollama", "mock") and p.get("auth_ready")]
    local_on = any(p["enabled"] and p["kind"] == "ollama" for p in providers)
    rows = [
        ("welcome", "Welcome", "done", "Read the short intro."),
        ("power", "How agents are powered", "done" if (cloud or (local_on and installed_models)) else "todo",
         "Agents think with an AI model: one running on this PC (free), or one from an online provider (paid per use), or both."),
        ("ollama", "Local models (Ollama)", "done" if ollama.get("running") else "todo",
         "running, version " + str(ollama.get("version")) if ollama.get("running") else ("installed but not running" if ollama.get("installed") else "not installed")),
        ("model", "A model to use", "done" if (installed_models and local_on) or cloud else "todo",
         f"{installed_models} local model(s) installed" + (", provider on" if local_on else ", provider off") if installed_models else "no local model yet"),
        ("key", "An online provider key", "done" if cloud else "optional",
         f"{len(cloud)} provider(s) ready: " + ", ".join(p["label"] for p in cloud) if cloud else "none yet (not needed if you use a local model)"),
        ("workspace", "Your work folder", "done" if workspace else "todo", workspace or "not chosen"),
        ("agents", "Coding agents", "done" if acp_installed else "optional", f"{acp_installed} installed" if acp_installed else "none installed (optional)"),
        ("assistant", "Side assistant", "done" if assistant_ready else "optional", "ready" if assistant_ready else "needs a model first"),
        ("first", "Your first chat or agent", "done" if (chats or custom_agents) else "todo", f"{chats} chat(s), {custom_agents} custom agent(s)"),
        ("done", "Finished", "done", ""),
    ]
    return [dict(id=i, label=l, state=s, detail=d) for i, l, s, d in rows]
