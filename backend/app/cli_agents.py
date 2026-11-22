"""Headless-CLI adapter: coding harnesses that have no usable ACP (Aider, OpenHands) driven through their
non-interactive CLI, exposed to Agent Studio as ordinary "external agents" (same call_external_agent tool).

What this layer guarantees (and what it does not):
  * cwd is the workspace; the task goes in through a temp file OUTSIDE the workspace (no argv length limits).
  * a hard timeout (owned by AcpManager._prompt), cancellation that kills the whole process tree, and a cap on
    how much output is kept (head + tail; the rest is counted, not stored).
  * these CLIs have no per-action permission protocol, so the run policy is mapped to ONE up-front decision:
    ask / reads_auto -> the user approves the whole unattended run first; readonly -> only if the harness has a
    real read-only mode (Aider --dry-run), otherwise the run is refused.
  * usage/cost is parsed from the output when the harness prints it; otherwise it is reported as unknown (None).
  * NOT sandboxed by us: the harness runs as your user (OpenHands on Windows runs inside WSL).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

IS_WINDOWS = os.name == "nt"
MAX_HEAD_CHARS = 120_000
MAX_TAIL_CHARS = 40_000
VERSION_TIMEOUT_S = 25
WSL_PROBE_TTL_S = 300
WSL_FORWARD = ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")      # env names OpenHands reads, passed into WSL via WSLENV
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class CliError(Exception):
    """Expected, user-actionable failure of a CLI harness run."""


# --------------------------------------------------------------------------- launching (native or via WSL)

def to_wsl_path(p: str | Path) -> str:
    """H:\\a\\b -> /mnt/h/a/b (what WSL sees)."""
    s = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/?(.*)$", s)
    if not m:
        raise CliError(f"cannot map '{p}' into WSL (only drive-letter paths are supported)")
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}".rstrip("/") or "/"


def _wsl_prefix(wsl: str, env: dict[str, str]) -> list[str]:
    return [wsl, *(["-d", env["WSL_DISTRO"]] if env.get("WSL_DISTRO") else [])]


_wsl_cache: dict[tuple[str, str], tuple[float, bool]] = {}


def _wsl_has(command: str, env: dict[str, str]) -> bool:
    """Is `command` on PATH of the WSL distro (login shell)? Cached: wsl.exe is slow to wake. Uses `wsl -e` (exec, no
    intermediate shell) so argument quoting is not re-interpreted."""
    key = (command, env.get("WSL_DISTRO", ""))
    hit = _wsl_cache.get(key)
    if hit and time.time() - hit[0] < WSL_PROBE_TTL_S:
        return hit[1]
    wsl = shutil.which("wsl")
    ok = False
    if wsl:
        with contextlib.suppress(Exception):
            r = subprocess.run([*_wsl_prefix(wsl, env), "-e", "bash", "-lc", 'command -v "$0"', command],
                               capture_output=True, timeout=VERSION_TIMEOUT_S)
            ok = r.returncode == 0
    _wsl_cache[key] = (time.time(), ok)
    return ok


def is_installed(command: str, env: dict[str, str] | None = None) -> bool:
    env = env or {}
    if shutil.which(command) or Path(command).is_file():
        return True
    return IS_WINDOWS and shutil.which("wsl") is not None and _wsl_has(command, env)


def launcher(command: str, workspace: Path, env: dict[str, str]) -> tuple[list[str], dict[str, str], bool]:
    """(argv prefix, child env, via_wsl). Native executable if there is one; on Windows else the WSL distro."""
    exe = shutil.which(command) or (command if Path(command).is_file() else None)
    if exe:
        return [exe], env, False
    wsl = shutil.which("wsl") if IS_WINDOWS else None
    if not wsl or not _wsl_has(command, env):
        raise CliError(f"'{command}' was not found on PATH" + (" or inside WSL" if wsl else ""))
    env = dict(env)
    env["WSLENV"] = ":".join(x for x in [env.get("WSLENV", ""), *[k for k in WSL_FORWARD if k in env]] if x)
    return [*_wsl_prefix(wsl, env), "--cd", str(workspace), "-e", "bash", "-lc", 'exec "$@"', "bash", command], env, True


def kill_tree(proc: Any) -> None:
    """Kill the process and its children (npx/cmd/wsl wrappers leave grandchildren)."""
    pid = getattr(proc, "pid", None)
    if not pid or proc.returncode is not None:
        return
    if IS_WINDOWS:
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


async def version_of(command: str, args: list[str], workspace: Path, env: dict[str, str]) -> str:
    """`<command> <args>` (e.g. --version): a free health check, never starts a model call."""
    prefix, env, _ = launcher(command, workspace, env)
    try:
        proc = await asyncio.create_subprocess_exec(*prefix, *args, cwd=str(workspace), env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as e:
        raise CliError(f"could not start '{command}': {e}")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), VERSION_TIMEOUT_S)
    except asyncio.TimeoutError:
        kill_tree(proc)
        raise CliError(f"'{command} {' '.join(args)}' did not answer within {VERSION_TIMEOUT_S}s")
    text = ANSI_RE.sub("", out.decode("utf-8", "replace")).replace("\0", "").strip()
    if proc.returncode != 0:
        raise CliError(f"'{command} {' '.join(args)}' exited with code {proc.returncode}: {text[-300:]}")
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not line:
        raise CliError(f"'{command} {' '.join(args)}' printed nothing")
    return line[:200]


# --------------------------------------------------------------------------- bounded output

class Capped:
    """Keeps the first MAX_HEAD_CHARS and last MAX_TAIL_CHARS of the output; counts what was dropped."""

    def __init__(self) -> None:
        self.head: list[str] = []
        self.head_len = 0
        self.tail: deque[str] = deque()
        self.tail_len = 0
        self.dropped = 0

    def add(self, s: str) -> None:
        if self.head_len < MAX_HEAD_CHARS:
            take = s[:MAX_HEAD_CHARS - self.head_len]
            self.head.append(take)
            self.head_len += len(take)
            s = s[len(take):]
        if not s:
            return
        self.tail.append(s)
        self.tail_len += len(s)
        while self.tail_len > MAX_TAIL_CHARS:
            gone = self.tail.popleft()
            over = self.tail_len - MAX_TAIL_CHARS
            if over < len(gone):             # partially trim the oldest chunk
                self.tail.appendleft(gone[over:])
                self.tail_len -= over
                self.dropped += over
            else:
                self.tail_len -= len(gone)
                self.dropped += len(gone)

    def text(self) -> str:
        mid = f"\n…[{self.dropped} characters of output omitted]…\n" if self.dropped else ""
        return "".join(self.head) + mid + "".join(self.tail)


# --------------------------------------------------------------------------- output parsers

def _num(v: str, unit: str | None) -> int:
    n = float(v.replace(",", ""))
    return int(n * {"k": 1_000, "M": 1_000_000}.get(unit or "", 1))


class AiderParser:
    """Plain-text output. Usage line:  'Tokens: 4.1k sent, 312 received. Cost: $0.02 message, $0.03 session.'"""
    SENT = re.compile(r"([\d.,]+)\s*([kM])? sent")
    RECV = re.compile(r"([\d.,]+)\s*([kM])? received")
    COST = re.compile(r"Cost: \$([\d.]+) message, \$([\d.]+) session")
    EDIT = re.compile(r"^Applied edit to (.+?)\s*$")

    def __init__(self) -> None:
        self.sent = self.recv = 0
        self.seen_usage = False
        self.session_cost: float | None = None
        self.out = Capped()

    @property
    def dropped(self) -> int:
        return self.out.dropped

    def feed(self, line: str, emit: Callable[[dict], None]) -> None:
        self.out.add(line + "\n")
        emit({"type": "text", "text": line + "\n"})
        if (m := self.EDIT.match(line)):
            emit({"type": "fs_write", "path": m.group(1), "bytes": 0})
        if "sent" in line and "received" in line:
            s, r = self.SENT.search(line), self.RECV.search(line)
            if s and r:
                self.sent += _num(*s.groups())
                self.recv += _num(*r.groups())
                self.seen_usage = True
        if (c := self.COST.search(line)):
            self.session_cost = float(c.group(2))

    def finish(self) -> tuple[str, dict | None, float | None]:
        usage = dict(input_tokens=self.sent, output_tokens=self.recv, total_tokens=self.sent + self.recv) if self.seen_usage else None
        return self.out.text().strip(), usage, self.session_cost


class JsonlParser:
    """OpenHands `--json`: one JSON event per line. The event schema is not documented stably, so this is deliberately
    tolerant: non-JSON lines are kept as plain text, the last agent message becomes the report, and token/cost
    counters are picked up wherever they appear (`accumulated_cost`, `prompt_tokens`, `completion_tokens`)."""

    def __init__(self) -> None:
        self.raw = Capped()
        self.final: str | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.cost: float | None = None

    @property
    def dropped(self) -> int:
        return self.raw.dropped

    @staticmethod
    def _text_of(v: Any) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return "\n".join(t for t in (JsonlParser._text_of(x) for x in v) if t)
        if isinstance(v, dict):
            for k in ("text", "content", "message", "llm_message"):
                if k in v:
                    return JsonlParser._text_of(v[k])
        return ""

    def _scan(self, o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "accumulated_cost" and isinstance(v, (int, float)):
                    self.cost = float(v)
                elif k == "prompt_tokens" and isinstance(v, int):
                    self.prompt_tokens = v
                elif k == "completion_tokens" and isinstance(v, int):
                    self.completion_tokens = v
                else:
                    self._scan(v)
        elif isinstance(o, list):
            for x in o:
                self._scan(x)

    def feed(self, line: str, emit: Callable[[dict], None]) -> None:
        try:
            ev = json.loads(line)
        except ValueError:
            ev = None
        if not isinstance(ev, dict):
            self.raw.add(line + "\n")
            emit({"type": "text", "text": line + "\n"})
            return
        self.raw.add(line + "\n")
        self._scan(ev)
        tool = ev.get("tool_name")
        if tool:
            emit({"type": "tool_call", "id": str(ev.get("id") or ev.get("tool_call_id") or tool), "title": str(tool), "kind": "other",
                  "status": "completed", "input": json.dumps(ev.get("action"), default=str)[:1500] if ev.get("action") else None,
                  "locations": []})
        elif ev.get("source") == "agent" and (t := self._text_of(ev.get("llm_message") or ev.get("message") or ev.get("content"))):
            self.final = t
            emit({"type": "text", "text": t + "\n"})

    def finish(self) -> tuple[str, dict | None, float | None]:
        usage = None
        if self.prompt_tokens is not None or self.completion_tokens is not None:
            i, o = self.prompt_tokens or 0, self.completion_tokens or 0
            usage = dict(input_tokens=i, output_tokens=o, total_tokens=i + o)
        return (self.final or self.raw.text()).strip(), usage, self.cost


# --------------------------------------------------------------------------- per-harness specs

@dataclass
class CliSpec:
    parser: Callable[[], Any]
    task_flag: str                      # how the task file is passed
    readonly_args: list[str] | None     # extra args for policy 'readonly'; None = the harness has no read-only mode
    unattended: str                     # what the user is told they approve
    extra: Callable[[Path], list[str]] = field(default=lambda ws: [])


def _aider_extra(ws: Path) -> list[str]:
    for d in (ws.resolve(), *ws.resolve().parents):       # outside a git repo Aider would `git init` one: don't let it
        if (d / ".git").exists():
            return []
    return ["--no-git"]


SPECS: dict[str, CliSpec] = {
    "aider": CliSpec(AiderParser, "--message-file", ["--dry-run"],
                     "Aider will edit files in the workspace without asking again (it only edits the files it is given or creates).", _aider_extra),
    "openhands": CliSpec(JsonlParser, "-f", None,
                         "OpenHands headless mode auto-approves EVERY action it takes, including shell commands, for the whole run.", lambda ws: []),
}


@dataclass
class CliResult:
    text: str
    stop_reason: str
    usage: dict | None
    cost: float | None
    truncated: int


async def run_cli(agent_id: str, cfg: dict, env: dict[str, str], ws: Path, task: str, policy: str,
                  emit: Callable[[dict], None], sess: Any) -> CliResult:
    """One turn = one process. `sess` only carries `.proc` and `.cancel_requested` so AcpManager can cancel us."""
    spec = SPECS[agent_id]
    if policy == "readonly" and spec.readonly_args is None:
        raise CliError(f"{cfg['label']} has no read-only mode (it auto-approves everything), so the 'readonly' policy cannot be honoured. "
                       "Use another agent for read-only work, or choose a different policy.")
    prefix, child_env, via_wsl = launcher(cfg["command"], ws, {**os.environ, **env, "NO_COLOR": "1", "PYTHONUTF8": "1", "TERM": "dumb"})
    tmp = Path(tempfile.mkdtemp(prefix="agent-studio-cli-"))
    parser = spec.parser()
    err: deque[str] = deque(maxlen=30)
    try:
        taskfile = tmp / "task.txt"
        taskfile.write_text(task, encoding="utf-8")
        argv = [*prefix, *cfg["args"], *spec.extra(ws), *(spec.readonly_args if policy == "readonly" and spec.readonly_args else []),
                spec.task_flag, to_wsl_path(taskfile) if via_wsl else str(taskfile)]
        sess.cancel_requested = False
        try:
            sess.proc = proc = await asyncio.create_subprocess_exec(*argv, cwd=str(ws), env=child_env, stdin=asyncio.subprocess.DEVNULL,
                                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise CliError(f"could not start {cfg['label']}: {e}")

        async def drain_err() -> None:
            while (b := await proc.stderr.read(4096)):
                for ln in ANSI_RE.sub("", b.decode("utf-8", "replace")).replace("\0", "").splitlines():
                    if ln.strip():
                        err.append(ln.strip()[:300])

        etask = asyncio.create_task(drain_err())
        try:
            buf = ""
            while (b := await proc.stdout.read(4096)):
                buf += ANSI_RE.sub("", b.decode("utf-8", "replace")).replace("\0", "")
                *lines, buf = buf.split("\n")
                for ln in lines:
                    parser.feed(ln.rstrip("\r"), emit)
                if len(buf) > MAX_HEAD_CHARS:            # a never-ending line: flush it as a line of its own
                    parser.feed(buf, emit)
                    buf = ""
            if buf.strip():
                parser.feed(buf.rstrip("\r"), emit)
            await proc.wait()
            await etask
        except BaseException:                               # timeout/cancel from the caller: never leave the process running
            kill_tree(proc)
            etask.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(proc.wait(), 5)
            raise
        text, usage, cost = parser.finish()
        if sess.cancel_requested:
            return CliResult(text, "cancelled", usage, cost, parser.dropped)
        if proc.returncode != 0:
            raise CliError(f"{cfg['label']} exited with code {proc.returncode}. " + ("stderr: " + " | ".join(list(err)[-6:]) if err else "(no stderr)"))
        return CliResult(text, "end_turn", usage, cost, parser.dropped)
    finally:
        sess.proc = None
        shutil.rmtree(tmp, ignore_errors=True)
