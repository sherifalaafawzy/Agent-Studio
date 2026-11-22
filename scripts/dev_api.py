"""Run the API with auto-reload for `npm run dev`.

Why not `uvicorn --reload`: on Windows uvicorn restarts its worker with a console-wide Ctrl+C. Under `concurrently`
that Ctrl+C also kills the Next.js dev server sharing the console, and it can leave orphaned workers holding the
port. So this launcher does the reloading itself: it watches backend/app/*.py and, on a change, kills the API
process tree (taskkill /T on Windows) and starts it again. The API runs in its own hidden console (Windows), its
output is relayed here, and it is always cleaned up when this launcher exits.
"""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
WIN = os.name == "nt"
PY = BACKEND / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")
PORT = os.environ.get("AGENT_STUDIO_PORT", "8765")
CMD = [str(PY), "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", PORT]


def snapshot() -> dict:
    out = {}
    for p in APP.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            out[p] = p.stat().st_mtime_ns
        except OSError:
            pass
    return out


def start() -> subprocess.Popen:
    kw: dict = dict(cwd=BACKEND, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw.update(creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    else:
        kw.update(start_new_session=True)
    proc = subprocess.Popen(CMD, **kw)

    def relay() -> None:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, b""):
            sys.stdout.write(line.decode("utf-8", "replace"))
            sys.stdout.flush()

    threading.Thread(target=relay, daemon=True).start()
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if WIN:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def running_turns() -> int:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/chat/jobs", timeout=3) as r:
            return sum(1 for j in json.load(r) if j.get("status") == "running")
    except Exception:  # noqa: BLE001 - API not answering: nothing to protect
        return 0


def wait_for_running_turns(max_s: int = 1800) -> None:
    waited = 0
    while (n := running_turns()) and waited < max_s:
        if waited == 0:
            print(f"[dev_api] {n} chat turn(s) running - the restart waits for them to finish (up to 30 min)", flush=True)
        time.sleep(5)
        waited += 5
    if waited:
        print("[dev_api] turns finished - restarting", flush=True)


def main() -> int:
    seen = snapshot()
    proc = start()
    print(f"[dev_api] watching {APP} for changes", flush=True)
    try:
        while True:
            time.sleep(1.0)
            now = snapshot()
            if now != seen:
                time.sleep(0.4)                       # let editors finish writing
                seen = snapshot()
                changed = sorted({str(p.relative_to(BACKEND)) for p in set(now) ^ set(seen) | {p for p in now if seen.get(p) != now.get(p)}})
                print(f"[dev_api] change in {', '.join(changed[:3]) or 'app'} - restarting the API", flush=True)
                wait_for_running_turns()              # never cut a chat turn off in the middle of its work
                stop(proc)
                proc = start()
            elif proc.poll() is not None:            # crashed (e.g. syntax error): wait for the next edit
                print(f"[dev_api] API exited with code {proc.returncode} - fix the error and save to restart", flush=True)
                while snapshot() == seen:
                    time.sleep(1.0)
                seen = snapshot()
                proc = start()
    except KeyboardInterrupt:
        return 0
    finally:
        stop(proc)


if __name__ == "__main__":
    sys.exit(main())
