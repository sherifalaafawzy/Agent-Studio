"""Runtimes: the container runner, the bundle, the four targets against fakes, and the release lifecycle (deploy / shadow / health / switch / rollback)."""
from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents import AgentDef
from app.runtimes import bundle as B
from app.runtimes import container_runner as CR
from app.runtimes import service as S
from app.runtimes import targets as T
from tests.rag_fakes_stores import aws_error


# ------------------------------------------------------------------------------------------------------------- container runner
def fake_completion(**kw):
    fake_completion.calls.append(kw)
    return {"choices": [{"message": {"content": "pong: " + kw["messages"][-1]["content"]}}]}


fake_completion.calls = []

SPEC = {"id": "bot", "name": "Bot", "instruction": "Be brief.", "model": "openai/x", "max_tokens": 50}


def runner(monkeypatch=None, token=None, spec=SPEC):
    if token and monkeypatch:
        monkeypatch.setenv("RUNTIME_TOKEN", token)
    return TestClient(CR.build_app(spec, fake_completion))


def test_runner_health_endpoints_and_invocations():
    c = runner()
    for path in ("/ping", "/health", "/readiness"):
        assert c.get(path).json()["status"] == "Healthy"
    r = c.post("/invocations", json={"prompt": "hello"}).json()
    assert r == {"response": "pong: hello", "model": "openai/x"}
    sent = fake_completion.calls[-1]
    assert sent["messages"][0] == {"role": "system", "content": "Be brief."} and sent["max_tokens"] == 50 and "api_base" not in sent


def test_runner_accepts_messages_inputs_and_content_parts(monkeypatch):
    c = runner()
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": [{"type": "output_text", "text": "b"}, "c"]}, {"role": "tool", "content": None}, "junk"]
    assert c.post("/invocations", json={"messages": msgs}).status_code == 200
    sent = fake_completion.calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"] and sent[2]["content"] == "bc" and sent[3]["content"] == ""
    assert c.post("/invocations", json={"message": "m"}).json()["response"] == "pong: m"
    monkeypatch.setenv("AGENT_API_BASE", "http://base")
    c.post("/invocations", json={"input": "i"})
    assert fake_completion.calls[-1]["api_base"] == "http://base"


def test_runner_refuses_bad_requests():
    c = runner()
    assert c.post("/invocations", json={}).status_code == 422
    assert c.post("/invocations", json={"messages": []}).status_code == 422
    assert c.post("/invocations", json=["x"]).status_code == 422
    assert c.post("/responses", json=["x"]).status_code == 422
    assert c.post("/invocations", json={"prompt": "x" * (CR.MAX_INPUT_CHARS + 1)}).status_code == 413


def test_runner_responses_shape_and_no_system_prompt():
    c = runner(spec={**SPEC, "instruction": ""})
    r = c.post("/responses", json={"input": "hi"}).json()
    assert r["output_text"] == "pong: hi" and r["output"][0]["content"][0]["text"] == "pong: hi" and r["status"] == "completed"
    assert [m["role"] for m in fake_completion.calls[-1]["messages"]] == ["user"]


def test_runner_token_guard(monkeypatch):
    c = runner(monkeypatch, token="s3cret")
    assert c.get("/ping").status_code == 200
    assert c.post("/invocations", json={"prompt": "x"}).status_code == 401
    assert c.post("/responses", json={"input": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/invocations", json={"prompt": "x"}, headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_runner_default_completion_and_object_responses(monkeypatch, tmp_path):
    import litellm

    class Msg:
        content = None

    class Resp:
        choices = [type("C", (), {"message": Msg()})()]
    monkeypatch.setattr(litellm, "completion", lambda **kw: Resp())
    f = tmp_path / "agent.json"
    f.write_text(json.dumps(SPEC))
    monkeypatch.setenv("AGENT_SPEC", str(f))
    c = TestClient(CR.build_app())                       # reads the spec from AGENT_SPEC, completion from litellm
    assert c.post("/invocations", json={"prompt": "x"}).json()["response"] == ""
    assert CR.load_spec(str(f))["id"] == "bot"


# ------------------------------------------------------------------------------------------------------------------- bundle
def test_bundle_spec_hash_diff_and_files(tmp_path):
    a = AgentDef(id="bot", name="Bot", description="d", instruction="i")
    spec = B.agent_spec(a, "openai/x")
    assert spec == {"id": "bot", "name": "Bot", "description": "d", "instruction": "i", "model": "openai/x", "max_tokens": 2048}
    h = B.spec_hash(spec)
    assert h == B.spec_hash(dict(reversed(list(spec.items())))) and h != B.spec_hash({**spec, "instruction": "j"})
    assert B.spec_diff(None, spec) == []
    changed = {**spec, "instruction": "x" * 300, "extra": 1}
    d = {x["field"]: x for x in B.spec_diff(spec, changed)}
    assert set(d) == {"instruction", "extra"} and d["instruction"]["after"].endswith("...") and len(d["instruction"]["after"]) == 160 and d["extra"]["before"] == ""
    files = B.write_bundle(tmp_path / "b", spec)
    assert files == ["Dockerfile", "agent.json", "requirements.txt", "runner.py"]
    assert json.loads((tmp_path / "b" / "agent.json").read_text()) == spec
    assert "FROM python:3.12-slim" in (tmp_path / "b" / "Dockerfile").read_text() and "litellm==" in (tmp_path / "b" / "requirements.txt").read_text()
    assert "def build_app" in (tmp_path / "b" / "runner.py").read_text()


def test_bundle_images_and_commands():
    assert B.default_image("reg.io/agents:{agent}-{tag}", "bot", "abcdef0123456789") == "reg.io/agents:bot-abcdef012345"
    assert B.image_ok("123.dkr.ecr.us-east-1.amazonaws.com/a:b") and not B.image_ok("") and not B.image_ok("-rm") and not B.image_ok("has space/x")
    ac = B.commands("agentcore", "1.dkr.ecr.eu.amazonaws.com/a:b", "/tmp/b", {"region": "eu-west-1"})
    assert ac[0].startswith("aws ecr get-login-password --region eu-west-1") and "--platform linux/arm64" in ac[1] and ac[2].startswith("docker push")
    assert "<region>" in B.commands("agentcore", "x.io/a:b", "/f", {})[0]
    assert B.commands("foundry", "myreg.azurecr.io/a:b", "/f", {})[0] == "az acr login --name myreg" and "linux/amd64" in B.commands("foundry", "myreg.azurecr.io/a:b", "/f", {})[1]
    assert B.commands("vertex", "us-docker.pkg.dev/p/r/a:b", "/f", {})[0] == "gcloud auth configure-docker us-docker.pkg.dev"
    assert len(B.commands("docker", "a:b", "/f", {})) == 1


# ------------------------------------------------------------------------------------------------------------------- targets
def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class FakeDocker:
    def __init__(self):
        self.calls, self.fail, self.state, self.port = [], {}, "running", "127.0.0.1:49153"

    def __call__(self, args, timeout):
        self.calls.append(args)
        cmd = args[0]
        if cmd in self.fail:
            return self.fail[cmd]
        if cmd == "inspect":
            return cp(out=self.state + "\n")
        if cmd == "port":
            return cp(out=self.port + "\n") if self.port else cp(1, err="no")
        if cmd == "version":
            return cp(out="27.1\n")
        return cp(out="id\n")


@pytest.fixture
def docker(monkeypatch):
    fd = FakeDocker()
    monkeypatch.setattr(T, "DOCKER_RUN", fd)
    return fd


def test_docker_target_deploy_status_invoke_delete(docker, monkeypatch):
    t = T.DockerTarget({}, {})
    with pytest.raises(T.TargetError, match="no bundle"):
        t.deploy(SPEC, "n", "img", {}, None, None)
    docker.fail["build"] = cp(1, err="boom")
    with pytest.raises(T.TargetError, match="docker build failed: boom"):
        t.deploy(SPEC, "n", "img", {}, None, "/f")
    del docker.fail["build"]
    docker.fail["run"] = cp(1, err="nope")
    with pytest.raises(T.TargetError, match="docker run failed: nope"):
        t.deploy(SPEC, "n", "img", {}, None, "/f")
    del docker.fail["run"]
    ref = t.deploy(SPEC, "n", "img", {"OPENAI_API_KEY": "k"}, None, "/f")
    run = [a for a in docker.calls if a[0] == "run"][-1]
    assert ref["container"] == "n" and ref["token"] and "OPENAI_API_KEY=k" in run and "--cap-drop" in run and f"RUNTIME_TOKEN={ref['token']}" in run
    seen: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), req.headers.get("authorization")))
        if req.url.path == "/ping":
            return httpx.Response(200, json={"status": "Healthy"})
        return httpx.Response(200, json={"response": "hi"}) if req.headers.get("authorization") == f"Bearer {ref['token']}" else httpx.Response(401, text="no")
    monkeypatch.setattr(T, "TRANSPORT", httpx.MockTransport(handler))
    assert t.status(ref) == {"state": "ready", "detail": "answering /ping"}
    assert t.route(ref, "live")["slot"] == "live"
    assert t.invoke(ref, "yo") == "hi" and seen[-1][1] == "http://127.0.0.1:49153/invocations"
    t.delete(ref)
    assert docker.calls[-1][:2] == ["rm", "-f"]
    assert t.check() == {"ok": True, "detail": "Docker 27.1 is running"}


