"""Coverage for app/mcp_openapi.py (spec loading, URL/YAML inputs, refusals, CLI) and app/telemetry.py
(attribute mapping, forwarding queue, collector pump). No real network: httpx uses MockTransport and
urllib.request.urlopen is replaced by a recorder."""
import json
import queue
import runpy
import sys

import httpx
import pytest

from app import mcp_openapi as M
from app import telemetry as T
from app.db import Database

SPEC = {
    "openapi": "3.0.3", "info": {"title": "Items API", "version": "1"},
    "servers": [{"url": "https://api.example.test"}],
    "paths": {
        "/items": {"get": {"operationId": "listItems", "summary": "List items"}},
        "/items/{id}": {"get": {"operationId": "getItem", "parameters": [{"name": "id", "in": "path", "required": True}]}},
        "/notes": "not-an-object",
        "@evil.example/steal": {"get": {"operationId": "steal"}},
    },
}


@pytest.fixture
def transport(monkeypatch):
    calls = []
    state = {"handler": None}

    def dispatch(req):
        calls.append((req.method, str(req.url)))
        return state["handler"](req)

    monkeypatch.setattr(M, "TRANSPORT", httpx.MockTransport(dispatch))
    monkeypatch.setattr(M, "_allow_write", False)
    monkeypatch.setenv("HEADER_AUTHORIZATION", "Bearer t")
    return state, calls


def _ok_json(req):
    return httpx.Response(200, json={"path": req.url.path})


# ---------- mcp_openapi ----------

def _fake_remote(monkeypatch, status=200, text=""):
    """Serves the spec through the module's injectable transport (the URL branch of _load). Returns the URLs requested."""
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(status, text=text)

    monkeypatch.setattr(M, "TRANSPORT", httpx.MockTransport(handler))
    return seen


def test_spec_from_https_url_and_yaml_fallback(monkeypatch):
    seen = _fake_remote(monkeypatch, text="openapi: 3.0.0\npaths: {}\nservers:\n  - url: https://api.example.test\n")
    doc, base, ops = M.parse("https://specs.example.test/openapi.yaml")
    assert base == "https://api.example.test" and ops == {}
    assert seen == ["https://specs.example.test/openapi.yaml"]


def test_remote_spec_http_error_is_raised(monkeypatch):
    _fake_remote(monkeypatch, status=404, text="gone")
    with pytest.raises(httpx.HTTPStatusError):
        M.parse("https://specs.example.test/missing.json")


def test_relative_server_url_resolves_against_remote_spec(monkeypatch):
    _fake_remote(monkeypatch, text=json.dumps(dict(SPEC, servers=[{"url": "/v1/"}])))
    _, base, _ = M.parse("https://specs.example.test/docs/openapi.json")
    assert base == "https://specs.example.test/v1"


def test_remote_spec_loads_through_the_real_httpx_get(transport):
    state, _ = transport
    state["handler"] = lambda req: httpx.Response(200, text=json.dumps(SPEC))
    _, base, _ = M.parse("https://specs.example.test/openapi.json")
    assert base == "https://api.example.test"


def test_relative_server_url_on_local_file_needs_base(tmp_path):
    p = tmp_path / "rel.json"
    p.write_text(json.dumps(dict(SPEC, servers=[{"url": "/v1"}])), encoding="utf-8")
    with pytest.raises(ValueError, match="relative server URL needs --base"):
        M.parse(str(p))
    _, base, _ = M.parse(str(p), base="https://given.example.test/")
    assert base == "https://given.example.test"


def test_spec_without_any_server_url_is_rejected(tmp_path):
    p = tmp_path / "nosrv.json"
    p.write_text(json.dumps(dict(SPEC, servers=[])), encoding="utf-8")
    with pytest.raises(ValueError, match="no server URL"):
        M.parse(str(p))


def test_non_object_documents_are_rejected(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="not an OpenAPI document"):
        M.parse(str(p))


