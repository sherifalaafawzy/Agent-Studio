"""An agent's Live profile: how it behaves when you talk to it in live mode.

{} (the default) = the agent has no Live profile: live mode uses the global Live settings and the agent only lends its instructions.
With a profile the agent decides: which engine (this PC, or Google Gemini Live), voice-only or avatar, the voice, the language, the avatar, how its tools are used
and a budget. The profile lives in the agent record, so it is saved with the agent, returned and accepted by `/api/agents`, and shown as a badge in the list.

Fields (all optional except `enabled`):
  enabled      true = this agent may be used in live mode with this profile; false = live mode refuses it ("Live is switched off for the agent '...'")
  engine       "local" | "gemini"
  mode         "voice" | "avatar"
  voice        a Kokoro voice (local) or one of Gemini's 30 prebuilt voices
  language     BCP-47 code (Gemini); local speech is English
  avatar_name  Gemini's prebuilt avatar (free text: Google documents no list in the pages Agent Studio could read)
  provider/model  the local pipeline's model (empty = the Live settings')
  agent_turns  local engine only: let the agent do a FULL turn (its tools, knowledge, memory) for every reply, then speak the answer. Slower: the answer is
               spoken after it is complete. Off = the model speaks the agent's persona directly (fast, no tools). Gemini always reaches the agent's tools
               through its `ask_agent` function.
  budget_usd   a per-session cap in dollars (warn at 80%, stop at 100%); 0 or empty = use the Live settings' limit
"""
from __future__ import annotations

from typing import Any

ENGINES = ("local", "gemini")
MODES = ("voice", "avatar")
KEYS = ("enabled", "engine", "mode", "voice", "language", "avatar_name", "provider", "model", "agent_turns", "budget_usd")
DEFAULT: dict[str, Any] = {"enabled": False, "engine": "local", "mode": "voice", "voice": "", "language": "", "avatar_name": "", "provider": "", "model": "",
                           "agent_turns": False, "budget_usd": 0.0}


def normalise(raw: Any) -> dict:
    """The checked profile for an API request; `None` / `{}` = no profile. Raises ValueError with a plain message."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("live must be an object such as {\"enabled\": true, \"engine\": \"gemini\", \"mode\": \"avatar\"}")
    for k in raw:
        if k not in KEYS:
            raise ValueError(f"unknown Live setting '{str(k)[:40]}': use {', '.join(KEYS)}")
    out = dict(DEFAULT)
    out.update(raw)
    for k in ("enabled", "agent_turns"):
        if not isinstance(out[k], bool):
            raise ValueError(f"live.{k} must be true or false")
    if out["engine"] not in ENGINES:
        raise ValueError(f"live.engine must be one of {', '.join(ENGINES)}")
    if out["mode"] not in MODES:
        raise ValueError(f"live.mode must be one of {', '.join(MODES)}")
    for k in ("voice", "language", "avatar_name", "provider", "model"):
        if not isinstance(out[k], str) or len(out[k]) > 120:
            raise ValueError(f"live.{k} must be text of at most 120 characters")
    b = out["budget_usd"]
    if isinstance(b, bool) or not isinstance(b, (int, float)) or not 0 <= b <= 1000:
        raise ValueError("live.budget_usd must be a number from 0 to 1000 (dollars; 0 = use the Live settings' limit)")
    out["budget_usd"] = float(b)
    if out["engine"] == "gemini":
        from .gemini_live import LANGUAGES, VOICES
        if out["voice"] and out["voice"] not in VOICES:
            raise ValueError(f"'{out['voice']}' is not one of Gemini Live's 30 prebuilt voices")
        if out["language"] and out["language"] not in LANGUAGES:
            raise ValueError(f"'{out['language']}' is not a language Gemini Live supports")
        if out["mode"] == "avatar" and not out["avatar_name"]:
            raise ValueError("choose an avatar name for an avatar agent (Google's documented example is Ben)")
    return out


def profile(raw: Any) -> dict:
    """The stored profile, tolerant: {} when there is none or it is unreadable."""
    try:
        return normalise(raw)
    except ValueError:
        return {}


def of(agent: Any) -> dict:
    return profile(getattr(agent, "live", None))


def badge(agent: Any) -> str | None:
    """A short label for the agents list, or None when the agent has no Live profile."""
    p = of(agent)
    if not p:
        return None
    if not p["enabled"]:
        return "Live off"
    return f"Live · {'Gemini' if p['engine'] == 'gemini' else 'local'} · {'avatar' if p['mode'] == 'avatar' else 'voice'}"


def apply(settings: dict, agent: Any) -> tuple[dict, dict]:
    """(the Live settings for a session with this agent, the profile). Profile fields override the global ones; an empty field keeps the global value."""
    p = of(agent)
    s = dict(settings)
    if not p or not p["enabled"]:
        return s, p
    s["live_engine"] = p["engine"]
    s["live_avatar"] = p["mode"] == "avatar"
    if p["engine"] == "gemini":
        if p["voice"]:
            s["live_gemini_voice"] = p["voice"]
        if p["language"]:
            s["live_gemini_language"] = p["language"]
        if p["avatar_name"]:
            s["live_gemini_avatar_name"] = p["avatar_name"]
    else:
        if p["voice"]:
            s["live_voice"] = p["voice"]
        if p["provider"]:
            s["live_provider"] = p["provider"]
            s["live_model"] = p["model"]
        elif p["model"]:
            s["live_model"] = p["model"]
    return s, p