def test_docker_target_problems(docker, monkeypatch):
    t = T.DockerTarget({}, {})
    ref = {"container": "n", "token": "t"}
    docker.fail["inspect"] = cp(1)
    assert t.status(ref)["state"] == "gone"
    del docker.fail["inspect"]
    docker.state = "exited"
    assert t.status(ref)["state"] == "failed"
    docker.state = "created"
    assert t.status(ref) == {"state": "creating", "detail": "container is created"}
    docker.state = "running"
    docker.port = ""
    assert t.status(ref)["state"] == "creating" and "no published port" in t.status(ref)["detail"]
    docker.port = "127.0.0.1:49153"
    monkeypatch.setattr(T, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(503)))
    assert t.status(ref)["detail"] == "started, not answering yet"
    with pytest.raises(T.TargetError, match="answered 503"):
        t.invoke(ref, "x")

    def boom(r):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(T, "TRANSPORT", httpx.MockTransport(boom))
    assert t.status(ref)["state"] == "creating"
    with pytest.raises(T.TargetError, match="did not answer"):
        t.invoke(ref, "x")
    docker.fail["version"] = cp(1)
    assert not t.check()["ok"]


def test_docker_helper_runs_the_real_cli_or_explains(monkeypatch):
    monkeypatch.setattr(T, "DOCKER_RUN", None)
    monkeypatch.setattr(T.shutil, "which", lambda n: None)
    with pytest.raises(T.TargetError, match="docker command was not found"):
        T.docker(["version"])
    monkeypatch.setattr(T.shutil, "which", lambda n: "/bin/docker")
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: cp(0, "ok"))
    assert T.docker(["version"]).stdout == "ok"

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("docker", 1)
    monkeypatch.setattr(T.subprocess, "run", slow)
    with pytest.raises(T.TargetError, match="did not answer in time"):
        T.docker(["version"])


def test_reply_text_shapes_and_base_interface():
    assert T.reply_text("x") == "x" and T.reply_text({"response": "r"}) == "r" and T.reply_text({"output_text": "o"}) == "o"
    assert T.reply_text({"output": [{"content": [{"text": "a"}, "junk", {"text": "b"}]}, "junk"]}) == "ab" and T.reply_text({"output": "plain"}) == "plain"
    assert T.reply_text({"other": 1}) == '{"other": 1}' and T.reply_text([1]) == "[1]"
    loc = T.Local({}, {})
    assert loc.check()["ok"] and loc.info()["id"] == "local"
    for call in (lambda: loc.deploy({}, "n", "i", {}, None, None), lambda: loc.status({}), lambda: loc.route({}, "live"), lambda: loc.invoke({}, "x"),
                 lambda: loc.delete({}), lambda: T.Target({}, {}).check()):
        with pytest.raises(NotImplementedError):
            call()
    with pytest.raises(T.TargetError, match="missing AWS region, Execution role ARN"):
        T.AgentCore({}, {})


class FakeAgentCore:
    def __init__(self):
        self.calls, self.fail, self.endpoints, self.status_value = [], {}, {}, "READY"

    def __getattr__(self, op):
        def call(**kw):
            self.calls.append((op, kw))
            if op in self.fail:
                raise self.fail[op]
            return getattr(self, "_" + op)(**kw)
        return call

    def _create_agent_runtime(self, **kw):
        return {"agentRuntimeId": "rt-1", "agentRuntimeArn": "arn:rt-1", "agentRuntimeVersion": "1", "status": "CREATING"}

    def _update_agent_runtime(self, **kw):
        return {"agentRuntimeId": kw["agentRuntimeId"], "agentRuntimeArn": "arn:rt-1", "agentRuntimeVersion": 2, "status": "UPDATING"}

    def _get_agent_runtime(self, **kw):
        return {"status": self.status_value, "failureReason": "bad image" if self.status_value.endswith("FAILED") else None}

    def _list_agent_runtimes(self, **kw):
        return {"agentRuntimes": [{}]}

    def _get_agent_runtime_endpoint(self, **kw):
        if kw["endpointName"] not in self.endpoints:
            raise aws_error("ResourceNotFoundException", "no endpoint")
        return {}

    def _create_agent_runtime_endpoint(self, **kw):
        self.endpoints[kw["name"]] = kw["agentRuntimeVersion"]
        return {"agentRuntimeEndpointArn": "arn:ep:" + kw["name"]}

    def _update_agent_runtime_endpoint(self, **kw):
        self.endpoints[kw["endpointName"]] = kw["agentRuntimeVersion"]
        return {"agentRuntimeEndpointArn": "arn:ep:" + kw["endpointName"]}

    def _delete_agent_runtime(self, **kw):
        return {}

    def _delete_agent_runtime_endpoint(self, **kw):
        return {}

    def _invoke_agent_runtime(self, **kw):
        return {"response": type("B", (), {"read": lambda self: json.dumps({"response": "from aws"}).encode()})()}