def test_operations_skip_non_object_path_items_and_describe_bodies(tmp_path):
    spec = dict(SPEC, paths=dict(SPEC["paths"], **{
        "/bodies": {"post": {"operationId": "makeThing",
                             "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}}},
        "/opaque": {"put": {"operationId": "opaqueBody", "requestBody": {"content": {"text/plain": {}}}}},
    }))
    p = tmp_path / "s.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    _, _, ops = M.parse(str(p))
    assert "notes" not in " ".join(ops) and ops["makething"]["body"] == ["(body)"]
    assert ops["opaquebody"]["body"] == ["(body)"]
    assert ops["listitems"]["body"] is None and ops["getitem"]["parameters"] == [{"name": "id", "in": "path", "required": True}]


def test_yaml_spec_file_is_parsed(tmp_path):
    p = tmp_path / "api.yaml"
    p.write_text("openapi: 3.0.0\ninfo: {title: Y, version: '1'}\nservers: [{url: 'https://y.example.test'}]\n"
                 "paths:\n  /ping:\n    get:\n      operationId: ping\n      summary: Ping\n", encoding="utf-8")
    assert M.validate(str(p)) == 1
    M.configure(str(p))
    try:
        assert M.list_operations()["operations"][0] == {"operation_id": "ping", "method": "GET", "path": "/ping",
                                                        "summary": "Ping", "allowed": True}
    finally:
        M.configure(str(p), None)


def test_unknown_operation_and_path_escape_are_refused(transport, tmp_path):
    state, calls = transport
    state["handler"] = _ok_json
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SPEC), encoding="utf-8")
    M.configure(str(p))
    try:
        assert M.call_operation("does-not-exist")["ok"] is False
        r = M.call_operation("steal")
        assert r == {"ok": False, "error": "refused: the request would leave the API's server"}
        assert calls == []
    finally:
        M.configure(str(p))


def test_transport_failure_and_non_json_body(transport, tmp_path):
    state, _ = transport
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SPEC), encoding="utf-8")
    M.configure(str(p))
    try:
        def boom(req):
            raise httpx.ConnectError("no route")
        state["handler"] = boom
        r = M.call_operation("listitems")
        assert r["ok"] is False and r["error"] == "request failed: ConnectError: no route"

        state["handler"] = lambda req: httpx.Response(200, text="plain text body")
        r = M.call_operation("listitems")
        assert r["ok"] is True and r["status"] == 200 and r["body"] == "plain text body" and r["truncated"] is False
    finally:
        M.configure(str(p))


