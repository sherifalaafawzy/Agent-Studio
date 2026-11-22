"""Where an agent can run: the runtime targets (abstract factory). Every target deploys the same container bundle (see bundle.py) and
speaks one small interface: deploy / status / route / invoke / delete.

  local       the default: Agent Studio runs the agent itself (nothing to deploy).
  docker      a container on this PC's Docker (the bundle is built here, run on 127.0.0.1 with a private token).
  agentcore   Amazon Bedrock AgentCore Runtime. boto3 `bedrock-agentcore-control` create/update_agent_runtime (every update is a new
              immutable version), named endpoints ("live", "shadow") point at a version; `bedrock-agentcore` invoke_agent_runtime.
              Image must be linux/arm64, listen on 8080 and answer POST /invocations (checked against botocore's service model and the
              AgentCore developer guide, 2026-11-22).
  foundry     Microsoft Foundry hosted agents. REST `{project}/agents/{name}/versions?api-version=v1` with a "hosted" definition;
              traffic is a FixedRatio version selector set with PATCH /agents/{name} (merge-patch); draft versions get no traffic. Image must
              be linux/amd64 and listen on 8088 (the docs' port); Entra token for https://ai.azure.com.
  vertex      Vertex AI Agent Engine. REST v1 `reasoningEngines` create with spec.containerSpec {imageUri, port}; every release is its own
              engine (the API has no traffic split), so shadow/rollback are pointer switches in Agent Studio.

Credentials arrive as secret fields (stored write-only); HTTP, SDK and Docker access are injectable so tests use fakes.
Verified against the vendors' references; NOT verified: the Vertex `:query` request shape for container agents.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from typing import Any, Callable

import httpx

from .bundle import PORT

TRANSPORT: httpx.BaseTransport | None = None          # tests inject httpx.MockTransport
TIMEOUT = 60
DOCKER_RUN: Callable[[list[str], float], subprocess.CompletedProcess] | None = None      # tests inject a fake docker


class TargetError(Exception):
    """Something to tell the user as is (missing setting, platform refusal, unreachable)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def F(name: str, label: str, secret: bool = False, required: bool = True, help: str = "", default: str = "") -> dict:
    return dict(name=name, label=label, secret=secret, required=required, help=help, default=default)