def agentcore(fake=None):
    fake = fake or FakeAgentCore()
    return T.AgentCore({"region": "us-east-1", "role_arn": "arn:role"}, {}, clients=lambda svc: fake), fake


def test_agentcore_deploy_update_status_and_routes():
    t, f = agentcore()
    ref = t.deploy(SPEC, "as_bot", "1.dkr.ecr.x/a:b", {"K": "v"}, None, None)
    op, kw = f.calls[-1]
    assert op == "create_agent_runtime" and kw["agentRuntimeName"] == "as_bot" and kw["agentRuntimeArtifact"] == {"containerConfiguration": {"containerUri": "1.dkr.ecr.x/a:b"}}
    assert kw["protocolConfiguration"] == {"serverProtocol": "HTTP"} and kw["environmentVariables"] == {"K": "v"} and kw["roleArn"] == "arn:role" and kw["networkConfiguration"] == {"networkMode": "PUBLIC"}
    assert ref == {"runtime_id": "rt-1", "arn": "arn:rt-1", "version": "1", "slot": None}
    ref2 = t.deploy(SPEC, "as_bot", "img2", {}, ref, None)
    assert f.calls[-1][0] == "update_agent_runtime" and f.calls[-1][1]["agentRuntimeId"] == "rt-1" and ref2["version"] == "2"
    assert t.status(ref) == {"state": "ready", "detail": "ready"}
    f.status_value = "UPDATE_FAILED"
    assert t.status(ref) == {"state": "failed", "detail": "bad image"}
    f.status_value = "DELETING"
    assert t.status(ref)["state"] == "gone"
    f.status_value = "CREATING"
    assert t.status(ref) == {"state": "creating", "detail": "creating"}
    live = t.route(ref2, "live")                           # no endpoint yet: created
    assert live["slot"] == "live" and live["endpoint_arn"] == "arn:ep:live" and f.endpoints["live"] == "2"
    assert t.route(ref, "live")["slot"] == "live" and f.endpoints["live"] == "1"          # endpoint exists: moved to the version
    f.fail["get_agent_runtime_endpoint"] = aws_error("AccessDeniedException", "no")
    with pytest.raises(T.TargetError, match="AccessDeniedException"):
        t.route(ref, "live")
    assert t.check()["ok"] and "1 runtime(s)" in t.check()["detail"]


def test_agentcore_invoke_and_delete():
    t, f = agentcore()
    ref = {"runtime_id": "rt-1", "arn": "arn:rt-1", "version": "1", "slot": None}
    with pytest.raises(T.TargetError, match="no endpoint yet"):
        t.invoke(ref, "x")
    assert t.invoke({**ref, "slot": "shadow"}, "hello") == "from aws"
    op, kw = f.calls[-1]
    assert kw["qualifier"] == "shadow" and kw["agentRuntimeArn"] == "arn:rt-1" and json.loads(kw["payload"]) == {"prompt": "hello"} and len(kw["runtimeSessionId"]) >= 33
    f._invoke_agent_runtime = lambda **kw: {"response": type("B", (), {"read": lambda self: b"plain text"})()}
    assert t.invoke({**ref, "slot": "live"}, "x") == "plain text"
    f._invoke_agent_runtime = lambda **kw: {"response": "raw string"}
    assert t.invoke({**ref, "slot": "live"}, "x") == "raw string"
    t.delete({**ref, "slot": "shadow"})
    assert f.calls[-1] == ("delete_agent_runtime_endpoint", {"agentRuntimeId": "rt-1", "endpointName": "shadow"})
    f.fail["delete_agent_runtime_endpoint"] = aws_error("ResourceNotFoundException", "gone")
    t.delete({**ref, "slot": "shadow"})                    # already gone: fine
    f.fail["delete_agent_runtime_endpoint"] = aws_error("ThrottlingException", "slow")
    with pytest.raises(T.TargetError, match="Throttling"):
        t.delete({**ref, "slot": "shadow"})
    t.delete(ref)                                          # no endpoint to remove
    t.delete(ref, whole=True)
    assert f.calls[-1] == ("delete_agent_runtime", {"agentRuntimeId": "rt-1"})


def test_agentcore_builds_real_boto3_clients(monkeypatch):
    import boto3
    seen = {}

    class Sess:
        def __init__(self, **kw):
            seen.update(kw)

        def client(self, name):
            seen["client"] = name
            return object()
    monkeypatch.setattr(boto3, "Session", Sess)
    t = T.AgentCore({"region": "eu-west-1", "role_arn": "r", "profile": "p"}, {"access_key_id": "A", "secret_access_key": "S", "session_token": "T"})
    t.c("bedrock-agentcore-control")
    t.c("bedrock-agentcore-control")
    assert seen == {"region_name": "eu-west-1", "aws_access_key_id": "A", "aws_secret_access_key": "S", "aws_session_token": "T", "profile_name": "p", "client": "bedrock-agentcore-control"}


def test_agentcore_error_without_a_code():
    t, f = agentcore()
    f.fail["get_agent_runtime"] = RuntimeError("socket closed")
    with pytest.raises(T.TargetError, match="get_agent_runtime failed: socket closed"):
        t.status({"runtime_id": "x", "version": "1"})


# ---- Foundry
FOUNDRY = {"project_endpoint": "https://acct.services.ai.azure.com/api/projects/proj", "tenant_id": "t", "client_id": "c"}


def foundry(handler, **kw):
    T.TRANSPORT = httpx.MockTransport(handler)
    return T.Foundry(FOUNDRY, {"client_secret": "s"}, token=lambda: "tok", **kw)


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    T.TRANSPORT = None


