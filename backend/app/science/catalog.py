"""Optional science components: what they are, what they need, and a safe install into their own isolated environment.

* Nothing here is installed unless you press Install after reading the plan (licence, size, hardware, keys, where data goes, cost) and tick the licence box.
* Each component gets its OWN virtual environment under `<data>/science/envs/<id>/`, never the backend's. Packages are pinned to exact versions and pip runs with the
  14-day rule (`--uploaded-prior-to`), ready-made wheels only (see pip_install.py).
* A component whose licence has not been confirmed from the source cannot be installed; one that needs something we have not built (conda, a data download, a GPU) is
  listed with the reason instead of a button. Hosted services cannot be installed at all: they are listed as information.
* Claude Science and Google's co-scientist are deliberately NOT part of this catalogue: Agent Studio does not connect to, drive or install them.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from datetime import date
from pathlib import Path

from .. import pip_install, ts_spec
from ..db import data_dir
from ..d1_setup import run_streaming

STATE_KEY = "science_components"
CHECKED = "2026-11-23"


def _c(id, name, kind, summary, **kw) -> dict:
    base = dict(id=id, name=name, kind=kind, summary=summary, installable=False, blocked="", package="", version="", module="", python_min="", licence="", licence_verified=False,
                licence_source="", hardware="", disk="", keys="", egress="", cost="", caution="", link="")
    return {**base, **kw}


COMPONENTS: dict[str, dict] = {c["id"]: c for c in (
    _c("paperqa", "PaperQA2", "self-hostable", "Answers questions from a set of papers with cited passages (FutureHouse, open source).", installable=True, package="paper-qa", version="2026.8.12", module="paperqa",
       python_min="3.11", licence="Apache-2.0", licence_verified=True, licence_source=f"PyPI metadata, checked {CHECKED}", hardware="CPU only. Needs a language model and an embedding model (a cloud key or local Ollama).",
       disk="about 1 GB with its dependencies (estimate)", keys="A model provider key, unless you point it at a local Ollama.", egress="Text of your papers and questions goes to the model provider you give it (nothing if it is local).",
       cost="Free software; the model provider may charge.", caution="Small local models (around 7B) answer poorly. Treat its answers as leads and check the cited passages.", link="https://github.com/Future-House/paper-qa"),
    _c("tooluniverse", "ToolUniverse", "self-hostable", "Hundreds of scientific tools as an MCP server (Harvard, open source).", installable=True, package="tooluniverse", version="1.5.9", module="tooluniverse",
       python_min="3.10", licence="Apache-2.0", licence_verified=True, licence_source=f"GitHub repository licence (mims-harvard/ToolUniverse), checked {CHECKED}", hardware="CPU only.", disk="about 1 GB (estimate)",
       keys="Many of its tools need their own free API keys.", egress="Each tool calls its own public service.", cost="Free software; some services have limits.",
       caution="It is a large tool collection and includes tools beyond what this lab offers by default. Review which ones you enable; the lab's safety screen does NOT cover tools you run through it.", link="https://github.com/mims-harvard/ToolUniverse"),
    _c("biomni", "Biomni", "self-hostable", "A general biomedical agent (Stanford, open source).", python_min="3.11", licence="Apache-2.0", licence_verified=True, licence_source=f"PyPI metadata, checked {CHECKED}",
       hardware="CPU. An optional local model needs a GPU.", disk="about 11 GB data download on first run (reported by the project; not verified here)", keys="Anthropic key mainly; others optional.",
       egress="Your questions go to the model provider.", cost="Free software; model costs apply.", blocked="Not installable yet: it expects a conda environment and a large data download, and it runs code a model writes. It needs a container recipe that runs it only inside the sandbox, which is not built.",
       caution="Runs code written by a language model. Never run it outside a sandbox.", link="https://github.com/snap-stanford/Biomni"),
    _c("chemcrow", "ChemCrow", "self-hostable", "A chemistry tool agent.", python_min="3.9", licence="MIT", licence_verified=True, licence_source=f"PyPI metadata, checked {CHECKED}", hardware="CPU; needs an OpenAI key.",
       keys="An OpenAI key.", egress="Questions go to OpenAI.", blocked="Excluded on purpose: it contains reaction and synthesis tools, and the lab does not offer retrosynthesis or reaction prediction. Its safe parts (name lookups, formula masses) are already offered natively, through PubChem and the formula tool.",
       caution="It also requires Python below 3.12.", link="https://pypi.org/project/chemcrow/"),
    _c("ai-scientist-v2", "AI Scientist v2", "self-hostable", "Writes research papers autonomously (Sakana AI).", python_min="3.11", licence="Custom licence (not confirmed)", licence_verified=False,
       hardware="Linux with an NVIDIA GPU (CUDA).", disk="large", keys="Model provider keys.", egress="Prompts go to model providers.", blocked="Not offered: its licence has not been confirmed from the project's own LICENSE file, and it needs a GPU server. It also runs code a model writes without supervision.",
       caution="Runs LLM-written code autonomously. Off by default.", link="https://github.com/SakanaAI/AI-Scientist-v2"),
    _c("edison", "Edison Scientific / FutureHouse platform", "hosted", "Hosted research agents with a web and Python API (credits).", hardware="Hosted by the vendor.", keys="An account and API key.",
       egress="Questions go to the vendor.", cost="Credit based (per the vendor's pricing page).", blocked="Hosted service: it cannot be installed. A connector is not built because its API terms have not been confirmed.",
       link="https://www.edisonscientific.com"),
)}


def plan(comp: dict, python_version: str | None = None) -> dict:
    """What installing a component involves, in plain words, plus whether it can be installed right now."""
    py = python_version or sys.version.split()[0]
    reasons = []
    if comp["blocked"]:
        reasons.append(comp["blocked"])
    if comp["kind"] == "hosted":
        reasons.append("Hosted services are connected to, not installed.")
    if comp["python_min"] and _ver(py) < _ver(comp["python_min"]):
        reasons.append(f"It needs Python {comp['python_min']} or newer; Agent Studio is using Python {py}. Set AGENT_STUDIO_ENV_PYTHON to a newer python.exe.")
    if not comp["licence_verified"]:
        reasons.append("Its licence has not been confirmed.")
    return {**comp, "can_install": comp["installable"] and not reasons, "reasons": reasons, "python": py,
            "steps": ["Create an isolated environment", f"Install {comp['package']}=={comp['version']} (ready-made packages only, nothing newer than 14 days)", "Check that it imports"] if comp["installable"] else []}


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class ComponentInstaller:
    def __init__(self, db, home: Path | None = None, runner=None, today=date.today):
        self.db, self.runner, self.today = db, runner or run_streaming, today
        self._home = home
        self.thread: threading.Thread | None = None
        self.installing = ""
        self.log: list[str] = []

    def home(self) -> Path:
        h = self._home or (data_dir() / "science" / "envs")
        h.mkdir(parents=True, exist_ok=True)
        return h

    def _state(self) -> dict:
        try:
            s = json.loads(self.db.get_setting(STATE_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return s if isinstance(s, dict) else {}

    def _record(self, cid: str, **rec) -> None:
        s = self._state()
        s[cid] = rec
        self.db.set_setting(STATE_KEY, json.dumps(s))

    def _py(self, cid: str) -> Path:
        v = self.home() / cid / "venv"
        return v / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def list(self) -> list[dict]:
        st = self._state()
        return [{**plan(c), "state": (st.get(cid) or {}).get("state", "not_installed"), "installed_version": (st.get(cid) or {}).get("version", ""), "error": (st.get(cid) or {}).get("error", ""),
                 "installing": self.installing == cid, "command": self._command(cid) if (st.get(cid) or {}).get("state") == "ready" else ""} for cid, c in COMPONENTS.items()]

    def _command(self, cid: str) -> str:
        return {"tooluniverse": f'"{self._py(cid)}" -m tooluniverse.smcp_server', "paperqa": f'"{self._py(cid)}" -m paperqa'}.get(cid, "")

    def status(self) -> dict:
        return {"installing": self.installing, "log": self.log[-30:], "components": self.list()}

    def install(self, cid: str, licence_ack: bool) -> dict:
        c = COMPONENTS.get(cid)
        if c is None:
            raise ValueError(f"unknown component '{cid}'")
        p = plan(c)
        if not p["can_install"]:
            raise ValueError("it cannot be installed: " + " ".join(p["reasons"]))
        if licence_ack is not True:
            raise ValueError(f"tick the box to accept the {c['licence']} licence first")
        if self.thread is not None and self.thread.is_alive():
            raise ValueError(f"{self.installing or 'another component'} is being installed: wait for it to finish")
        self.installing, self.log = cid, []
        self._record(cid, state="installing", licence=c["licence"], licence_ack_at=time.time())
        self.thread = threading.Thread(target=self._run, args=(cid, licence_ack), name="science-install", daemon=True)
        self.thread.start()
        return self.status()

    def wait(self, timeout: float = 30.0) -> None:
        if self.thread is not None:
            self.thread.join(timeout)

    def _env(self) -> dict:
        import os
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
        env.update(PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PYTHONUTF8="1")
        return env

    def _run(self, cid: str, ack: bool) -> None:
        c = COMPONENTS[cid]
        py, cutoff = str(self._py(cid)), ts_spec.cutoff(self.today())
        steps = [("Create the isolated environment", [pip_install.base_python(), "-m", "venv", "--clear", str(self.home() / cid / "venv")]),
                 ("Update pip", [py, "-m", "pip", "install", "--no-input", f"pip=={ts_spec.PIP_VERSION}"]),
                 (f"Install {c['name']}", pip_install.install_cmd(py, cutoff, [f"{c['package']}=={c['version']}"])),
                 ("Check that it imports", [py, "-c", f"import importlib.metadata as m, {c['module']}; print('VERSION', m.version('{c['package']}'))"])]
        captured: list[str] = []
        try:
            for label, cmd in steps:
                self.log.append(f"== {label}")
                captured = []
                rc = self.runner(cmd, self._env(), lambda ln: (captured.append(ln), self.log.append(ln[:300])))
                if rc != 0:
                    raise RuntimeError(pip_install.explain_failure(label, rc, captured, pip_install.python_label()))
            m = next((re.match(r"VERSION (\S+)", x) for x in reversed(captured) if x.startswith("VERSION ")), None)
            if m is None:
                raise RuntimeError("it installed but its version could not be read")
            self._record(cid, state="ready", version=m.group(1), installed_at=time.time(), cutoff=cutoff, licence=c["licence"], licence_ack=ack)
        except Exception as e:  # noqa: BLE001 - shown to the user; they can retry
            self._record(cid, state="failed", error=str(e)[:700])
        finally:
            self.installing = ""

    def uninstall(self, cid: str) -> dict:
        if cid not in COMPONENTS:
            raise ValueError(f"unknown component '{cid}'")
        if self.installing == cid:
            raise ValueError("it is being installed right now")
        shutil.rmtree(self.home() / cid, ignore_errors=True)
        s = self._state()
        s.pop(cid, None)
        self.db.set_setting(STATE_KEY, json.dumps(s))
        return self.status()
