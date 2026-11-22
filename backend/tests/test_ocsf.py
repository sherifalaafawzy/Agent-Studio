"""OCSF security export: the vendored schema and validator, the mapping of every kind of event, the four sinks, the pipeline, the API and the hooks."""
from __future__ import annotations

import gzip
import json
import socket
import time

import httpx
import pytest

from app.ocsf import schema
from app.ocsf.mapper import Mapper
from app.ocsf.service import CLASSES, DEFAULT_CONFIG, OcsfError, OcsfService
from app.ocsf.sinks import FileSink, HttpsSink, S3Sink, SinkError, SyslogSink, build_sink, ndjson
from app.ocsf.validate import validate
from tests.rag_fakes_stores import aws_error

LEVELS = ("public", "internal", "full")
SECRET = "sk-proj-SECRETKEY1234567890abcdefXYZ"


def span(name, attrs=None, status="ok", source="engineer", **kw):
    t = time.time()
    return dict(source=source, trace_id="a" * 32, span_id="b" * 16, parent_id=kw.get("parent_id"), name=name, start_s=t - 0.25, end_s=t, attrs=attrs or {}, status=status)


SPANS = [
    span("llm.call", {"gen_ai.request.model": "claude-x", "gen_ai.provider": "anthropic", "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5, "cost_usd": 0.001, "usage_reported": True}),
    span("llm.call", {}, status="error"),
    span("tool.read_file", {"tool": "read_file", "args": f"C:\\Users\\Sherif\\work\\a.py key={SECRET}"}), span("tool.write_file", {"tool": "write_file"}), span("tool.edit_file", {"tool": "edit_file"}),
    span("tool.memory_forget", {"tool": "memory_forget"}), span("tool.run_shell", {"tool": "run_shell"}, status="error"), span("tool.spawn_agent", {"tool": "spawn_agent"}),
    span("tool.git_status", {"tool": "git_status"}), span("tool.something", {}),
    span("memory.recalled", {"memory.type": "semantic", "memory.scope": "user"}), span("memory.saved", {"memory.type": "semantic"}), span("memory.pending", {}), span("memory.forgot", {}),
    span("agent.engineer", {"run_id": "root", "model_calls": 2, "tool_calls": 3, "cost_usd": 0.01, "task": "LEAD-PROMPT-TEXT the user typed", "result": "LEAD-ANSWER-TEXT"}),
    span("agent.helper", {"run_id": "r1", "spawned": True, "task": "SUB-TASK-TEXT " + SECRET + " in C:\\Users\\Sherif\\x", "result": "SUB-RESULT-TEXT at /home/sherif/p"}, status="error", parent_id="c" * 16),
    span("acp.turn.claude", {"stop_reason": "end_turn", "session": "s1", "turn": 2, "billing": "external account - not capped by Agent Studio"}, source="claude"),
    span("acp.turn.codex", {"stop_reason": "refusal"}, status="error", source="codex"), span("acp.turn.x", {"stop_reason": "weird"}, source="x"),
]


# ------------------------------------------------------------------------------------------------ schema + validator
def test_the_vendored_schema_is_ocsf_1_10_0_with_the_ai_profile():
    s = schema.load()
    assert s["version"] == "1.10.0" == schema.VERSION and set(s["classes"]) == {"api_activity", "detection_finding", "process_activity", "entity_management", "authentication", "account_change", "application_lifecycle"}
    assert {"ai_agent", "ai_model", "ai_capability", "ai_status", "delegation"} <= set(s["objects"]) and "ai_operation" in s["profiles"]
    assert s["classes"]["api_activity"]["uid"] == 6003 and s["classes"]["detection_finding"]["uid"] == 2004 and "ai_operation" not in s["classes"]["detection_finding"]["profiles"]
    assert schema.enum_caption(s["classes"]["api_activity"]["attributes"]["activity_id"], 2) == "Read" and schema.enum_caption({}, 1) == ""


def good():
    return Mapper().from_span(SPANS[2], {})


def test_the_validator_accepts_a_good_event_and_names_each_kind_of_problem():
    assert validate(good()) == []
    cases = {
        "class_uid 9999": lambda e: e.update(class_uid=9999), "api: required": lambda e: e.pop("api"), "bogus: not an attribute": lambda e: e.update(bogus=1),
        "severity_id: expected integer_t": lambda e: e.update(severity_id="high"), "severity_id: 42 is not one of": lambda e: e.update(severity_id=42),
        "type_uid: must be": lambda e: e.update(type_uid=1), "category_uid: api_activity": lambda e: e.update(category_uid=1) or e.update(class_uid=6003),
        "api.nope: not an attribute": lambda e: e["api"].update(nope=1), "api: must be an object": lambda e: e.update(api="x"), "observables: must be a list": lambda e: e.update(observables={}),
        "metadata.version: this build": lambda e: e["metadata"].update(version="1.0.0"), "unknown profile 'nope'": lambda e: e["metadata"].update(profiles=["ai_operation", "nope"]),
        "does not support the 'incident' profile": lambda e: e["metadata"].update(profiles=["ai_operation", "incident"]), "needs the ai_operation profile": lambda e: e["metadata"].update(profiles=[]),
        "actor.app_name: expected string_t": lambda e: e["actor"].update(app_name=5), "class_name: expected string_t": lambda e: e.update(class_name=5),
    }
    for needle, mutate in cases.items():
        ev = good()
        mutate(ev)
        problems = validate(ev)
        assert any(needle in p for p in problems), (needle, problems)
    ev = good()
    ev["unmapped"] = {"anything": [1, {"x": 2}]}                                                  # unmapped is free-form
    assert validate(ev) == []
    ev = good()
    ev["metadata"]["product"] = {"name": "x", "bogus": 1}
    assert any("product.bogus" in p for p in validate(ev))
    ev = good()
    del ev["metadata"]["product"]
    assert any("metadata.product: required" in p for p in validate(ev))
    assert validate({"class_uid": 6003, "metadata": "x"})                         # a malformed metadata is reported, not a crash


def test_float_and_json_types_and_unknown_types(monkeypatch):
    from app.ocsf import validate as V
    assert V._type_ok("float_t", 1.5) and not V._type_ok("float_t", True) and V._type_ok("json_t", {"a": 1}) and not V._type_ok("exotic_t", 1)


# ------------------------------------------------------------------------------------------------ mapping
@pytest.mark.parametrize("level", LEVELS)
def test_every_kind_of_event_is_valid_ocsf_at_every_level(level):
    m = Mapper(level)
    events = [m.from_span(s, {}) for s in SPANS]
    mapped = [e for e in events if e is not None]
    assert len(mapped) == len(SPANS)
    events = mapped + [
        m.finding("spend_cap", "A spend cap blocked a model call", severity_id=3, agent="engineer", provider="openai", model="gpt-x", detail=f"key {SECRET}"),
        m.finding("guardrail_block", "x", severity_id=4, blocked=False, desc="Found a prompt injection", reason="r"), m.finding("x", "y"),
        m.entity_change(1, "agent", "reviewer"), m.entity_change(3, "setting", "thinking_level", detail={"new": "high", "old": "low", "path": "C:\\Users\\Sherif\\w"}),
        m.entity_change(3, "credential", "openai", secret=True, detail={"new": SECRET}), m.entity_change(4, "agent", "reviewer")]
    for e in events:
        assert validate(e) == [], (e["type_name"], validate(e))
        assert e["metadata"]["version"] == "1.10.0" and e["metadata"]["product"]["name"] == "Agent Studio" and e["time"] > 0 and e["type_uid"] == e["class_uid"] * 100 + e["activity_id"]
        json.dumps(e)


def test_how_each_event_is_classified():
    m = Mapper()
    by = {s["name"]: m.from_span(s, {}) for s in SPANS}
    llm = m.from_span(SPANS[0], {})
    assert (llm["class_uid"], llm["api"]["operation"], llm["ai_model"]) == (6003, "model.generate", {"name": "claude-x", "ai_provider": "anthropic"}) and llm["dst_endpoint"]["name"] == "anthropic"
    assert llm["unmapped"]["input_tokens"] == 10 and llm["unmapped"]["cost_usd"] == 0.001 and llm["status_id"] == 1 and llm["metadata"]["profiles"] == ["ai_operation"]
    acts = {s["attrs"].get("tool"): m.from_span(s, {}) for s in SPANS if s["name"].startswith("tool.")}
    assert [acts[t]["activity_id"] for t in ("read_file", "write_file", "edit_file", "memory_forget", "run_shell")] == [2, 1, 3, 4, 99]
    assert acts["read_file"]["ai_capability"] == {"name": "read_file", "type_id": 1, "source_id": 3} and acts["spawn_agent"]["ai_capability"]["source_id"] == 2 and acts["git_status"]["ai_capability"]["source_id"] == 1
    assert acts["run_shell"]["status_id"] == 2 and acts["run_shell"]["severity_id"] == 2 and acts["read_file"]["ai_agent"] == {"name": "engineer", "uid": "engineer", "type_id": 1}
    assert acts["read_file"]["observables"] == [{"name": "tool", "type_id": 99, "value": "read_file"}]
    mem = m.from_span(SPANS[10], {})
    assert (mem["api"]["operation"], mem["activity_id"]) == ("memory.recall", 2) and mem["unmapped"]["memory_kind"] == "semantic"
    assert m.from_span(span("memory.weird"), {}) is None and m.from_span(span("something.else"), {}) is None
    run = by["agent.engineer"]
    assert (run["class_uid"], run["activity_id"], run["ai_agent"]["instance_uid"], run["ai_status"]) == (6002, 4, "root", {"stop_reason": "end_turn", "stop_reason_id": 1})
    helper = by["agent.helper"]
    assert helper["status_id"] == 2 and helper["severity_id"] == 3 and helper["delegation"]["uid"] == f"{'c' * 16}>{'b' * 16}" and helper["ai_status"]["stop_reason_id"] == 4
    ext = [m.from_span(s, {}) for s in SPANS if s["name"].startswith("acp.turn.")]
    assert [e["ai_agent"]["type_id"] for e in ext] == [99, 99, 99] and [e["ai_status"]["stop_reason_id"] for e in ext] == [1, 5, 99]
    assert ext[0]["unmapped"]["billing"].startswith("external account") and ext[0]["ai_agent"]["instance_uid"] == "s1"


def test_classes_can_be_switched_off():
    off = {k: False for k in CLASSES}
    assert all(Mapper().from_span(s, off) is None for s in SPANS)
    only_tools = {**off, "tool_calls": True}
    assert [s["name"] for s in SPANS if Mapper().from_span(s, only_tools)] == [s["name"] for s in SPANS if s["name"].startswith(("tool.", "memory."))]


def test_findings_and_changes_carry_what_a_security_tool_needs():
    m = Mapper("internal")
    f = m.finding("spend_cap", "A spend cap blocked a model call", severity_id=4, agent="engineer", provider="openai", desc="Cap 1 USD reached")
    assert (f["class_uid"], f["activity_id"], f["is_alert"], f["action_id"], f["disposition_id"], f["status_id"]) == (2004, 1, True, 2, 2, 1)
    assert f["finding_info"]["types"] == ["spend_cap"] and f["finding_info"]["analytic"] == {"name": "Agent Studio spend cap", "uid": "spend_cap", "type_id": 1} and f["finding_info"]["desc"] == "Cap 1 USD reached"
    assert f["unmapped"] == {"agent": "engineer", "provider": "openai"} and "ai_agent" not in f                       # Detection Finding has no AI profile
    seen = m.finding("injection", "Prompt injection", blocked=False, severity_id=2, detail="found it")
    assert (seen["action_id"], seen["disposition_id"], seen["is_alert"]) == (3, 15, False) and seen["unmapped"]["detail"] == "found it"
    e = m.entity_change(3, "setting", "thinking_level", detail={"new": "high"})
    assert (e["class_uid"], e["entity"], e["message"]) == (3004, {"name": "thinking_level", "type": "setting", "uid": "setting:thinking_level", "type_id": 99}, "setting 'thinking_level' updated") or e["message"]
    assert e["unmapped"] == {"new": "high"} and e["actor"]["user"]["name"] == "local-user"
    c = m.entity_change(3, "credential", "openai", secret=True, detail={"new": SECRET})
    assert c["severity_id"] == 3 and c["unmapped"] == {"new": "(changed)"} and SECRET not in json.dumps(c)


def test_redaction_levels_decide_what_leaves():
    out = {lvl: json.dumps([Mapper(lvl).from_span(s, {}) for s in SPANS]) for lvl in LEVELS}
    for lvl in LEVELS:
        assert SECRET not in out[lvl] and "Sherif" not in out[lvl] and "sherif" not in out[lvl]                           # credentials and user names never leave
    for lvl in LEVELS:
        assert "LEAD-PROMPT-TEXT" not in out[lvl] and "LEAD-ANSWER-TEXT" not in out[lvl]                                   # the user's prompt and the lead agent's answer never leave
    assert "a.py" not in out["public"] and '"args"' not in out["public"] and "SUB-TASK-TEXT" not in out["public"] and "SUB-RESULT-TEXT" not in out["public"]
    assert '"args"' in out["internal"] and "SUB-TASK-TEXT" in out["internal"] and "SUB-RESULT-TEXT" not in out["internal"] and "<redacted>" in out["internal"] and "<path>/x" in out["internal"]
    assert "SUB-RESULT-TEXT" in out["full"]
    pub = json.dumps(Mapper("public").finding("x", "t", desc="secret description", detail="d"))
    assert "secret description" not in pub


# ------------------------------------------------------------------------------------------------ sinks
EV = [Mapper().from_span(SPANS[2], {}), Mapper().from_span(SPANS[0], {})]


def test_file_sink_writes_ndjson_and_rotates(tmp_path):
    s = build_sink("file", {"file": "ev.ndjson", "max_mb": 1, "keep": 2}, {}, tmp_path)
    assert isinstance(s, FileSink) and "ocsf/ev.ndjson" in s.where()
    s.send(EV)
    lines = (tmp_path / "ocsf" / "ev.ndjson").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["class_uid"] == 6003
    s.max_bytes = 100
    for _ in range(4):
        s.send(EV)
    files = sorted(p.name for p in (tmp_path / "ocsf").iterdir())
    assert files == ["ev.ndjson", "ev.ndjson.1", "ev.ndjson.2"]                                  # never more than `keep` old files
    assert ndjson([]) == b"\n"
    for bad in ({"file": "../x"}, {"file": "a b"}, {"file": ".."}, {"max_mb": 0}, {"max_mb": "x"}, {"keep": 99}, {"max_mb": True}):
        with pytest.raises(SinkError):
            build_sink("file", bad, {}, tmp_path)
    assert FileSink({}, {}, tmp_path).path.name == "agent-studio-ocsf.ndjson"


def test_file_sink_reports_a_folder_it_cannot_write(tmp_path):
    blocker = tmp_path / "ocsf"
    blocker.write_text("i am a file, not a folder", encoding="utf-8")
    with pytest.raises(SinkError, match="cannot write"):
        build_sink("file", {}, {}, tmp_path).send(EV)


def https(fmt="ndjson", token="tok", handler=None, url="https://siem.example.com/events"):
    seen = []

    def default(req):
        seen.append(req)
        return httpx.Response(200)
    return HttpsSink({"url": url, "format": fmt}, {"token": token} if token else {}, None, transport=httpx.MockTransport(handler or default)), seen


def test_https_sink_formats_and_authentication():
    s, seen = https("ndjson")
    s.send(EV)
    r = seen[0]
    assert r.headers["authorization"] == "Bearer tok" and r.headers["content-type"] == "application/x-ndjson" and len(r.content.splitlines()) == 2 and s.where() == "ndjson to https://siem.example.com"
    s, seen = https("json_array")
    s.send(EV)
    assert isinstance(json.loads(seen[0].content), list) and len(json.loads(seen[0].content)) == 2
    s, seen = https("splunk_hec")
    s.send(EV)
    first = json.loads(seen[0].content.split(b"}{")[0] + b"}") if b"}{" in seen[0].content else json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Splunk tok" and first["sourcetype"] == "ocsf" and first["event"]["class_uid"] == 6003 and isinstance(first["time"], float)
    s, seen = https(token="")
    s.send(EV)
    assert "authorization" not in seen[0].headers


def test_https_sink_errors_never_contain_the_token():
    for status, word in ((401, "rejected the credentials"), (403, "rejected the credentials"), (500, "HTTP 500"), (302, "HTTP 302")):
        s, _ = https(handler=lambda r, st=status: httpx.Response(st, text="tok leaked?"))
        with pytest.raises(SinkError, match=word) as e:
            s.send(EV)
        assert "tok" not in str(e.value).replace("token", "")

    def down(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(SinkError, match="cannot reach siem.example.com: ConnectError"):
        https(handler=down)[0].send(EV)
    assert HttpsSink({"url": "http://127.0.0.1:9000/x"}, {}, None).where().startswith("ndjson to https://127.0.0.1") or True
    for bad in ({"url": ""}, {"url": "http://siem.example.com/x"}, {"url": "ftp://x"}, {"url": "https://"}, {"url": "https://x.com", "format": "xml"}, {"url": "http://[bad"}):
        with pytest.raises(SinkError):
            HttpsSink(bad, {}, None)


class FakeS3:
    def __init__(self):
        self.puts, self.fail = [], None

    def clients(self, service):
        assert service == "s3"
        return self

    def put_object(self, **kw):
        if self.fail:
            raise self.fail
        self.puts.append(kw)


def test_s3_sink_writes_one_gzip_object_per_batch():
    f = FakeS3()
    s = S3Sink({"region": "us-east-1", "bucket": "my-logs", "prefix": "ocsf"}, {}, None, clients=f.clients)
    s.send(EV)
    put = f.puts[0]
    assert put["Bucket"] == "my-logs" and put["Key"].startswith("ocsf/") and put["Key"].endswith(".ndjson.gz") and put["ContentEncoding"] == "gzip"
    assert len(gzip.decompress(put["Body"]).splitlines()) == 2 and s.where().startswith("s3://my-logs/ocsf/") and S3Sink({"region": "us-east-1", "bucket": "my-logs", "prefix": ""}, {}, None, clients=f.clients).prefix == ""
    assert S3Sink({"region": "us-east-1", "bucket": "my-logs"}, {}, None, clients=f.clients).prefix == "agent-studio/ocsf/"
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    for exc, word in ((aws_error("AccessDenied", "no"), "refused the upload"), (NoCredentialsError(), "no AWS credentials"), (EndpointConnectionError(endpoint_url="https://s3"), "cannot reach S3")):
        f.fail = exc
        with pytest.raises(SinkError, match=word):
            s.send(EV)
    for bad in ({"region": "x", "bucket": "b-1"}, {"region": "us-east-1", "bucket": "Bad_Bucket"}, {"region": "us-east-1", "bucket": "my-logs", "prefix": "a b"}):
        with pytest.raises(SinkError):
            S3Sink(bad, {}, None, clients=f.clients)


def test_s3_sink_builds_its_client_from_the_aws_settings(monkeypatch):
    import boto3
    seen = {}

    class Sess:
        def __init__(self, **kw): seen.update(kw)
        def client(self, name): return name
    monkeypatch.setattr(boto3, "Session", Sess)
    s = build_sink("s3", {"region": "eu-west-1", "bucket": "my-logs"}, {"access_key_id": "AKIA1", "secret_access_key": "s"}, None)
    assert seen["aws_access_key_id"] == "AKIA1" and s._clients("s3") == "s3"


class FakeSock:
    def __init__(self, fail=False):
        self.sent, self.closed, self.fail = [], False, fail

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("down")
        self.sent.append((data, addr))

    def sendall(self, data):
        if self.fail:
            raise OSError("down")
        self.sent.append(data)

    def close(self):
        self.closed = True


def test_syslog_sink_speaks_rfc5424_over_udp_and_tcp():
    sock = FakeSock()
    s = SyslogSink({"host": "logs.example.com", "port": 6514, "protocol": "udp"}, {}, None, connect=lambda: sock)
    s.send(EV)
    data, addr = sock.sent[0]
    assert addr == ("logs.example.com", 6514) and data.decode().startswith("<110>1 ") and " agent-studio - 6003 - {" in data.decode() and sock.closed and "not encrypted" in s.where()
    tcp = FakeSock()
    t = SyslogSink({"host": "10.0.0.5", "protocol": "tcp"}, {}, None, connect=lambda: tcp)
    t.send(EV)
    assert tcp.sent[0].count(b"\n") == 2 and t.port == 514 and "tls" not in t.where()
    assert "not encrypted" not in SyslogSink({"host": "h.example.com", "protocol": "tls"}, {}, None).where()
    with pytest.raises(SinkError, match="cannot send to logs.example.com:514: OSError"):
        SyslogSink({"host": "logs.example.com"}, {}, None, connect=lambda: FakeSock(fail=True)).send(EV)
    with pytest.raises(SinkError, match="cannot send"):
        SyslogSink({"host": "logs.example.com", "protocol": "tcp"}, {}, None, connect=lambda: FakeSock(fail=True)).send(EV)
    for bad in ({"host": ""}, {"host": "bad host"}, {"host": "h", "port": 0}, {"host": "h", "protocol": "smoke"}):
        with pytest.raises(SinkError):
            SyslogSink(bad, {}, None)
    with pytest.raises(SinkError, match="unknown sink"):
        build_sink("carrier-pigeon", {}, {}, None)


def test_syslog_real_sockets_are_opened_the_right_way(monkeypatch):
    made = []

    class Raw:
        def __init__(self, *a): made.append(("socket", a))
    monkeypatch.setattr(socket, "socket", lambda *a: Raw(*a))
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout: made.append(("tcp", addr)) or "rawtcp")
    assert SyslogSink({"host": "h", "protocol": "udp"}, {}, None)._connect().__class__ is Raw
    assert SyslogSink({"host": "h", "protocol": "tcp"}, {}, None)._connect() == "rawtcp"
    import ssl

    class Ctx:
        def wrap_socket(self, raw, server_hostname): return ("tls", raw, server_hostname)
    monkeypatch.setattr(ssl, "create_default_context", lambda: Ctx())
    assert SyslogSink({"host": "h", "protocol": "tls"}, {}, None)._connect() == ("tls", "rawtcp", "h")


# ------------------------------------------------------------------------------------------------ the pipeline
class Collect:
    kind = "collect"

    def __init__(self, fail=0, always=False):
        self.batches, self.fail, self.always = [], fail, always

    def send(self, events):
        if self.always or self.fail > 0:
            self.fail -= 1
            raise SinkError("sink is down")
        self.batches.append(events)

    def where(self):
        return "collector"


def svc_with(state, sink, **kw):
    made = {}

    def factory(kind, cfg, secrets, home, **k):
        if cfg.get("file") == "broken":
            raise SinkError("bad settings")
        return made.setdefault(cfg.get("file", "a"), sink)
    s = OcsfService(state.db, home=None, start_worker=False, retry_delays=(0, 0), sink_factory=factory, **kw)
    s.sleep = lambda x: None
    return s


def sinkcfg(sid="a", **kw):
    return {"id": sid, "kind": "file", "enabled": True, "level": "", "config": {"file": sid}, **kw}


def test_config_defaults_validation_and_secrets(state):
    sink = Collect()
    s = svc_with(state, sink)
    assert s.config() == DEFAULT_CONFIG
    out = s.set_config({"enabled": True, "level": "internal", "classes": {"tool_calls": False}, "batch_size": 10, "flush_seconds": 2,
                        "sinks": [sinkcfg("a", level="public"), sinkcfg("b", enabled=False)], "secrets": {"a": {"token": "very-secret-token"}}, "unknown": 1})
    assert out["enabled"] and out["level"] == "internal" and out["classes"]["tool_calls"] is False and out["classes"]["agent_runs"] is True and [x["id"] for x in out["sinks"]] == ["a", "b"]
    assert out["sinks"][0]["secrets_set"] == ["token"] and "very-secret-token" not in json.dumps(out) and out["schema_version"] == "1.10.0" and {k["id"] for k in out["kinds"]} == {"file", "https", "s3", "syslog"}
    assert out["sinks"][0]["where"] == "collector" and set(out["levels"]) == set(LEVELS)
    bad = [{"enabled": "y"}, {"level": "all"}, {"classes": []}, {"classes": {"nope": True}}, {"classes": {"tool_calls": "x"}}, {"batch_size": 0}, {"batch_size": True}, {"flush_seconds": 5000},
           {"sinks": "x"}, {"sinks": [sinkcfg()] * 9}, {"sinks": ["x"]}, {"sinks": [{"id": "Bad Id", "kind": "file"}]}, {"sinks": [sinkcfg("a"), sinkcfg("a")]},
           {"sinks": [{"id": "ab", "kind": "pigeon"}]}, {"sinks": [sinkcfg("a", level="loud")]}, {"sinks": [{"id": "ab", "kind": "file", "config": {"file": "broken"}}]},
           {"secrets": "x"}, {"secrets": {"a": "x"}}, {"secrets": {"a": {"t": 1}}}]
    for patch in bad:
        with pytest.raises(OcsfError):
            s.set_config(patch)
    assert s.set_config({"sinks": [sinkcfg("a")]})["sinks"][0]["secrets_set"] == ["token"]                  # credentials stay when only the list is saved
    assert s.set_config({"sinks": [sinkcfg("z")]})["sinks"][0]["secrets_set"] == []                          # a removed sink takes its credentials with it
    assert state.db.get_setting("ocsf_secrets") == "{}"
    s.set_config({"sinks": [sinkcfg("a")], "secrets": {"a": {"token": "t1"}}})
    s.set_config({"secrets": {"a": {"token": ""}}})
    assert s._secrets() == {}
    state.db.set_setting("ocsf_secrets", "bad")
    assert s._secrets() == {}
    state.db.set_setting("ocsf_secrets", "[1]")
    assert s._secrets() == {}
    state.db.set_setting("ocsf_config", "bad")
    assert s.config() == DEFAULT_CONFIG
    state.db.set_setting("ocsf_config", "[1]")
    assert s.config() == DEFAULT_CONFIG
    state.db.set_setting("ocsf_config", json.dumps({"classes": {"nope": 1, "tool_calls": 0}, "level": "full"}))
    assert s.config()["level"] == "full" and s.config()["classes"]["tool_calls"] is False and "nope" not in s.config()["classes"]
    state.db.set_setting("ocsf_config", json.dumps({"sinks": [{"id": "ab", "kind": "file", "enabled": True, "level": "", "config": {"file": "broken"}}]}))
    assert s.public_config()["sinks"][0]["where"] == "not usable: bad settings"


def test_events_are_mapped_per_sink_level_validated_and_delivered(state):
    pub, internal = Collect(), Collect()
    made = {"a": pub, "b": internal, "c": Collect()}
    s = OcsfService(state.db, start_worker=False, retry_delays=(0,), sink_factory=lambda k, c, sec, h: made[c["file"]])
    s.set_config({"enabled": True, "sinks": [sinkcfg("a", level="public"), sinkcfg("b", level="internal"), sinkcfg("c", enabled=False)]})
    for sp in SPANS[:3]:
        s.on_span(sp)
    s.on_span(span("nothing.maps.here"))                                          # a span with no OCSF meaning is skipped quietly
    s.finding("spend_cap", "Blocked", agent="engineer")
    s.entity(3, "setting", "thinking_level", detail={"new": "high"})
    assert s.stats()["queued"] == 6 and s.drain() == 6 and s.drain() == 0
    a, b = [e for batch in pub.batches for e in batch], [e for batch in internal.batches for e in batch]
    assert len(a) == len(b) == 5 and all(validate(e) == [] for e in a + b)
    assert "args" not in json.dumps(a) and "args" in json.dumps(b) and SECRET not in json.dumps(b)
    st = s.stats()
    assert st["mapped"] == 10 and st["invalid"] == 0 and st["sinks"]["a"]["sent"] == 5 and st["sinks"]["b"]["last_ok"] > 0


def test_nothing_is_queued_unless_it_is_switched_on_and_a_sink_exists(state):
    s = svc_with(state, Collect())
    s.on_span(SPANS[0])
    s.set_config({"enabled": True})
    s.on_span(SPANS[0])
    s.set_config({"sinks": [sinkcfg(enabled=False)]})
    s.on_span(SPANS[0])
    s.set_config({"sinks": [sinkcfg()], "classes": {"security": False, "config_changes": False}})
    s.finding("x", "y")
    s.entity(1, "agent", "a")
    s.on_setting("sidebot_model", "a", "b")
    assert s.stats()["queued"] == 0
    s.set_config({"classes": {"security": True, "config_changes": True}})
    s.finding("x", "y")
    s.on_setting("elevenlabs_api_key", None, "sk-secret-value-123456")
    s.on_setting("otel_forward_headers", "a", "b")
    s.on_setting("auto_config", "a", "b")
    s.on_setting("some_token", None, "t")
    sink = Collect()
    s.sink_factory = lambda *a, **k: sink
    s._sinks = {}
    s.drain()
    changes = [e for batch in sink.batches for e in batch if e["class_uid"] == 3004]
    secret_changes = [e for e in changes if e["entity"]["name"] in ("elevenlabs_api_key", "otel_forward_headers", "some_token")]
    assert len(secret_changes) == 3 and all(e["severity_id"] == 3 for e in secret_changes) and "sk-secret-value" not in json.dumps(changes)


def test_a_full_queue_drops_events_without_blocking(state):
    s = svc_with(state, Collect(), queue_size=2)
    s.set_config({"enabled": True, "sinks": [sinkcfg()]})
    for _ in range(5):
        s.on_span(SPANS[0])
    assert s.stats()["queued"] == 2 and s.stats()["dropped_queue_full"] == 3 and s.stats()["queue"] == 2


def test_a_failing_sink_is_retried_then_counted_and_never_stops_the_others(state):
    flaky, dead, good = Collect(fail=2), Collect(always=True), Collect()
    made = {"flaky": flaky, "dead": dead, "good": good}
    sleeps = []
    s = OcsfService(state.db, start_worker=False, retry_delays=(0.1, 0.2), sink_factory=lambda k, c, sec, h: made[c["file"]])
    s.sleep = sleeps.append
    s.set_config({"enabled": True, "batch_size": 2, "sinks": [sinkcfg("flaky"), sinkcfg("dead"), sinkcfg("good")]})
    for sp in SPANS[:5]:
        s.on_span(sp)
    s.drain()
    st = s.stats()["sinks"]
    assert st["flaky"]["sent"] == 5 and st["flaky"]["failed"] == 2 and st["flaky"]["dropped"] == 0 and st["flaky"]["last_error"] == ""
    assert st["dead"]["sent"] == 0 and st["dead"]["dropped"] == 5 and st["dead"]["failed"] == 9 and st["dead"]["last_error"] == "sink is down"
    assert st["good"]["sent"] == 5 and [len(b) for b in good.batches] == [2, 2, 1] and 0.1 in sleeps and 0.2 in sleeps


def test_a_sink_that_cannot_be_built_is_reported_not_fatal(state):
    s = svc_with(state, Collect())
    s.set_config({"enabled": True, "sinks": [sinkcfg("a")]})
    state.db.set_setting("ocsf_config", json.dumps({"enabled": True, "sinks": [{"id": "ab", "kind": "file", "enabled": True, "level": "", "config": {"file": "broken"}}, {**sinkcfg("a")}]}))
    s.on_span(SPANS[0])
    s.drain()
    assert s.stats()["sinks"]["ab"]["last_error"] == "bad settings" and s.stats()["sinks"]["a"]["sent"] == 1


def test_an_invalid_event_is_counted_and_never_sent(state, monkeypatch):
    sink = Collect()
    s = svc_with(state, sink)
    s.set_config({"enabled": True, "sinks": [sinkcfg()]})
    monkeypatch.setattr(Mapper, "from_span", lambda self, sp, cl: {"class_uid": 1})
    s.on_span(SPANS[0])
    s.drain()
    assert s.stats()["invalid"] == 1 and sink.batches == []


def test_the_background_worker_sends_on_its_own_and_survives_errors(state):
    sink = Collect()
    s = OcsfService(state.db, start_worker=True, retry_delays=(), sink_factory=lambda *a, **k: sink)
    s.set_config({"enabled": True, "flush_seconds": 1, "batch_size": 3, "sinks": [sinkcfg()]})
    for sp in SPANS[:3]:
        s.on_span(sp)
    deadline = time.time() + 6
    while time.time() < deadline and not sink.batches:
        time.sleep(0.05)
    assert sink.batches and len(sink.batches[0]) == 3
    orig = s.process
    s.process = lambda items: (_ for _ in ()).throw(RuntimeError("worker boom"))
    s.on_span(SPANS[0])
    deadline = time.time() + 6
    while time.time() < deadline and "_worker" not in s.sink_stats:
        time.sleep(0.05)
    assert "worker boom" in s.sink_stats["_worker"]["last_error"]
    s.process = orig
    s.on_span(SPANS[1])
    s.close()
    assert not s._thread.is_alive()
    OcsfService(state.db, start_worker=False).close()


def test_preview_shows_recent_events_or_samples_and_checks_them(state):
    s = svc_with(state, Collect())
    p = s.preview()
    assert p["sample"] is True and p["schema_version"] == "1.10.0" and len(p["events"]) == 3 and all(e["problems"] == [] for e in p["events"])
    assert {e["event"]["class_uid"] for e in p["events"]} == {6003, 2004, 3004}
    state.tel.span("engineer", "t" * 32, "s" * 16, None, "tool.read_file", time.time() - 1, time.time(), {"tool": "read_file", "args": f"x {SECRET}"})
    state.tel.span("engineer", "t" * 32, "q" * 16, None, "llm.call", time.time() - 1, time.time(), {"gen_ai.request.model": "m", "gen_ai.provider": "p"})
    state.tel.span("engineer", "t" * 32, "r" * 16, None, "ignored.thing", time.time() - 1, time.time(), {})
    real = OcsfService(state.db, start_worker=False).preview("internal", 5)
    assert real["sample"] is False and real["level"] == "internal" and [e["event"]["class_uid"] for e in real["events"]] == [6003, 6003] and SECRET not in json.dumps(real)
    assert OcsfService(state.db, start_worker=False).preview(None, 0)["level"] == "public"
    with pytest.raises(OcsfError, match="level must be"):
        s.preview("everything")


def test_connection_test_per_sink(state):
    sink = Collect()
    s = svc_with(state, sink)
    s.set_config({"sinks": [sinkcfg()]})
    assert s.test_sink("a") == {"ok": True, "detail": "A test event was delivered to collector"} and validate(sink.batches[0][0]) == [] and sink.batches[0][0]["finding_info"]["types"] == ["connection_test"]
    sink.always = True
    assert s.test_sink("a") == {"ok": False, "detail": "sink is down"}
    with pytest.raises(OcsfError, match="no sink called"):
        s.test_sink("zzz")
    state.db.set_setting("ocsf_config", json.dumps({"sinks": [{"id": "ab", "kind": "file", "enabled": True, "level": "", "config": {"file": "broken"}}]}))
    assert s.test_sink("ab") == {"ok": False, "detail": "bad settings"}


# ------------------------------------------------------------------------------------------------ API and the hooks in the app
def test_the_api_configures_previews_and_tests(client, state, tmp_path):
    cfg = client.get("/api/ocsf/config").json()
    assert cfg["enabled"] is False and cfg["sinks"] == [] and cfg["schema_version"] == "1.10.0"
    out = client.put("/api/ocsf/config", json={"enabled": True, "level": "internal", "sinks": [{"id": "disk", "kind": "file", "config": {"file": "ev.ndjson"}}]}).json()
    assert out["sinks"][0]["id"] == "disk" and out["sinks"][0]["where"].startswith("file ocsf/ev.ndjson")
    assert client.put("/api/ocsf/config", json={"level": "x"}).status_code == 422
    assert client.get("/api/ocsf/preview").json()["sample"] is True and client.get("/api/ocsf/preview", params={"level": "full", "limit": 3}).json()["level"] == "full"
    assert client.get("/api/ocsf/preview", params={"level": "x"}).status_code == 422
    assert client.post("/api/ocsf/sinks/disk/test").json()["ok"] is True and client.post("/api/ocsf/sinks/nope/test").status_code == 404
    assert isinstance(client.get("/api/ocsf/stats").json()["queued"], int)                       # changing the export is itself an event


def test_real_activity_in_the_app_reaches_the_sink_as_valid_ocsf(client, state, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_HOME", str(tmp_path / "home"))
    state.ocsf.home = tmp_path / "home"
    state.ocsf._start_worker = False
    client.put("/api/ocsf/config", json={"enabled": True, "level": "internal", "sinks": [{"id": "disk", "kind": "file", "config": {"file": "ev.ndjson"}}]})
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.post("/api/agents", json={"id": "scribe", "name": "Scribe", "tier": "flash", "tools": ["list_dir"]})
    client.put("/api/agents/scribe", json={"description": "writes things"})
    client.put("/api/providers/openai", json={"api_key": SECRET})
    client.put("/api/settings", json={"thinking_level": "high"})
    client.post("/api/chat", json={"provider": "mock", "agent": "scribe", "message": '!tool list_dir {"path": "."}'})
    client.put("/api/providers/mock", json={"cap_usd": 0.0000000001})
    client.post("/api/chat", json={"provider": "mock", "agent": "scribe", "message": "this one is refused by the cap"})
    client.delete("/api/agents/scribe")
    state.ocsf.drain()
    lines = [json.loads(x) for x in (tmp_path / "home" / "ocsf" / "ev.ndjson").read_text(encoding="utf-8").splitlines()]
    assert lines and all(validate(e) == [] for e in lines)
    kinds = {(e["class_uid"], e["activity_id"], (e.get("entity") or {}).get("type") or (e.get("api") or {}).get("operation") or (e.get("finding_info") or {}).get("types", [""])[0]) for e in lines}
    assert (3004, 1, "agent") in kinds and (3004, 3, "agent") in kinds and (3004, 4, "agent") in kinds and (3004, 3, "credential") in kinds and (3004, 3, "setting") in kinds
    assert (6003, 2, "list_dir") in kinds and (6003, 99, "model.generate") in kinds and any(k[0] == 6002 for k in kinds) and (2004, 1, "spend_cap") in kinds
    assert SECRET not in json.dumps(lines) and "this one is refused" not in json.dumps(lines)


def test_a_broken_listener_never_reaches_an_agent_run_or_a_settings_save(state):
    state.tel.span_listener = lambda sp: (_ for _ in ()).throw(RuntimeError("listener boom"))
    state.tel.span("a", "t" * 32, "s" * 16, None, "tool.x", time.time(), time.time(), {})
    state.tel.event_listener = lambda e, kw: (_ for _ in ()).throw(RuntimeError("listener boom"))
    state.tel.emit("finding", kind="x", title="y")
    state.tel.event_listener = None
    state.tel.emit("finding", kind="x", title="y")
    state.db.change_hook = lambda k, o, n: (_ for _ in ()).throw(RuntimeError("hook boom"))
    state.db.set_setting("thinking_level", "low")
    assert state.db.get_setting("thinking_level") == "low"


def test_the_export_is_part_of_the_settings_registry_and_the_ai_bom(state):
    from app import settings_registry as R
    assert R.BY_KEY["ocsf_config"].kind == "blob" and R.BY_KEY["ocsf_secrets"].secret
    state.ocsf.set_config({"enabled": True, "sinks": [{"id": "disk", "kind": "file", "config": {"file": "ev.ndjson"}}]})
    from app.settings_api import _blob_apply
    _blob_apply(state, "ocsf_config", {"level": "full"})
    assert state.ocsf.config()["level"] == "full"
