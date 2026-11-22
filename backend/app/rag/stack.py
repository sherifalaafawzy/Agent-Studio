"""Vector stores that run in Docker on this PC. Data lives in named Docker volumes (never bind mounts: they break on Windows).

Each stack is plain data (image, ports, volume), so another store is one entry in STACKS. Containers listen on
127.0.0.1 only. Images are pinned to a release that has been public for more than two weeks.
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..db import data_dir


class StackError(Exception):
    """Docker is unavailable or refused. The message is safe to show to the user."""


@dataclass(frozen=True)
class Stack:
    id: str
    label: str
    image: str
    container: str
    volume: str
    mount: str                        # where the volume goes inside the container
    port: int                         # published on 127.0.0.1
    container_port: int
    store_kind: str                   # the rag store kind this container serves
    config: dict = field(default_factory=dict)     # non-secret connection settings to register for it
    env: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)    # fixed connection secrets for the container (not shown in the UI)
    gen_env: dict = field(default_factory=dict)    # {container env var: secret name}: a random value made on first start, kept in the data dir
    gen_store: dict = field(default_factory=dict)  # {store secret field: secret name}: the same value, handed to the store we register
    cmd: tuple[str, ...] = ()
    notes: str = ""


STACKS: dict[str, Stack] = {
    "pgvector": Stack(
        id="pgvector", label="PostgreSQL + pgvector (Docker)", image="pgvector/pgvector:0.8.6-pg17", container="agent-studio-pgvector",
        volume="agent-studio-pgvector", mount="/var/lib/postgresql/data", port=5433, container_port=5432, store_kind="pgvector",
        config={"host": "127.0.0.1", "port": "5433", "database": "agentstudio", "user": "agentstudio", "sslmode": "disable"},
        env={"POSTGRES_USER": "agentstudio", "POSTGRES_DB": "agentstudio"},
        gen_env={"POSTGRES_PASSWORD": "password"}, gen_store={"password": "password"},
        notes="PostgreSQL 17 with pgvector 0.8.6 on port 5433 (so it does not clash with a Postgres on 5432). The password is "
              "generated on first start and kept in Agent Studio's data folder; the first start takes about 10 seconds to accept connections."),
    "chroma": Stack(
        id="chroma", label="Chroma (Docker)", image="chromadb/chroma:1.5.9", container="agent-studio-chroma",
        volume="agent-studio-chroma", mount="/data", port=8008, container_port=8000, store_kind="chroma",
        config={"url": "http://127.0.0.1:8008"}, env={"IS_PERSISTENT": "TRUE", "ANONYMIZED_TELEMETRY": "FALSE"},
        notes="Chroma 1.5.9 on port 8008. The server has no login of its own: it is only reachable from this PC (127.0.0.1)."),
    "qdrant": Stack(
        id="qdrant", label="Qdrant (Docker)", image="qdrant/qdrant:v1.20.0", container="agent-studio-qdrant",
        volume="agent-studio-qdrant", mount="/qdrant/storage", port=6333, container_port=6333, store_kind="qdrant",
        config={"url": "http://127.0.0.1:6333"}, notes="Vector database with filtering. Good from small to very large knowledge bases."),
}


def _secrets_file() -> Path:
    return data_dir() / "rag" / "stack-secrets.json"


def stack_secret(stack_id: str, name: str) -> str:
    """A random secret for a container, made on first use and kept in the data folder. Never returned by the API."""
    path = _secrets_file()
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    key = f"{stack_id}.{name}"
    if key not in known:
        known[key] = _secrets.token_hex(24)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(known), encoding="utf-8")
        os.replace(tmp, path)
    return known[key]


def forget_secrets(stack_id: str) -> None:
    """Drop a stack's generated secrets (called when its data volume is deleted, so the next install starts fresh)."""
    path = _secrets_file()
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    path.write_text(json.dumps({k: v for k, v in known.items() if not k.startswith(f"{stack_id}.")}), encoding="utf-8")


def store_secrets(s: Stack) -> dict:
    """The secrets the store we register for this container needs."""
    return {**s.secrets, **{field_: stack_secret(s.id, name) for field_, name in s.gen_store.items()}}


def _docker(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    exe = shutil.which("docker")
    if not exe:
        raise StackError("Docker is not installed or not on PATH")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        raise StackError("Docker did not respond in time") from e


def _why(r: subprocess.CompletedProcess) -> str:
    """The last line Docker printed, which is where it puts the reason."""
    text = (r.stderr or r.stdout or "").strip()
    return text.splitlines()[-1][:300] if text else "docker failed"


def _spec(stack_id: str) -> Stack:
    if stack_id not in STACKS:
        raise StackError(f"unknown stack '{stack_id}'")
    return STACKS[stack_id]


def _state(s: Stack) -> str:
    r = _docker(["inspect", "-f", "{{.State.Status}}", s.container], timeout=10)
    if r.returncode != 0:
        return "missing"
    return "running" if r.stdout.strip() == "running" else "stopped"


def available() -> list[dict]:
    """Every stack with its current state; one cheap Docker query each."""
    return [status(sid) for sid in STACKS]


def status(stack_id: str) -> dict:
    s = _spec(stack_id)
    base = {"id": s.id, "label": s.label, "image": s.image, "port": s.port, "store_kind": s.store_kind, "notes": s.notes}
    try:
        up = _docker(["version", "--format", "{{.Server.Version}}"], timeout=8)
        if up.returncode != 0:
            return {**base, "docker": False, "state": "unavailable", "detail": "Docker is not running - start Docker Desktop"}
        state = _state(s)
    except StackError as e:
        return {**base, "docker": False, "state": "unavailable", "detail": str(e)}
    return {**base, "docker": True, "state": state, "detail": {"running": f"running on 127.0.0.1:{s.port}",
                                                              "stopped": "stopped (your data is kept)",
                                                              "missing": "not created yet"}[state]}


def start(stack_id: str) -> dict:
    """Create the container on first use (pulling the image), or start it again."""
    s = _spec(stack_id)
    state = _state(s)
    if state == "running":
        return status(stack_id)
    if state == "stopped":
        r = _docker(["start", s.container], timeout=60)
    else:
        args = ["run", "-d", "--name", s.container, "--restart", "unless-stopped", "--label", "agent-studio=rag",
                "-p", f"127.0.0.1:{s.port}:{s.container_port}", "-v", f"{s.volume}:{s.mount}"]
        generated = {var: stack_secret(s.id, name) for var, name in s.gen_env.items()}
        for k, v in {**s.env, **s.secrets, **generated}.items():
            args += ["-e", f"{k}={v}"]
        r = _docker([*args, s.image, *s.cmd], timeout=900)            # the first run pulls the image
    if r.returncode != 0:
        raise StackError(_why(r))
    return status(stack_id)


def stop(stack_id: str) -> dict:
    s = _spec(stack_id)
    if _state(s) == "running":
        r = _docker(["stop", s.container], timeout=60)
        if r.returncode != 0:
            raise StackError(_why(r))
    return status(stack_id)


def remove(stack_id: str, delete_data: bool = False) -> dict:
    """Remove the container. Its volume (your vectors) is kept unless delete_data is true."""
    s = _spec(stack_id)
    if _state(s) != "missing":
        r = _docker(["rm", "-f", s.container], timeout=60)
        if r.returncode != 0:
            raise StackError(_why(r))
    if delete_data:
        _docker(["volume", "rm", "-f", s.volume], timeout=60)
        forget_secrets(s.id)
    return status(stack_id)
