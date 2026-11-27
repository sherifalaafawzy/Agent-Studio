"""Desktop start-up progress lines (app/boot.py) and the installed app's entry point (run_server.py)."""
import io
import runpy
import sys
import threading
from pathlib import Path

from app import boot


def test_line_is_prefixed_and_defaults_to_stdout(capsys):
    buf = io.StringIO()
    boot.line("starting", buf)
    assert buf.getvalue() == "[boot] starting\n"
    boot.line("hello")
    assert capsys.readouterr().out == "[boot] hello\n"


def test_heartbeat_prints_until_stopped_and_returns_the_elapsed_time():
    buf, seen = io.StringIO(), threading.Event()
    ticks = iter([100.0, 103.0, 106.0, 109.0])

    def count():
        seen.set()
        return 42

    last = [100.0]

    def clock():
        last[0] = next(ticks, last[0])
        return last[0]

    stop = boot.heartbeat(buf, interval=0.01, count=count, clock=clock)
    assert seen.wait(5)
    took = stop()
    assert "[boot] loading: 42 modules, " in buf.getvalue() and buf.getvalue().rstrip().endswith(" s")
    assert took >= 3.0
    n = buf.getvalue().count("\n")
    threading.Event().wait(0.05)
    assert buf.getvalue().count("\n") == n                       # stopped: no more lines


def test_heartbeat_defaults_count_loaded_modules():
    buf = io.StringIO()
    stop = boot.heartbeat(buf, interval=0.01)
    while "modules" not in buf.getvalue():
        threading.Event().wait(0.01)
    assert stop() >= 0
    assert int(buf.getvalue().split("loading: ")[1].split(" ")[0]) >= len([m for m in ("sys", "threading") if m in sys.modules])


def test_run_server_prints_progress_then_serves_on_the_given_port(monkeypatch, capsys):
    import uvicorn

    import app.main as main

    calls = {}
    monkeypatch.setattr(main, "create_app", lambda: "the-app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    monkeypatch.setenv("AGENT_STUDIO_PORT", "51234")
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "run_server.py"), run_name="__main__")
    assert calls == {"app": "the-app", "host": "127.0.0.1", "port": 51234, "log_level": "info"}
    out = capsys.readouterr().out
    assert out.startswith("[boot] starting\n") and "[boot] ready to listen after " in out
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "run_server.py"), run_name="not_main")   # imported: does nothing
    assert capsys.readouterr().out == ""