def test_foundry_deploy_status_route_invoke_delete():
    seen: list = []

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content[:1] == b"{" else req.content
        seen.append((req.method, req.url.path, dict(req.url.params), body, req.headers["authorization"]))
        p = req.url.path
        if req.method == "POST" and p.endswith("/versions"):
            return httpx.Response(200, json={"name": "as-bot", "version": "3"})
        if req.method == "GET" and p.endswith("/versions/3"):
            return httpx.Response(200, json={"status": seen_status[0], "error": seen_status[1]})
        if req.method == "GET" and p.endswith("/agents"):
            return httpx.Response(200, json={"data": [{}, {}]})
        if req.method == "PATCH":
            return httpx.Response(200, json={})
        if p.endswith("/responses"):
            return httpx.Response(200, json={"output_text": "azure says hi"})
        return httpx.Response(204)
    seen_status = ["creating", None]
    t = foundry(h)
    ref = t.deploy(SPEC, "as-bot", "reg.azurecr.io/a:b", {"K": "v"}, None, None)
    m, path, params, body, auth = seen[-1]
    assert path == "/api/projects/proj/agents/as-bot/versions" and params == {"api-version": "v1"} and auth == "Bearer tok" and "draft" not in body
    assert body["definition"] == {"kind": "hosted", "container_configuration": {"image": "reg.azurecr.io/a:b"}, "cpu": "1", "memory": "2Gi",
                                  "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}], "environment_variables": {"K": "v", "PORT": "8088"}}
    assert ref == {"agent": "as-bot", "version": "3", "slot": None}
    t.deploy(SPEC, "as-bot", "img", {}, ref, None)
    assert seen[-1][3]["draft"] is True                    # a candidate next to the live version
    assert t.status(ref) == {"state": "creating", "detail": "creating"}
    seen_status[:] = ["active", None]
    assert t.status(ref)["state"] == "ready"
    seen_status[:] = ["failed", {"message": "image pull failed"}]
    assert t.status(ref) == {"state": "failed", "detail": "image pull failed"}
    seen_status[:] = ["failed", "plain error"]
    assert t.status(ref)["detail"] == "plain error"
    seen_status[:] = ["deleted", None]
    assert t.status(ref)["state"] == "gone"
    assert t.route(ref, "shadow")["slot"] == "shadow" and seen[-1][0] != "PATCH"
    live = t.route(ref, "live")
    m, path, params, body, auth = seen[-1]
    assert m == "PATCH" and path.endswith("/agents/as-bot") and body["agent_endpoint"]["version_selector"]["version_selection_rules"] == [{"type": "FixedRatio", "agent_version": "3", "traffic_percentage": 100}]
    assert t.invoke(live, "hi") == "azure says hi" and seen[-1][1].endswith("/agents/as-bot/endpoint/protocols/openai/responses") and seen[-1][3] == {"input": "hi", "stream": False}
    with pytest.raises(T.TargetError, match="receives no traffic"):
        t.invoke(ref, "hi")
    t.delete(ref)
    assert seen[-1][0] == "DELETE" and seen[-1][1].endswith("/versions/3")
    t.delete(ref, whole=True)
    assert seen[-1][1].endswith("/agents/as-bot")
    assert "2 agent(s) visible" in t.check()["detail"]


def test_foundry_token_and_http_errors(monkeypatch):
    calls = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "login.microsoftonline.com" in str(req.url):
            return httpx.Response(200, json={"access_token": "entra-token"}) if b"client_secret=s" in req.content else httpx.Response(400, text="bad secret")
        return httpx.Response(200, json={"status": "active"})
    T.TRANSPORT = httpx.MockTransport(h)
    t = T.Foundry(FOUNDRY, {"client_secret": "s"})
    assert t._fetch_token() == "entra-token" and "https://login.microsoftonline.com/t/oauth2/v2.0/token" in calls
    assert t.status({"agent": "a", "version": "1"})["state"] == "ready"
    bad = T.Foundry(FOUNDRY, {"client_secret": "wrong"})
    with pytest.raises(T.TargetError, match="Entra refused"):
        bad.status({"agent": "a", "version": "1"})

    def codes(code, **kw):
        t2 = foundry(lambda r: httpx.Response(code, **kw))
        return t2
    with pytest.raises(T.TargetError, match="refused the credentials"):
        codes(403).check()
    with pytest.raises(T.TargetError, match="not found"):
        codes(404).check()
    with pytest.raises(T.TargetError, match="API error 500: kaboom"):
        codes(500, json={"error": {"message": "kaboom"}}).check()
    with pytest.raises(T.TargetError, match="API error 500: top"):
        codes(500, json={"error": "top"}).check()
    with pytest.raises(T.TargetError, match="API error 500: oops"):
        codes(500, text="oops").check()
    with pytest.raises(T.TargetError, match="not JSON"):
        codes(200, text="not json").check()
    assert codes(200, content=b"").req("GET", "http://x") == {}

    def down(req):
        raise httpx.ConnectError("down")
    with pytest.raises(T.TargetError, match="cannot reach the API"):
        foundry(down).check()
    t3 = T.Foundry(FOUNDRY, {"client_secret": "s"}, token=lambda: (_ for _ in ()).throw(ValueError("expired")))
    with pytest.raises(T.TargetError, match="credentials could not be used"):
        t3.check()
    t4 = T.Foundry(FOUNDRY, {"client_secret": "s"}, token=lambda: (_ for _ in ()).throw(T.TargetError("own message")))
    with pytest.raises(T.TargetError, match="own message"):
        t4.check()
    with pytest.raises(NotImplementedError):
        T.Http.__new__(T.Http)._fetch_token()


# ---- Vertex
VERTEX = {"project": "p", "region": "us-central1", "credentials_file": "/k.json"}


def vertex(handler, extra=None):
    T.TRANSPORT = httpx.MockTransport(handler)
    return T.Vertex({**VERTEX, **(extra or {})}, {}, token=lambda: "gtok")


def test_vertex_deploy_status_invoke_delete():
    seen: list = []
    state = {"op": {"done": False}}

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, dict(req.url.params), json.loads(req.content) if req.content else None))
        p = req.url.path
        if req.method == "POST" and p.endswith("/reasoningEngines"):
            return httpx.Response(200, json={"name": "projects/p/locations/us-central1/operations/op1"})
        if "/operations/" in p and req.method == "GET":
            return httpx.Response(200, json=state["op"])
        if p.endswith(":query"):
            return httpx.Response(200, json={"output": {"response": "vertex says hi"}})
        if req.method == "GET" and p.endswith("/reasoningEngines"):
            return httpx.Response(200, json={"reasoningEngines": [{}]})
        return httpx.Response(200, json={})
    t = vertex(h)
    ref = t.deploy({**SPEC, "description": "does things"}, "as-bot-v1", "us-docker.pkg.dev/p/r/a:b", {"K": "v"}, None, None)
    m, path, params, body = seen[-1]
    assert path == "/v1/projects/p/locations/us-central1/reasoningEngines" and body["displayName"] == "as-bot-v1" and body["description"] == "does things"
    assert body["spec"]["containerSpec"] == {"imageUri": "us-docker.pkg.dev/p/r/a:b", "port": 8080} and body["spec"]["deploymentSpec"] == {"env": [{"name": "K", "value": "v"}]} and "serviceAccount" not in body["spec"]
    assert ref == {"operation": "projects/p/locations/us-central1/operations/op1", "engine": "", "slot": None}
    assert t.status(ref) == {"state": "creating", "detail": "creating the engine"}
    state["op"] = {"done": True, "error": {"message": "image not found"}}
    assert t.status(ref) == {"state": "failed", "detail": "image not found"}
    state["op"] = {"done": True, "response": {}}
    assert t.status(ref)["state"] == "failed" and "without an engine" in t.status(ref)["detail"]
    state["op"] = {"done": True, "response": {"name": "projects/p/locations/us-central1/reasoningEngines/9"}}
    st = t.status(ref)
    assert st["state"] == "ready" and st["ref"]["engine"].endswith("/reasoningEngines/9")
    assert t.status(st["ref"]) == {"state": "ready", "detail": "engine created"}
    assert t.route(st["ref"], "live")["slot"] == "live"
    assert t.invoke(st["ref"], "hi") == "vertex says hi" and seen[-1][3] == {"input": {"prompt": "hi"}}
    with pytest.raises(T.TargetError, match="not ready yet"):
        t.invoke(ref, "hi")
    t.delete(st["ref"])
    assert seen[-1][0] == "DELETE" and seen[-1][2] == {"force": "true"}
    n = len(seen)
    t.delete(ref)                                          # no engine yet: nothing to delete
    assert len(seen) == n
    assert "1 engine(s) visible" in t.check()["detail"]
    t2 = vertex(h, {"service_account": "sa@p.iam"})
    t2.deploy(SPEC, "n", "img", {}, None, None)
    assert seen[-1][3]["spec"]["serviceAccount"] == "sa@p.iam"