def docker(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    if DOCKER_RUN is not None:
        return DOCKER_RUN(args, timeout)
    exe = shutil.which("docker")
    if not exe:
        raise TargetError("the docker command was not found - install Docker Desktop and start it")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise TargetError("docker did not answer in time")


class Target:
    id = ""
    label = ""
    docs = ""
    remote = True
    cost_note = ""
    platform = "linux/amd64"
    fields: list[dict] = []
    side_effects: tuple[str, ...] = ()

    def __init__(self, cfg: dict, secrets: dict):
        self.cfg, self.sec = cfg, secrets
        self.val = {**{f["name"]: f["default"] for f in self.fields}, **{k: v for k, v in cfg.items() if v not in (None, "")}, **secrets}
        miss = [f["label"] for f in self.fields if f["required"] and not self.val.get(f["name"])]
        if miss:
            raise TargetError(f"{self.label} is not set up: missing {', '.join(miss)} (Settings > Runtimes)")

    # the interface ---------------------------------------------------------------------------------------------------------------
    def deploy(self, spec: dict, name: str, image: str, env: dict, existing: dict | None, folder: str | None) -> dict:
        raise NotImplementedError

    def status(self, ref: dict) -> dict:               # {"state": creating|ready|failed|gone, "detail": str, "ref": updated ref (optional)}
        raise NotImplementedError

    def route(self, ref: dict, slot: str) -> dict:     # slot "live" sends the traffic here; "shadow" makes it testable without traffic
        raise NotImplementedError

    def invoke(self, ref: dict, text: str) -> str:
        raise NotImplementedError

    def delete(self, ref: dict, whole: bool = False) -> None:
        raise NotImplementedError

    def check(self) -> dict:                           # a cheap read that proves the credentials work: {"ok": bool, "detail": str}
        raise NotImplementedError

    def info(self) -> dict:
        return dict(id=self.id, label=self.label, docs=self.docs, remote=self.remote, cost_note=self.cost_note, platform=self.platform,
                    side_effects=list(self.side_effects), fields=self.fields)


def reply_text(data: Any) -> str:
    """The answer inside whatever shape a runtime returned."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for k in ("response", "output_text", "text", "output", "answer"):
            if k in data:
                v = data[k]
                if k == "output" and isinstance(v, list):
                    return "".join(p.get("text", "") for m in v if isinstance(m, dict) for p in (m.get("content") or []) if isinstance(p, dict))
                return reply_text(v)
    return json.dumps(data)[:4000]


# ------------------------------------------------------------------------------------------------------------------- Docker
class DockerTarget(Target):
    id, label, remote, platform = "docker", "Docker container on this PC", False, "linux/amd64"
    docs = "https://docs.docker.com/reference/cli/docker/container/run/"
    cost_note = "Runs on this PC and uses its CPU and memory; the model calls are billed by your model provider."
    side_effects = ("Builds an image and starts a container on this PC's Docker.",)
    fields = [F("memory", "Memory limit", required=False, default="512m"), F("cpus", "CPU limit", required=False, default="1")]

    def deploy(self, spec, name, image, env, existing, folder):
        if not folder:
            raise TargetError("no bundle folder to build")
        r = docker(["build", "-t", image, folder], 600)
        if r.returncode:
            raise TargetError(f"docker build failed: {r.stderr.strip()[-300:]}")
        token = uuid.uuid4().hex
        args = ["run", "-d", "--name", name, "--label", "agent-studio-runtime=1", "-p", f"127.0.0.1::{PORT}", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", self.val["memory"], "--cpus", self.val["cpus"], "-e", f"RUNTIME_TOKEN={token}"]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        r = docker([*args, image], 120)
        if r.returncode:
            raise TargetError(f"docker run failed: {r.stderr.strip()[-300:]}")
        return dict(container=name, token=token, slot=None)

    def _port(self, ref: dict) -> str:
        r = docker(["port", ref["container"], f"{PORT}/tcp"], 15)
        line = (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and (r.stdout or "").strip() else ""
        if not line:
            raise TargetError("the container has no published port (is it running?)")
        return line.rsplit(":", 1)[1]

    def status(self, ref):
        r = docker(["inspect", "-f", "{{.State.Status}}", ref["container"]], 15)
        if r.returncode:
            return dict(state="gone", detail="the container no longer exists")
        st = r.stdout.strip()
        if st != "running":
            return dict(state="failed" if st in ("exited", "dead") else "creating", detail=f"container is {st}")
        try:
            ok = self._get(ref, "/ping")
        except TargetError as e:
            return dict(state="creating", detail=str(e))
        return dict(state="ready" if ok else "creating", detail="answering /ping" if ok else "started, not answering yet")

    def _http(self) -> httpx.Client:
        return httpx.Client(transport=TRANSPORT, timeout=TIMEOUT)

    def _get(self, ref: dict, path: str) -> bool:
        try:
            return self._http().get(f"http://127.0.0.1:{self._port(ref)}{path}").status_code == 200
        except httpx.HTTPError:
            return False

    def route(self, ref, slot):
        return {**ref, "slot": slot}

    def invoke(self, ref, text):
        try:
            r = self._http().post(f"http://127.0.0.1:{self._port(ref)}/invocations", json={"prompt": text}, headers={"Authorization": f"Bearer {ref['token']}"})
        except httpx.HTTPError as e:
            raise TargetError(f"the container did not answer ({type(e).__name__})")
        if r.status_code >= 400:
            raise TargetError(f"the container answered {r.status_code}: {r.text[:200]}")
        return reply_text(r.json())

    def delete(self, ref, whole=False):
        docker(["rm", "-f", ref["container"]], 30)

    def check(self):
        r = docker(["version", "--format", "{{.Server.Version}}"], 10)
        return dict(ok=r.returncode == 0, detail=f"Docker {r.stdout.strip()} is running" if r.returncode == 0 else "the Docker daemon is not running (start Docker Desktop)")


# ------------------------------------------------------------------------------------------------------ Amazon Bedrock AgentCore
class AgentCore(Target):
    id, label, platform = "agentcore", "Amazon Bedrock AgentCore Runtime", "linux/arm64"
    docs = "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-how-it-works.html"
    cost_note = "AgentCore Runtime bills for the CPU and memory used while sessions run (plus model calls); an idle runtime costs little but the image sits in ECR."
    side_effects = ("Creates an AgentCore runtime and named endpoints in your AWS account.", "The model key you give it is stored in the runtime's configuration.")
    fields = [F("region", "AWS region", help="e.g. us-east-1"), F("role_arn", "Execution role ARN", help="An IAM role AgentCore Runtime may assume (it needs to pull the image from ECR)."),
              F("network", "Network mode", required=False, default="PUBLIC"), F("profile", "AWS profile (optional)", required=False),
              F("access_key_id", "Access key id (optional)", secret=True, required=False), F("secret_access_key", "Secret access key (optional)", secret=True, required=False),
              F("session_token", "Session token (optional)", secret=True, required=False)]

    def __init__(self, cfg: dict, secrets: dict, clients: Callable[[str], Any] | None = None):
        super().__init__(cfg, secrets)
        self._clients = clients or self._boto
        self._cache: dict[str, Any] = {}

    def _boto(self, service: str) -> Any:
        import boto3
        v = self.val
        kw = {k: v[a] for k, a in (("aws_access_key_id", "access_key_id"), ("aws_secret_access_key", "secret_access_key"), ("aws_session_token", "session_token"), ("profile_name", "profile")) if v.get(a)}
        return boto3.Session(region_name=v["region"], **kw).client(service)

    def c(self, service: str) -> Any:
        if service not in self._cache:
            self._cache[service] = self._clients(service)
        return self._cache[service]

    def _call(self, service: str, op: str, **kw: Any) -> Any:
        try:
            return getattr(self.c(service), op)(**kw)
        except Exception as e:  # noqa: BLE001 - botocore errors carry a code and a message worth showing
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
            raise TargetError(f"AWS {op} failed: {code + ': ' if code else ''}{str(e)[:300]}", code)

    def deploy(self, spec, name, image, env, existing, folder):
        common = dict(agentRuntimeArtifact={"containerConfiguration": {"containerUri": image}}, roleArn=self.val["role_arn"],
                      networkConfiguration={"networkMode": self.val["network"]}, protocolConfiguration={"serverProtocol": "HTTP"},
                      environmentVariables=env, description=(spec.get("description") or spec["name"])[:200])
        if existing:
            r = self._call("bedrock-agentcore-control", "update_agent_runtime", agentRuntimeId=existing["runtime_id"], **common)
        else:
            r = self._call("bedrock-agentcore-control", "create_agent_runtime", agentRuntimeName=name, clientToken=uuid.uuid4().hex, **common)
        return dict(runtime_id=r["agentRuntimeId"], arn=r["agentRuntimeArn"], version=str(r["agentRuntimeVersion"]), slot=None)

    def status(self, ref):
        r = self._call("bedrock-agentcore-control", "get_agent_runtime", agentRuntimeId=ref["runtime_id"], agentRuntimeVersion=ref["version"])
        st = r["status"]
        state = "ready" if st == "READY" else "failed" if st.endswith("FAILED") else "gone" if st == "DELETING" else "creating"
        return dict(state=state, detail=r.get("failureReason") or st.lower().replace("_", " "))

    def route(self, ref, slot):
        ctl, kw = "bedrock-agentcore-control", dict(agentRuntimeId=ref["runtime_id"])
        try:
            self._call(ctl, "get_agent_runtime_endpoint", endpointName=slot, **kw)
            r = self._call(ctl, "update_agent_runtime_endpoint", endpointName=slot, agentRuntimeVersion=ref["version"], **kw)
        except TargetError as e:
            if e.code != "ResourceNotFoundException":
                raise
            r = self._call(ctl, "create_agent_runtime_endpoint", name=slot, agentRuntimeVersion=ref["version"], clientToken=uuid.uuid4().hex, **kw)
        return {**ref, "slot": slot, "endpoint_arn": r.get("agentRuntimeEndpointArn", "")}

    def invoke(self, ref, text):
        if not ref.get("slot"):
            raise TargetError("this version has no endpoint yet: it is exposed when you test or switch to it")
        r = self._call("bedrock-agentcore", "invoke_agent_runtime", agentRuntimeArn=ref["arn"], qualifier=ref["slot"], runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex[:8],
                       payload=json.dumps({"prompt": text}).encode(), contentType="application/json", accept="application/json")
        body = r["response"]
        raw = body.read() if hasattr(body, "read") else body
        try:
            return reply_text(json.loads(raw))
        except ValueError:
            return (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))[:4000]

    def check(self):
        r = self._call("bedrock-agentcore-control", "list_agent_runtimes", maxResults=1)
        return dict(ok=True, detail=f"signed in to AWS in {self.val['region']}; {len(r.get('agentRuntimes', []))} runtime(s) visible")

    def delete(self, ref, whole=False):
        ctl = "bedrock-agentcore-control"
        if whole:
            self._call(ctl, "delete_agent_runtime", agentRuntimeId=ref["runtime_id"])
        elif ref.get("slot"):
            try:
                self._call(ctl, "delete_agent_runtime_endpoint", agentRuntimeId=ref["runtime_id"], endpointName=ref["slot"])
            except TargetError as e:
                if e.code != "ResourceNotFoundException":
                    raise


# ------------------------------------------------------------------------------------------------------------- HTTP based targets
class Http(Target):
    def __init__(self, cfg: dict, secrets: dict, token: Callable[[], str] | None = None):
        super().__init__(cfg, secrets)
        self._token = token or self._fetch_token
        self.http = httpx.Client(transport=TRANSPORT, timeout=TIMEOUT)

    def _fetch_token(self) -> str:
        raise NotImplementedError

    def req(self, method: str, url: str, **kw: Any) -> Any:
        try:
            tok = self._token()
        except TargetError:
            raise
        except Exception as e:  # noqa: BLE001 - bad key file, expired, no network
            raise TargetError(f"{self.label}: the credentials could not be used ({type(e).__name__}: {str(e)[:200]})")
        headers = {"Authorization": f"Bearer {tok}", **kw.pop("headers", {})}
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise TargetError(f"{self.label}: cannot reach the API ({type(e).__name__})")
        if r.status_code in (401, 403):
            raise TargetError(f"{self.label} refused the credentials or the account lacks permission (HTTP {r.status_code}).", "denied")
        if r.status_code == 404:
            raise TargetError(f"{self.label}: not found (it may have been deleted)", "notfound")
        if r.status_code >= 400:
            try:
                j = r.json()
                msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else (j.get("error") or j.get("message"))
            except ValueError:
                msg = None
            raise TargetError(f"{self.label} API error {r.status_code}: {str(msg or r.text)[:300]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise TargetError(f"{self.label} returned something that is not JSON")


class Foundry(Http):
    id, label, platform = "foundry", "Microsoft Foundry hosted agents", "linux/amd64"
    docs = "https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent"
    cost_note = "A hosted agent bills for the compute it uses while sessions run; the image sits in your Azure Container Registry."
    side_effects = ("Creates an agent version in your Foundry project (and an Entra agent identity the first time).", "The model key you give it is stored in the agent version.")
    fields = [F("project_endpoint", "Project endpoint", help="https://<account>.services.ai.azure.com/api/projects/<project>"),
              F("tenant_id", "Tenant id"), F("client_id", "Client id"), F("client_secret", "Client secret", secret=True),
              F("cpu", "CPU", required=False, default="1"), F("memory", "Memory", required=False, default="2Gi")]
    API = "v1"

    def _fetch_token(self) -> str:
        v = self.val
        r = self.http.post(f"https://login.microsoftonline.com/{v['tenant_id']}/oauth2/v2.0/token",
                           data={"grant_type": "client_credentials", "client_id": v["client_id"], "client_secret": v["client_secret"], "scope": "https://ai.azure.com/.default"})
        if r.status_code >= 400:
            raise TargetError(f"Entra refused the client credentials (HTTP {r.status_code}): {r.text[:200]}")
        return r.json()["access_token"]

    def _url(self, path: str) -> str:
        return self.val["project_endpoint"].rstrip("/") + path

    def _q(self) -> dict:
        return {"api-version": self.API}

    def deploy(self, spec, name, image, env, existing, folder):
        body = {"definition": {"kind": "hosted", "container_configuration": {"image": image}, "cpu": self.val["cpu"], "memory": self.val["memory"],
                               "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}], "environment_variables": {**env, "PORT": "8088"}}}
        if existing:
            body["draft"] = True                      # a candidate that gets no traffic until we pin the selector to it
        r = self.req("POST", self._url(f"/agents/{name}/versions"), params=self._q(), json=body)
        return dict(agent=name, version=str(r.get("version", "")), slot=None)

    def status(self, ref):
        r = self.req("GET", self._url(f"/agents/{ref['agent']}/versions/{ref['version']}"), params=self._q())
        st = str(r.get("status", "")).lower()
        state = "ready" if st == "active" else "failed" if st == "failed" else "gone" if st in ("deleting", "deleted") else "creating"
        err = r.get("error")
        return dict(state=state, detail=(err.get("message") if isinstance(err, dict) else str(err)) if err else st)

    def route(self, ref, slot):
        if slot == "live":
            self.req("PATCH", self._url(f"/agents/{ref['agent']}"), params=self._q(), headers={"Content-Type": "application/merge-patch+json"},
                     content=json.dumps({"agent_endpoint": {"version_selector": {"version_selection_rules": [{"type": "FixedRatio", "agent_version": ref["version"], "traffic_percentage": 100}]},
                                                            "protocol_configuration": {"responses": {}}}}))
        return {**ref, "slot": slot}

    def invoke(self, ref, text):
        if ref.get("slot") != "live":
            raise TargetError("a version that is not live receives no traffic on Azure: test it by its status, then switch to it")
        r = self.req("POST", self._url(f"/agents/{ref['agent']}/endpoint/protocols/openai/responses"), params=self._q(), json={"input": text, "stream": False})
        return reply_text(r)

    def check(self):
        r = self.req("GET", self._url("/agents"), params=self._q())
        return dict(ok=True, detail=f"signed in to the Foundry project; {len(r.get('data', []))} agent(s) visible")

    def delete(self, ref, whole=False):
        self.req("DELETE", self._url(f"/agents/{ref['agent']}" + ("" if whole else f"/versions/{ref['version']}")), params=self._q())


class Vertex(Http):
    id, label, platform = "vertex", "Vertex AI Agent Engine", "linux/amd64"
    docs = "https://cloud.google.com/vertex-ai/docs/reference/rest/v1/projects.locations.reasoningEngines"
    cost_note = "Every release is its own Agent Engine runtime and bills for the compute it uses while it exists (a shadow release doubles that until you delete the old one)."
    side_effects = ("Creates a reasoning engine in your Google Cloud project.", "The model key you give it is stored in the engine's environment.")
    fields = [F("project", "Project id"), F("region", "Region", help="e.g. us-central1"),
              F("credentials_file", "Service-account JSON file", help="Path to a key file with the Vertex AI User role plus access to the image."),
              F("service_account", "Run as service account (optional)", required=False)]

    def _fetch_token(self) -> str:
        import google.auth.transport.requests
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(self.val["credentials_file"], scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    def _base(self) -> str:
        return f"https://{self.val['region']}-aiplatform.googleapis.com/v1"

    def deploy(self, spec, name, image, env, existing, folder):
        s: dict = {"containerSpec": {"imageUri": image, "port": PORT}, "agentFramework": "custom", "deploymentSpec": {"env": [{"name": k, "value": v} for k, v in env.items()]}}
        if self.val.get("service_account"):
            s["serviceAccount"] = self.val["service_account"]
        r = self.req("POST", f"{self._base()}/projects/{self.val['project']}/locations/{self.val['region']}/reasoningEngines",
                     json={"displayName": name, "description": (spec.get("description") or spec["name"])[:200], "spec": s})
        return dict(operation=r.get("name", ""), engine="", slot=None)

    def status(self, ref):
        if ref.get("engine"):
            return dict(state="ready", detail="engine created")
        op = self.req("GET", f"{self._base()}/{ref['operation']}")
        if not op.get("done"):
            return dict(state="creating", detail="creating the engine")
        if op.get("error"):
            return dict(state="failed", detail=str(op["error"].get("message", "failed"))[:300])
        engine = (op.get("response") or {}).get("name", "")
        return dict(state="ready" if engine else "failed", detail="engine created" if engine else "the operation finished without an engine", ref={**ref, "engine": engine})

    def route(self, ref, slot):
        return {**ref, "slot": slot}                   # Agent Engine has no traffic split: Agent Studio records which engine is live

    def invoke(self, ref, text):
        if not ref.get("engine"):
            raise TargetError("the engine is not ready yet")
        r = self.req("POST", f"{self._base()}/{ref['engine']}:query", json={"input": {"prompt": text}})
        return reply_text(r.get("output", r))

    def check(self):
        r = self.req("GET", f"{self._base()}/projects/{self.val['project']}/locations/{self.val['region']}/reasoningEngines", params={"pageSize": "1"})
        return dict(ok=True, detail=f"signed in to Google Cloud project {self.val['project']}; {len(r.get('reasoningEngines', []))} engine(s) visible")

    def delete(self, ref, whole=False):
        if ref.get("engine"):
            self.req("DELETE", f"{self._base()}/{ref['engine']}", params={"force": "true"})


class Local(Target):
    id, label, remote = "local", "This PC (Agent Studio runs it)", False
    cost_note = "Nothing to deploy; model calls are billed by your model provider and stay under Agent Studio's spend caps."
    fields: list[dict] = []

    def check(self):
        return dict(ok=True, detail="nothing to set up")


TARGETS: dict[str, type[Target]] = {t.id: t for t in (Local, DockerTarget, AgentCore, Vertex, Foundry)}
TARGET_IDS = tuple(TARGETS)