def test_cli_usage_and_run(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        M.main([])
    assert e.value.code == 2 and "usage:" in capsys.readouterr().err

    p = tmp_path / "s.json"
    p.write_text(json.dumps(SPEC), encoding="utf-8")
    ran = []
    monkeypatch.setattr(M.server, "run", lambda transport: ran.append(transport))
    try:
        M.main([str(p), "--base", "https://cli.example.test"])
        assert ran == ["stdio"] and M._base == "https://cli.example.test"
    finally:
        M.configure(str(p))


def test_module_entrypoint_runs_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["app.mcp_openapi"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.mcp_openapi", run_name="__main__")
    assert e.value.code == 2


# ---------- telemetry ----------

class FakeQueue:
    """Yields queued items, then stops the pump loop the way a closed process would (SystemExit)."""

    def __init__(self, items):
        self.items = list(items)

    def get(self):
        if not self.items:
            raise SystemExit("drained")
        return self.items.pop(0)


@pytest.fixture
def tel(tmp_path):
    return T.Telemetry(Database(tmp_path / "otel.db"))


def _alive_thread():
    class Alive:
        def is_alive(self):
            return True
    return Alive()


def test_receiver_url_setting_is_normalised(tel):
    out = tel.update({"receiver_url": "http://127.0.0.1:9000/"})
    assert out["receiver_url"] == "http://127.0.0.1:9000"


def test_nonscalar_otlp_attribute_is_stored_as_json_text():
    attrs = T._attrs([{"key": "list", "value": {"arrayValue": {"values": [{"stringValue": "a"}]}}},
                      {"key": "n", "value": {"intValue": "42"}}, {"key": "ok", "value": {"boolValue": True}}])
    assert attrs["list"].startswith('{"arrayValue"') and attrs["n"] == 42 and attrs["ok"] is True


def test_otlp_attribute_encoding_per_python_type():
    out = {a["key"]: a["value"] for a in T._otlp_attrs({"b": True, "i": 3, "f": 1.5, "s": "x" * 3000})}
    assert out["b"] == {"boolValue": True} and out["i"] == {"intValue": "3"} and out["f"] == {"doubleValue": 1.5}
    assert len(out["s"]["stringValue"]) == 2000


def test_empty_insert_is_a_noop(tel):
    tel._insert([])
    assert tel.summary()["total"] == 0


def test_span_is_stored_and_queued_for_forwarding_when_collector_set(tel, monkeypatch):
    import time
    t0 = time.time()                              # retention purge drops rows older than 30 days, so use real times
    tel.update({"forward_url": "http://collector.test:4318/"})
    tel._thread = _alive_thread()                 # no real pump thread in tests
    tel._q = queue.Queue()
    tel.span("engineer", "t" * 32, "s" * 16, "p" * 16, "tool:read", t0, t0 + 1,
             attrs={"bytes": 12, "ratio": 0.5, "cached": False, "tag": "x"}, status="error")
    rows = tel.trace("t" * 32)
    assert len(rows) == 1 and rows[0]["status"] == "error" and rows[0]["source"] == "engineer"
    assert rows[0]["attrs"] == {"bytes": 12, "ratio": 0.5, "cached": False, "tag": "x"}
    signal, body = tel._q.get_nowait()
    span = json.loads(body)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert signal == "traces" and span["parentSpanId"] == "p" * 16 and span["status"] == {"code": 2}
    assert span["name"] == "tool:read" and span["startTimeUnixNano"] == str(int(t0 * 1e9))


def test_span_is_skipped_entirely_when_disabled(tel):
    tel.update({"enabled": False})
    tel.span("x", "a" * 32, "b" * 16, None, "n", 1.0, 2.0)
    assert tel.summary()["total"] == 0


def test_ingest_rejects_unknown_signal(tel):
    with pytest.raises(ValueError, match="unknown signal"):
        tel.ingest("profiles", {})


def test_ingest_forwards_raw_bytes_when_collector_configured(tel):
    tel.update({"forward_url": "http://collector.test:4318"})
    tel._thread = _alive_thread()
    tel._q = queue.Queue()
    raw = b'{"resourceLogs": []}'
    assert tel.ingest("logs", {"resourceLogs": []}, forward_raw=raw) == 0
    assert tel._q.get_nowait() == ("logs", raw)


def test_full_forward_queue_counts_and_reports_drop(tel):
    tel._thread = _alive_thread()
    tel._q = queue.Queue(maxsize=1)
    tel._enqueue("traces", {"a": 1})
    tel._enqueue("traces", {"a": 2})               # queue full -> dropped
    assert tel.forward_errors == 1 and tel.last_forward_error == "forward queue full - dropped"
    assert tel.cfg()["forward_errors"] == 1


def test_enqueue_starts_pump_thread_once(tel, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target, self.name, self.daemon = target, name, daemon
            self.alive = False

        def start(self):
            started.append(self.name)
            self.alive = True

        def is_alive(self):
            return self.alive

    monkeypatch.setattr(T.threading, "Thread", FakeThread)
    tel._enqueue("traces", b"{}")
    tel._enqueue("metrics", b"{}")
    assert started == ["otel-forward"]             # a live pump is reused, not restarted


def test_pump_posts_to_collector_with_headers_and_counts_failures(tel, monkeypatch):
    tel.update({"forward_url": "http://collector.test:4318/", "forward_headers": {"Authorization": "Bearer SECRET"}})
    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout):
        sent.append((req.full_url, req.get_method(), req.get_header("Authorization"), req.data, timeout))
        if len(sent) == 2:
            raise OSError("collector down")
        return Resp()

    monkeypatch.setattr(T.urllib.request, "urlopen", fake_urlopen)
    tel._q = FakeQueue([("traces", b'{"t":1}'), ("logs", b'{"l":1}')])
    with pytest.raises(SystemExit):
        tel._pump()
    assert sent[0] == ("http://collector.test:4318/v1/traces", "POST", "Bearer SECRET", b'{"t":1}', 10)
    assert sent[1][0] == "http://collector.test:4318/v1/logs"
    assert tel.forwarded == 1 and tel.forward_errors == 1
    assert tel.last_forward_error == "OSError: collector down"


def test_pump_drops_items_when_no_collector_is_configured(tel, monkeypatch):
    def must_not_send(*a, **k):
        raise AssertionError("no collector configured: nothing may be sent")

    monkeypatch.setattr(T.urllib.request, "urlopen", must_not_send)
    tel._q = FakeQueue([("traces", b"{}")])
    with pytest.raises(SystemExit):
        tel._pump()
    assert tel.forwarded == 0 and tel.forward_errors == 0
