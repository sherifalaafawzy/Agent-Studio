"""Start-up progress lines for the desktop build.

Loading the server's modules takes seconds normally, but the first start after an install can take much longer while
the antivirus checks thousands of new files. Until the server listens there is nothing to ask, so `run_server.py`
prints a line every few seconds and the desktop window shows the last one. Standard library only: this module is
imported before anything heavy."""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable, TextIO


def line(text: str, stream: TextIO | None = None) -> None:
    print(f"[boot] {text}", file=stream or sys.stdout, flush=True)


def heartbeat(stream: TextIO | None = None, interval: float = 3.0, count: Callable[[], int] = lambda: len(sys.modules),
              clock: Callable[[], float] = time.monotonic) -> Callable[[], float]:
    """Print "loading: N modules, T s" every `interval` seconds on a daemon thread. Returns stop(), which ends the
    thread and returns the seconds since the heartbeat started."""
    t0 = clock()
    done = threading.Event()

    def beat() -> None:
        while not done.wait(interval):
            line(f"loading: {count()} modules, {int(clock() - t0)} s", stream)

    th = threading.Thread(target=beat, name="boot-heartbeat", daemon=True)
    th.start()

    def stop() -> float:
        done.set()
        th.join(timeout=5)
        return clock() - t0

    return stop