def test_vertex_uses_a_service_account_key(monkeypatch):
    import google.auth.transport.requests as gtr
    from google.oauth2 import service_account

    class Creds:
        token = None

        def refresh(self, request):
            Creds.token = "fresh"
    seen = {}

    def load(path, scopes):
        seen.update(path=path, scopes=scopes)
        return Creds()
    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", staticmethod(load))
    monkeypatch.setattr(gtr, "Request", lambda: object())
    t = T.Vertex(VERTEX, {})
    assert t._fetch_token() == "fresh" and seen == {"path": "/k.json", "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}


# ------------------------------------------------------------------------------------------------------------------- the service
class FakeTarget(T.Target):
    id, label, platform = "agentcore", "Fake cloud", "linux/arm64"
    cost_note = "costs"

    def __init__(self):
        self.calls, self.state, self.fail, self.answer, self.n = [], "ready", {}, "ok", 0

    def _do(self, op, *a):
        self.calls.append((op, *a))
        if op in self.fail:
            raise self.fail[op]

    def deploy(self, spec, name, image, env, existing, folder):
        self._do("deploy", name, image, dict(env), existing)
        self.n += 1
        return {"id": name, "version": str(self.n), "slot": None}

    def status(self, ref):
        self._do("status", ref["version"])
        return {"state": self.state, "detail": f"is {self.state}"}

    def route(self, ref, slot):
        self._do("route", ref["version"], slot)
        return {**ref, "slot": slot}

    def invoke(self, ref, text):
        self._do("invoke", ref["version"], text)
        return self.answer

    def delete(self, ref, whole=False):
        self._do("delete", ref["version"], whole)

    def check(self):
        self._do("check")
        return {"ok": True, "detail": "fine"}


@pytest.fixture
def rt(state, tmp_path):
    ft = FakeTarget()
    svc = S.RuntimeService(state.db, state.agents, lambda a: "openai/m", tmp_path / "home", factory=lambda tid, cfg, sec: ft)
    svc.set_config({"targets": {"agentcore": {"enabled": True, "region": "us-east-1", "role_arn": "arn:role"}}, "image_template": "reg.io/agents:{agent}-{tag}",
                    "secrets": {"model_env": {"OPENAI_API_KEY": "sk-test"}}})
    state.agents.create(AgentDef(id="bot", name="Bot", description="Answers.", instruction="Be brief."))
    svc.ft = ft
    return svc


def test_compat_report_lists_what_a_runtime_cannot_do():
    assert S.compat(AgentDef(id="a", name="a"), "local") == []
    plain = {c["feature"] for c in S.compat(AgentDef(id="a", name="a", guardrails=["none"]), "agentcore")}
    assert plain == {"spend"}
    full = AgentDef(id="a", name="A", tools=["read_file"], mcp=["git"], sub_agents=["x"], rag=["kb"], data=["d"], memory={"types": ["semantic"]}, skills=["s"], framework="langgraph",
                    acp_agents=["claude"])
    rep = S.compat(full, "vertex")
    assert {c["feature"] for c in rep} == {"external agents", "tools", "sub-agents", "knowledge", "data", "memory", "skills", "guardrails", "framework", "spend"}
    assert [c["level"] for c in rep if c["feature"] == "external agents"] == ["block"] and sum(c["feature"] == "tools" for c in rep) == 2
    assert any(c["feature"] == "sub-agents" for c in S.compat(AgentDef(id="a", name="a", can_spawn=True), "docker"))


def test_config_validation_and_public_view_hides_secrets(rt):
    pub = rt.public()
    ac = next(t for t in pub["targets"] if t["id"] == "agentcore")
    assert ac["enabled"] and ac["configured"] and ac["values"] == {"region": "us-east-1", "role_arn": "arn:role"} and ac["secrets_set"] == []
    assert pub["model_env_set"] == ["OPENAI_API_KEY"] and "sk-test" not in json.dumps(pub)
    local = next(t for t in pub["targets"] if t["id"] == "local")
    assert local["enabled"] and local["configured"]
    vertex = next(t for t in pub["targets"] if t["id"] == "vertex")
    assert not vertex["enabled"] and not vertex["configured"] and "Settings > Runtimes" in vertex["problem"]
    for bad, msg in [("nope", "must be an object"), ({"image_template": 5}, "image template"), ({"image_template": "bad image"}, "image template"),
                     ({"targets": {"x": {}}}, "unknown runtime target"), ({"targets": {"local": {}}}, "unknown runtime target"), ({"targets": {"vertex": "s"}}, "must be an object"),
                     ({"targets": {"vertex": {"nope": "1"}}}, "has no setting nope"), ({"targets": {"vertex": {"project": 5}}}, "text of at most 300"),
                     ({"targets": {"vertex": {"project": "x" * 301}}}, "text of at most 300"), ({"targets": {"vertex": {"enabled": "yes"}}}, "true or false"),
                     ({"secrets": {"model_env": {"lower": "x"}}}, "upper-case"), ({"secrets": {"model_env": "x"}}, "upper-case"),
                     ({"secrets": {"vertex": {"project": "x"}}}, "not secret settings"), ({"secrets": {"nope": {}}}, "not secret settings")]:
        with pytest.raises(S.RuntimeFault, match=msg):
            rt.set_config(bad)
    rt.set_config({"secrets": {"agentcore": {"access_key_id": "AKIA"}, "model_env": {"ANTHROPIC_API_KEY": "k2"}}, "clear_model_env": ["OPENAI_API_KEY"], "image_template": ""})
    pub = rt.public()
    assert pub["model_env_set"] == ["ANTHROPIC_API_KEY"] and next(t for t in pub["targets"] if t["id"] == "agentcore")["secrets_set"] == ["access_key_id"] and pub["image_template"] == ""
    rt.set_config({"secrets": {"agentcore": {"access_key_id": ""}}})                       # an empty value clears a secret
    assert next(t for t in rt.public()["targets"] if t["id"] == "agentcore")["secrets_set"] == []


def test_bad_stored_settings_fall_back_to_defaults(state, tmp_path):
    state.db.set_setting(S.CONFIG_KEY, "{not json")
    state.db.set_setting(S.SECRETS_KEY, "[1]")
    svc = S.RuntimeService(state.db, state.agents, lambda a: "m", tmp_path)
    assert svc.config() == {"targets": {}, "image_template": ""} and svc._secrets() == {}
    state.db.set_setting(S.SECRETS_KEY, "{bad")
    assert svc._secrets() == {}


def test_plan_shows_compat_diff_bundle_and_commands(rt):
    p = rt.plan("bot", "agentcore")
    assert p["ready"] and not p["blocked"] and p["image"].startswith("reg.io/agents:bot-") and p["image_ok"] and not p["shadow"] and p["live"] is None
    assert p["info"]["label"] == "Amazon Bedrock AgentCore Runtime" and p["cost_note"] and p["side_effects"] and p["commands"][1].startswith("docker build --platform linux/arm64")
    assert (rt.home / "runtime-bundles" / "bot" / p["spec_hash"][:12] / "agent.json").exists() and p["bundle"]["files"][0] == "Dockerfile"
    assert rt.plan("bot", "agentcore", "my/own:image")["image"] == "my/own:image"
    assert rt.plan("bot", "docker")["image"].startswith("agent-studio-rt-bot:")
    off = rt.plan("bot", "vertex")
    assert not off["ready"] and "switched off" in off["problem"] and off["commands"][0].startswith("gcloud auth")
    rt.set_config({"image_template": ""})
    assert rt.plan("bot", "agentcore")["image"] == "" and not rt.plan("bot", "agentcore")["image_ok"] and rt.plan("bot", "agentcore")["commands"] == []
    with pytest.raises(S.RuntimeFault, match="no such agent"):
        rt.plan("ghost", "agentcore")
    with pytest.raises(S.RuntimeFault, match="other than 'local'"):
        rt.plan("bot", "local")
    rt.model_for = lambda a: (_ for _ in ()).throw(ValueError("no provider"))
    with pytest.raises(S.RuntimeFault, match="choose a model provider"):
        rt.plan("bot", "agentcore")


def test_deploy_needs_confirmation_and_refuses_what_cannot_work(rt, state):
    with pytest.raises(S.RuntimeFault, match="Confirm to continue"):
        rt.deploy("bot", "agentcore")
    state.agents.update("bot", {"acp_agents": ["claude"]})
    with pytest.raises(S.RuntimeFault, match="cannot be deployed there: Coding agents"):
        rt.deploy("bot", "agentcore", confirm=True)
    state.agents.update("bot", {"acp_agents": []})
    with pytest.raises(S.RuntimeFault, match="switched off"):
        rt.deploy("bot", "vertex", confirm=True)
    rt.set_config({"image_template": ""})
    with pytest.raises(S.RuntimeFault, match="give the image to deploy"):
        rt.deploy("bot", "agentcore", confirm=True)
    rt.ft.fail["deploy"] = T.TargetError("quota exceeded")
    with pytest.raises(S.RuntimeFault, match="quota exceeded"):
        rt.deploy("bot", "agentcore", "reg.io/a:1", confirm=True)
    assert rt.releases("bot") == []


def test_first_deploy_goes_live_by_itself_then_shadow_switch_and_rollback(rt, state):
    r1 = rt.deploy("bot", "agentcore", confirm=True)
    assert r1["state"] == "deploying" and not r1["live"] and not r1["shadow"] and r1["version"] == 1
    assert rt.ft.calls[0][0] == "deploy" and rt.ft.calls[0][3] == {"OPENAI_API_KEY": "sk-test"} and rt.ft.calls[0][1] == "as_bot" and rt.ft.calls[0][4] is None
    rt.ft.state = "creating"
    assert rt.refresh(r1["id"])["state"] == "deploying"
    rt.ft.state = "ready"
    live = rt.refresh(r1["id"])                                 # ready + first release -> goes live by itself
    assert live["live"] and live["state"] == "ready" and live["ref"]["slot"] == "live" and state.agents.get("bot").runtime == "agentcore"
    assert rt.live("bot")["id"] == r1["id"]
    m = rt.migration("bot")
    assert not m["stale"] and m["live"]["id"] == r1["id"] and "matches" in m["message"] and m["candidate"] is None
    state.agents.update("bot", {"instruction": "Be very brief."})                 # edit after deploying
    m = rt.migration("bot")
    assert m["stale"] and [c["field"] for c in m["changes"]] == ["instruction"] and "shadow" in m["message"]
    r2 = rt.deploy("bot", "agentcore", confirm=True)                                   # shadow next to the live one
    assert r2["shadow"] and r2["version"] == 2 and rt.ft.calls[-1][4]["version"] == "1"
    assert rt.migration("bot")["candidate"]["id"] == r2["id"]
    with pytest.raises(S.RuntimeFault, match="not ready"):
        rt.ft.state = "creating"
        rt.refresh(r2["id"])
        rt.promote(r2["id"], confirm=True)
    rt.ft.state = "ready"
    h = rt.health(r2["id"])
    assert h["ok"] and h["state"] == "ready" and not h.get("smoke")
    h = rt.health(r2["id"], smoke=True)
    assert h["ok"] and h["smoke"] and "answered: ok" in h["detail"] and rt.ft.calls[-2][:3] == ("route", "2", "shadow")
    assert rt.releases("bot")[0]["health"]["smoke"]
    rt.ft.answer = "  "
    assert not rt.health(r2["id"], smoke=True)["ok"] and "nothing" in rt.health(r2["id"], smoke=True)["detail"]
    rt.ft.answer = "ok"
    n = len([c for c in rt.ft.calls if c[0] == "route"])
    rt.health(r2["id"], smoke=True)
    assert len([c for c in rt.ft.calls if c[0] == "route"]) == n                         # already exposed: not routed again
    rt.ft.fail["invoke"] = T.TargetError("500 from runtime")
    h = rt.health(r2["id"], smoke=True)
    assert not h["ok"] and h["smoke"] and "500" in h["detail"]
    del rt.ft.fail["invoke"]
    with pytest.raises(S.RuntimeFault, match="Confirm"):
        rt.promote(r2["id"])
    with pytest.raises(S.RuntimeFault, match="already live"):
        rt.promote(r1["id"], confirm=True)
    p = rt.promote(r2["id"], confirm=True)
    assert p["live"] and not p["shadow"] and p["prev_id"] == r1["id"] and rt.live("bot")["id"] == r2["id"]
    old = next(r for r in rt.releases("bot") if r["id"] == r1["id"])
    assert not old["live"] and old["state"] == "retired"
    assert not rt.migration("bot")["stale"]
    with pytest.raises(S.RuntimeFault, match="Confirm"):
        rt.rollback("bot")
    back = rt.rollback("bot", confirm=True)
    assert back["id"] == r1["id"] and back["live"] and back["state"] == "ready" and rt.live("bot")["id"] == r1["id"]
    assert rt.live("bot")["prev_id"] == r2["id"]
    assert rt.refresh(r2["id"])["state"] == "retired"                                   # a retired release is left alone
    assert rt.summary() == {"bot": {"target": "agentcore", "deployed": True, "version": 1}}


def test_rollback_and_delete_guards(rt, state):
    r1 = rt.deploy("bot", "agentcore", confirm=True)
    rt.refresh(r1["id"])
    with pytest.raises(S.RuntimeFault, match="no earlier version"):
        rt.rollback("bot", confirm=True)
    with pytest.raises(S.RuntimeFault, match="no earlier version"):
        rt.rollback("nobody", confirm=True)
    r2 = rt.deploy("bot", "agentcore", confirm=True)
    rt.refresh(r2["id"])
    rt.promote(r2["id"], confirm=True)
    rt.db.execute("UPDATE runtime_releases SET state='deleted' WHERE id=?", (r1["id"],))
    with pytest.raises(S.RuntimeFault, match="no longer exists"):
        rt.rollback("bot", confirm=True)
    rt.db.execute("UPDATE runtime_releases SET state='retired' WHERE id=?", (r1["id"],))
    rt.ft.fail["route"] = T.TargetError("platform said no")
    with pytest.raises(S.RuntimeFault, match="platform said no"):
        rt.rollback("bot", confirm=True)
    del rt.ft.fail["route"]
    r3 = rt.deploy("bot", "agentcore", confirm=True)
    rt.refresh(r3["id"])
    rt.ft.fail["route"] = T.TargetError("platform said no")
    with pytest.raises(S.RuntimeFault, match="platform said no"):
        rt.promote(r3["id"], confirm=True)
    del rt.ft.fail["route"]
    with pytest.raises(S.RuntimeFault, match="Confirm"):
        rt.delete_release(r1["id"])
    with pytest.raises(S.RuntimeFault, match="live: switch traffic"):
        rt.delete_release(r2["id"], confirm=True)
    gone = rt.delete_release(r1["id"], confirm=True)
    assert gone["state"] == "deleted" and rt.ft.calls[-1] == ("delete", "1", False)
    assert all(r["id"] != r1["id"] for r in rt.releases("bot"))
    for r in rt.releases("bot"):
        if not r["live"]:
            rt.delete_release(r["id"], confirm=True)
    only = rt.live("bot")
    done = rt.delete_release(only["id"], confirm=True)                                     # the last one: the whole runtime goes
    assert done["state"] == "deleted" and rt.ft.calls[-1][2] is True and state.agents.get("bot").runtime == "local"
    rt.ft.fail["delete"] = T.TargetError("not found", "notfound")
    r5 = rt.deploy("bot", "agentcore", confirm=True)
    assert rt.delete_release(r5["id"], confirm=True)["state"] == "deleted"                 # already gone on the platform: fine
    rt.ft.fail["delete"] = T.TargetError("denied", "denied")
    r4 = rt.deploy("bot", "agentcore", confirm=True)
    with pytest.raises(S.RuntimeFault, match="denied"):
        rt.delete_release(r4["id"], confirm=True)
    with pytest.raises(S.RuntimeFault, match="no such deployment"):
        rt.delete_release("nope", confirm=True)


def test_refresh_maps_platform_states_and_errors(rt):
    r = rt.deploy("bot", "agentcore", confirm=True)
    rt.ft.state = "failed"
    assert rt.refresh(r["id"])["state"] == "failed" and rt.refresh(r["id"])["detail"] == "is failed"
    rt.ft.state = "gone"
    assert rt.refresh(r["id"])["state"] == "deleted"
    r2 = rt.deploy("bot", "agentcore", confirm=True)
    rt.ft.fail["status"] = T.TargetError("api down")
    with pytest.raises(S.RuntimeFault, match="api down"):
        rt.refresh(r2["id"])
    rt.set_config({"targets": {"agentcore": {"enabled": False}}})
    del rt.ft.fail["status"]
    with pytest.raises(S.RuntimeFault, match="switched off"):
        rt.refresh(r2["id"])


def test_a_target_can_update_state_through_its_ref(rt):
    r = rt.deploy("bot", "agentcore", confirm=True)
    rt.ft.status = lambda ref: {"state": "ready", "detail": "done", "ref": {**ref, "engine": "e1"}}
    out = rt.refresh(r["id"])
    assert out["ref"]["engine"] == "e1" and out["live"]


def test_test_button_reports_instead_of_raising(rt):
    assert rt.test("agentcore") == {"ok": True, "detail": "fine"}
    assert not rt.test("vertex")["ok"] and "switched off" in rt.test("vertex")["detail"]
    rt.factory = lambda *a: (_ for _ in ()).throw(T.TargetError("bad creds"))
    assert rt.test("agentcore") == {"ok": False, "detail": "bad creds"}
    with pytest.raises(S.RuntimeFault, match="bad creds"):
        rt._target("agentcore")
    with pytest.raises(S.RuntimeFault, match="other than 'local'"):
        rt._target("local")


def test_migration_messages_and_invoke(rt, state):
    m = rt.migration("bot")
    assert m["runtime"] == "local" and "runs on this PC" in m["message"] and m["compat"] == []
    state.agents.update("bot", {"runtime": "agentcore"})
    m = rt.migration("bot")
    assert m["live"] is None and "no live deployment" in m["message"] and m["compat"]
    with pytest.raises(S.RuntimeFault, match="no live deployment"):
        rt.invoke("bot", "hi")
    r = rt.deploy("bot", "agentcore", confirm=True)
    rt.refresh(r["id"])
    assert rt.invoke("bot", "hello") == "ok" and rt.ft.calls[-1] == ("invoke", "1", "hello")
    rt.ft.fail["invoke"] = T.TargetError("runtime crashed")
    with pytest.raises(S.RuntimeFault, match="runtime crashed"):
        rt.invoke("bot", "hello")
    with pytest.raises(S.RuntimeFault, match="no such agent"):
        rt.migration("ghost")


def test_release_names_per_target(rt):
    a = AgentDef(id="my_bot", name="x")
    assert rt._names("agentcore", a, 2) == "as_my_bot" and rt._names("foundry", a, 2) == "as-my-bot" and rt._names("vertex", a, 2) == "as-my-bot-v2"
    assert rt._names("docker", a, 2) == "agent-studio-rt-my_bot-v2"


# ------------------------------------------------------------------------------------------------------------------ the HTTP API
def test_api_flow_and_agent_runtime_validation(client, state, monkeypatch):
    ft = FakeTarget()
    state.runtimes.factory = lambda tid, cfg, sec: ft
    assert {t["id"] for t in client.get("/api/runtimes").json()["targets"]} == {"local", "docker", "agentcore", "vertex", "foundry"}
    cfg = {"targets": {"agentcore": {"enabled": True, "region": "r", "role_arn": "arn:x"}}, "image_template": "reg.io/a:{agent}-{tag}"}
    assert client.put("/api/runtimes/config", json=cfg).status_code == 200
    assert client.put("/api/runtimes/config", json={"targets": {"zzz": {}}}).status_code == 422
    assert client.post("/api/runtimes/targets/agentcore/test").json()["ok"]
    client.post("/api/agents", json={"id": "api_bot", "name": "Api Bot"})
    plan = client.post("/api/runtimes/agents/api_bot/plan", json={"target": "agentcore"}).json()
    assert plan["ready"] and plan["compat"]
    assert client.post("/api/runtimes/agents/ghost/plan", json={"target": "agentcore"}).status_code == 404
    assert client.post("/api/runtimes/agents/api_bot/deploy", json={"target": "agentcore"}).status_code == 422
    rel = client.post("/api/runtimes/agents/api_bot/deploy", json={"target": "agentcore", "confirm": True, "image": plan["image"]}).json()
    assert rel["state"] == "deploying"
    assert client.post(f"/api/runtimes/releases/{rel['id']}/refresh").json()["live"]
    assert client.post(f"/api/runtimes/releases/{rel['id']}/health", json={"smoke": True}).json()["smoke"]
    st = client.get("/api/runtimes/agents/api_bot").json()
    assert st["releases"][0]["id"] == rel["id"] and st["migration"]["live"]["id"] == rel["id"]
    assert client.get("/api/agents").json()[-1]["runtime"] == "agentcore"
    r2 = client.post("/api/runtimes/agents/api_bot/deploy", json={"target": "agentcore", "confirm": True, "image": "reg.io/a:2"}).json()
    client.post(f"/api/runtimes/releases/{r2['id']}/refresh")
    assert client.post(f"/api/runtimes/releases/{r2['id']}/promote", json={}).status_code == 422
    assert client.post(f"/api/runtimes/releases/{r2['id']}/promote", json={"confirm": True}).json()["live"]
    assert client.post("/api/runtimes/agents/api_bot/rollback", json={"confirm": True}).json()["id"] == rel["id"]
    assert client.delete(f"/api/runtimes/releases/{r2['id']}").status_code == 422
    assert client.delete(f"/api/runtimes/releases/{r2['id']}?confirm=true").json()["state"] == "deleted"
    assert client.put("/api/agents/api_bot", json={"runtime": "mars"}).status_code == 422
    assert client.put("/api/agents/api_bot", json={"runtime": "local"}).json()["runtime"] == "local"


def test_a_deployed_agent_answers_the_chat_through_its_runtime(client, state):
    ft = FakeTarget()
    ft.answer = "hello from the cloud"
    state.runtimes.factory = lambda tid, cfg, sec: ft
    client.put("/api/runtimes/config", json={"targets": {"agentcore": {"enabled": True, "region": "r", "role_arn": "arn:x"}}, "image_template": "reg.io/a:{agent}-{tag}"})
    client.post("/api/agents", json={"id": "cloud_bot", "name": "Cloud Bot"})
    local = client.post("/api/chat", json={"provider": "mock", "agent": "cloud_bot", "message": "first"}).json()
    assert local["model"] != "runtime:agentcore"                         # not deployed yet: answered on this PC
    rel = client.post("/api/runtimes/agents/cloud_bot/deploy", json={"target": "agentcore", "confirm": True}).json()
    client.post(f"/api/runtimes/releases/{rel['id']}/refresh")
    r = client.post("/api/chat", json={"provider": "mock", "agent": "cloud_bot", "message": "second", "session_id": local["session_id"]}).json()
    assert r["reply"] == "hello from the cloud" and r["model"] == "runtime:agentcore" and any("billed there" in w for w in r["warnings"]) and r["usage"]["cost_usd"] == 0
    sent = ft.calls[-1][2]
    assert sent.startswith("Conversation so far:\nUser: first") and sent.endswith("User: second")
    ft.fail["invoke"] = T.TargetError("runtime exploded")
    bad = client.post("/api/chat", json={"provider": "mock", "agent": "cloud_bot", "message": "third", "session_id": local["session_id"]})
    assert bad.status_code >= 400
    assert S.RuntimeService.public_release(state.runtimes.live("cloud_bot"))["target"] == "agentcore"


def test_history_text_and_streaming_events():
    from app.runtimes.turn import history_text
    assert history_text([], "hi") == "hi"
    rows = [{"role": "user", "text": f"q{i}"} for i in range(20)]
    out = history_text(rows, "now")
    assert "q7" not in out and "q8" in out and out.endswith("User: now")


def test_runner_module_is_runnable_standalone(tmp_path):
    """The file copied into the bundle works on its own (no import of the rest of Agent Studio)."""
    f = tmp_path / "runner.py"
    f.write_text(CR.__file__ and open(CR.__file__, encoding="utf-8").read(), encoding="utf-8")
    (tmp_path / "agent.json").write_text(json.dumps(SPEC))
    code = ("import runner; from fastapi.testclient import TestClient; "
            "c = TestClient(runner.build_app(completion=lambda **k: {'choices': [{'message': {'content': 'ok'}}]})); print(c.post('/invocations', json={'prompt': 'x'}).json()['response'])")
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, env={**__import__("os").environ, "AGENT_SPEC": str(tmp_path / "agent.json")})
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr[-500:]


