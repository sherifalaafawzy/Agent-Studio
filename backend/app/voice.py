"""ElevenLabs voice: read answers aloud (text-to-speech) and dictate messages (speech-to-text).

The API key is stored locally (or read from ELEVENLABS_API_KEY) and never returned. ElevenLabs bills per character
on YOUR account - Agent Studio's model caps don't cover it - so every TTS request is counted here and refused
BEFORE it is sent if it would exceed your monthly character cap (Settings -> Voice).
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

import httpx

from .db import Database

API = "https://api.elevenlabs.io/v1"
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"         # fast + cheap; multilingual_v2 for best quality
DEFAULT_STT_MODEL = "scribe_v1"
MAX_TTS_CHARS = 5000


class VoiceError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def speakable(text: str) -> str:
    """Markdown answer -> something worth hearing (code is summarised, not read out)."""
    t = re.sub(r"```.*?```", " (code block omitted) ", text or "", flags=re.S)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|\*|_)(\S[^*_]*?)\1", r"\2", t)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"\|", " ", t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", t)).strip()


class Voice:
    def __init__(self, db: Database):
        self.db = db

    # ---------------------------------------------------------------- config
    def _key(self) -> str:
        k = self.db.get_setting("elevenlabs_api_key") or os.environ.get("ELEVENLABS_API_KEY") or ""
        if not k:
            raise VoiceError("ElevenLabs is not set up: add your API key in Settings -> Voice", 412)
        return k

    def _month(self) -> str:
        return time.strftime("%Y-%m", time.gmtime())

    def chars_used(self) -> int:
        raw = self.db.get_setting("voice_chars_month", "") or ""
        m, _, n = raw.partition(":")
        return int(n) if m == self._month() and n.isdigit() else 0

    def _add_chars(self, n: int) -> None:
        self.db.set_setting("voice_chars_month", f"{self._month()}:{self.chars_used() + n}")

    def cfg(self) -> dict:
        g = self.db.get_setting
        stored = g("elevenlabs_api_key") or ""
        env = bool(os.environ.get("ELEVENLABS_API_KEY"))
        return dict(configured=bool(stored or env), key_source="stored" if stored else ("env" if env else None),
                    key_hint=("…" + stored[-4:]) if len(stored) > 8 else None,
                    voice_id=g("elevenlabs_voice_id", "") or "", tts_model=g("elevenlabs_tts_model", DEFAULT_TTS_MODEL),
                    stt_model=g("elevenlabs_stt_model", DEFAULT_STT_MODEL), auto_read=g("voice_auto_read", "0") == "1",
                    dictation=g("voice_dictation", "1") == "1", char_cap=int(g("voice_char_cap", "0") or 0), chars_used_month=self.chars_used())

    def update(self, patch: dict) -> dict:
        s = self.db.set_setting
        if patch.get("api_key") is not None:            # "" clears; values are write-only
            s("elevenlabs_api_key", patch["api_key"].strip())
        for k, key in (("voice_id", "elevenlabs_voice_id"), ("tts_model", "elevenlabs_tts_model"), ("stt_model", "elevenlabs_stt_model")):
            if patch.get(k) is not None:
                s(key, str(patch[k]).strip())
        for k, key in (("auto_read", "voice_auto_read"), ("dictation", "voice_dictation")):
            if patch.get(k) is not None:
                s(key, "1" if patch[k] else "0")
        if patch.get("char_cap") is not None:
            if int(patch["char_cap"]) < 0:
                raise VoiceError("char_cap must be >= 0")
            s("voice_char_cap", str(int(patch["char_cap"])))
        return self.cfg()

    # ---------------------------------------------------------------- ElevenLabs calls
    async def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        async with httpx.AsyncClient(timeout=kw.pop("timeout", 60)) as c:
            r = await c.request(method, API + path, headers={"xi-api-key": self._key(), **kw.pop("headers", {})}, **kw)
        if r.status_code >= 400:
            try:
                d = r.json().get("detail")
                msg = d.get("message") if isinstance(d, dict) else d
            except Exception:  # noqa: BLE001
                msg = r.text[:200]
            raise VoiceError(f"ElevenLabs {r.status_code}: {msg}", 502 if r.status_code >= 500 else r.status_code)
        return r

    async def voices(self) -> list[dict]:
        j = (await self._req("GET", "/voices")).json()
        return [dict(voice_id=v["voice_id"], name=v.get("name"), category=v.get("category"), preview_url=v.get("preview_url"),
                     labels=v.get("labels") or {}) for v in j.get("voices", [])]

    async def models(self) -> list[dict]:
        j = (await self._req("GET", "/models")).json()
        return [dict(model_id=m["model_id"], name=m.get("name"), description=m.get("description"),
                     cost_factor=m.get("token_cost_factor")) for m in j if m.get("can_do_text_to_speech")]

    async def subscription(self) -> dict:
        j = (await self._req("GET", "/user/subscription")).json()
        return dict(tier=j.get("tier"), character_count=j.get("character_count"), character_limit=j.get("character_limit"),
                    next_reset_unix=j.get("next_character_count_reset_unix"))

    async def tts(self, text: str, voice_id: str | None = None) -> tuple[bytes, int]:
        say = speakable(text)[:MAX_TTS_CHARS]
        if not say:
            raise VoiceError("nothing to read aloud")
        c = self.cfg()
        voice = voice_id or c["voice_id"]
        if not voice:
            raise VoiceError("choose a voice in Settings -> Voice", 412)
        cap, used = c["char_cap"], c["chars_used_month"]
        if cap and used + len(say) > cap:                  # refused BEFORE anything is sent
            raise VoiceError(f"Monthly voice cap reached: {used:,} of {cap:,} characters used; this needs {len(say):,}. Raise it in Settings -> Voice.", 402)
        r = await self._req("POST", f"/text-to-speech/{voice}", params={"output_format": "mp3_44100_128"},
                            json={"text": say, "model_id": c["tts_model"]}, headers={"accept": "audio/mpeg"}, timeout=120)
        self._add_chars(len(say))
        return r.content, len(say)

    async def stt(self, audio: bytes, filename: str, content_type: str) -> dict:
        if not audio:
            raise VoiceError("empty recording")
        if len(audio) > 25 * 1024 * 1024:
            raise VoiceError("recording too large (25 MB max)")
        r = await self._req("POST", "/speech-to-text", data={"model_id": self.cfg()["stt_model"]},
                            files={"file": (filename or "speech.webm", audio, content_type or "audio/webm")}, timeout=180)
        j = r.json()
        return dict(text=(j.get("text") or "").strip(), language=j.get("language_code"))
