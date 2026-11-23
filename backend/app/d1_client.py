"""Backend side of the d1 worker: starts `app.d1_worker` in the isolated environment, loads the model in the background and
answers one-shot "decide" requests with a timeout.

State: idle -> loading -> ready | failed. A cold worker is NEVER waited for: loading the weights takes seconds to minutes, so
`decide` raises WorkerLoading ("still loading") immediately and the caller falls back (Auto mode then uses its built-in rules
and says so). The process dies with the backend: its stdin pipe closes and the worker's loop ends.
"""
from __future__ import annotations

import collections
import itertools
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import d1_spec
from .d1_worker import PREFIX

LOAD_TIMEOUT_S = 1800.0           # the first load can be slow on CPU / a cold disk
RETRY_AFTER_S = 30.0              # after a failed load, wait this long before trying again


class WorkerUnavailable(Exception):
    """The model is not loaded (yet) or the worker died. The message is safe to show."""


class WorkerTimeout(WorkerUnavailable):
    """A decision did not come back in time."""


class WorkerLoading(WorkerUnavailable):
    """The model is loading right now. Not a failure: it will answer once it is in memory."""


def default_spawn(model: str) -> Callable[[], subprocess.Popen]:
    def spawn() -> subprocess.Popen:
        from .d1_setup import BACKEND_DIR, _child_env
        h = d1_spec.home()
        env = _child_env(h, {"PYTHONPATH": str(BACKEND_DIR), "D1_MODEL": model})
        return subprocess.Popen([str(d1_spec.env_python(h)), "-m", "app.d1_worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                                cwd=str(Path(BACKEND_DIR)), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return spawn


class D1Worker:
    def __init__(self, model: str, spawn: Callable[[], subprocess.Popen] | None = None, clock: Callable[[], float] = time.monotonic):
        self.model = d1_spec.spec(model).id
        self._spawn = spawn or default_spawn(self.model)
        self._clock = clock
        self.state, self.error = "idle", ""
        self.device: str | None = None
        self.load_seconds: float | None = None
        self._proc: subprocess.Popen | None = None
        self._send_lock = threading.Lock()
        self._cv = threading.Condition()
        self._replies: dict[int, dict] = {}
        self._waiting: set[int] = set()
        self._ids = itertools.count(1)
        self._failed_at = 0.0
        self._loading_since: float | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=8)

    # ------------------------------------------------------------ process plumbing
    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:                                  # type: ignore[union-attr]
            if not line.startswith(PREFIX):
                continue
            try:
                reply = json.loads(line[len(PREFIX):])
            except ValueError:
                continue
            with self._cv:
                if reply.get("id") in self._waiting:
                    self._replies[reply["id"]] = reply
                    self._cv.notify_all()
        with self._cv:                                            # the worker's stdout closed: it exited
            if self._proc is proc:
                self._proc = None
                if self.state in ("loading", "ready"):
                    self._fail("the decision-model worker exited" + (f": {' | '.join(self._stderr)}" if self._stderr else ""))
            self._cv.notify_all()

    def _drain(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:                                  # type: ignore[union-attr]
            if line.strip():
                self._stderr.append(line.strip()[:200])

    def _fail(self, message: str) -> None:
        self.state, self.error, self._failed_at = "failed", message, self._clock()

    def _start_process(self) -> subprocess.Popen:
        proc = self._spawn()
        self._proc = proc
        threading.Thread(target=self._read, args=(proc,), name="d1-read", daemon=True).start()
        threading.Thread(target=self._drain, args=(proc,), name="d1-err", daemon=True).start()
        return proc

    def request(self, payload: dict, timeout: float) -> dict:
        """Send one request and wait for its reply. Raises WorkerTimeout / WorkerUnavailable."""
        proc = self._proc
        if proc is None:
            raise WorkerUnavailable("the decision-model worker is not running")
        rid = next(self._ids)
        with self._cv:
            self._waiting.add(rid)
        try:
            with self._send_lock:
                try:
                    proc.stdin.write(json.dumps({**payload, "id": rid}) + "\n")      # type: ignore[union-attr]
                    proc.stdin.flush()                                               # type: ignore[union-attr]
                except (OSError, ValueError) as e:
                    raise WorkerUnavailable(f"cannot reach the decision-model worker: {type(e).__name__}") from e
            deadline = self._clock() + timeout
            with self._cv:
                while rid not in self._replies:
                    if self._proc is not proc:
                        raise WorkerUnavailable(self.error or "the decision-model worker exited")
                    left = deadline - self._clock()
                    if left <= 0:
                        raise WorkerTimeout(f"no answer within {timeout:g} s")
                    self._cv.wait(left)
                return self._replies.pop(rid)
        finally:
            with self._cv:
                self._waiting.discard(rid)
                self._replies.pop(rid, None)

    # ------------------------------------------------------------ public
    def warm(self) -> None:
        """Start loading the model in the background (no-op when loading, loaded, or failed very recently)."""
        with self._cv:
            if self.state in ("loading", "ready"):
                return
            if self.state == "failed" and self._clock() - self._failed_at < RETRY_AFTER_S:
                return
            self.state, self.error, self._loading_since = "loading", "", self._clock()
        threading.Thread(target=self._warm_thread, name="d1-warm", daemon=True).start()

    def _warm_thread(self) -> None:
        try:
            if self._proc is None:
                self._start_process()
            reply = self.request({"op": "warm"}, LOAD_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - a spawn error, a timeout, a dead worker
            with self._cv:
                self._fail(str(e) or type(e).__name__)
                self._cv.notify_all()
            return
        with self._cv:
            if reply.get("ok"):
                self.state, self.device, self.load_seconds = "ready", reply.get("device"), reply.get("load_seconds")
            else:
                self._fail(str(reply.get("error") or "the model could not be loaded"))
            self._cv.notify_all()

    def wait_ready(self, max_s: float) -> None:
        """Wait (up to `max_s`) for the model to finish loading. Raises WorkerLoading when it is still loading, WorkerUnavailable when it failed."""
        deadline = self._clock() + max_s
        with self._cv:
            while self.state != "ready":
                if self.state == "failed":
                    raise WorkerUnavailable(f"the decision model could not be loaded: {self.error or 'unknown error'}")
                if self.state == "idle":
                    raise WorkerUnavailable("the decision model was stopped")
                left = deadline - self._clock()
                if left <= 0:
                    raise WorkerLoading("the decision model is still loading")
                self._cv.wait(min(left, 0.25))

    def decide(self, state: Any, questions: dict, timeout: float, wait_for_load: bool = False, load_wait: float | None = None) -> dict:
        """One forward pass. A worker that is not ready raises at once (and starts loading): WorkerLoading while it loads, WorkerUnavailable
        when it failed. A person who pressed a Test button passes `wait_for_load=True` to wait up to `load_wait` seconds for the load instead."""
        if self.state != "ready":
            self.warm()
            if wait_for_load:
                self.wait_ready(LOAD_TIMEOUT_S if load_wait is None else load_wait)
            elif self.state == "loading":
                raise WorkerLoading("the decision model is still loading")
            else:
                raise WorkerUnavailable(f"the decision model could not be loaded: {self.error or self.state}")
        return self.request({"op": "decide", "state": state, "questions": questions}, timeout)

    def close(self) -> None:
        with self._cv:
            proc, self._proc = self._proc, None
            self.state = "idle"
            self._cv.notify_all()
        if proc is not None:
            try:
                proc.stdin.close()                                                   # type: ignore[union-attr]
            except OSError:
                pass
            try:
                proc.terminate()
            except OSError:
                pass

    def public(self) -> dict:
        return {"model": self.model, "state": self.state, "error": self.error, "device": self.device, "load_seconds": self.load_seconds,
                "loading_for_s": round(self._clock() - self._loading_since, 1) if self.state == "loading" and self._loading_since is not None else None}