def test_remote_turn_streams_events_and_survives_cancel(state):
    import asyncio
    ft = FakeTarget()
    ft.answer = "streamed answer"
    state.runtimes.factory = lambda tid, cfg, sec: ft
    state.runtimes.set_config({"targets": {"agentcore": {"enabled": True, "region": "r", "role_arn": "arn:x"}}, "image_template": "reg.io/a:{agent}-{tag}"})
    state.agents.create(AgentDef(id="stream_bot", name="S"))
    rel = state.runtimes.deploy("stream_bot", "agentcore", confirm=True)
    state.runtimes.refresh(rel["id"])
    events: list = []
    out = asyncio.run(state.chat.send("mock", None, "hello", None, None, "stream_bot", None, emit=events.append))
    assert out["reply"] == "streamed answer"
    assert [e["type"] for e in events if e["type"] in ("session", "text")] == ["session", "text"]

    async def hang(*a):
        await asyncio.sleep(30)

    async def go():
        import app.runtimes.turn as RT
        orig = state.runtimes.invoke
        state.runtimes.invoke = lambda *a: __import__("time").sleep(5)
        t = asyncio.ensure_future(state.chat.send("mock", None, "x", None, None, "stream_bot", None))
        await asyncio.sleep(0.4)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        state.runtimes.invoke = orig
    asyncio.run(go())


def test_model_resolver_picks_the_provider_and_refuses_when_none(state):
    resolve = S.model_resolver(state.providers)
    assert resolve(AgentDef(id="a", name="a", provider="mock", tier="flash")) == "openai/mock-cheap"
    assert resolve(AgentDef(id="a", name="a")).startswith("openai/mock")
    state.db.execute("UPDATE providers SET enabled=0")
    with pytest.raises(ValueError, match="no model provider"):
        resolve(AgentDef(id="a", name="a"))


def test_the_test_button_reports_a_missing_setup(rt):
    rt.ft.fail["check"] = T.TargetError("no permission")
    assert rt.test("agentcore") == {"ok": False, "detail": "no permission"}
    rt.factory = lambda *a: (_ for _ in ()).throw(S.RuntimeFault("not set up"))
    assert rt.test("agentcore") == {"ok": False, "detail": "not set up"}
