"""Cloud ML providers (app/cloudml.py) against fakes: an in-memory SageMaker client, mock HTTP transports shaped like the real
APIs (Vertex AI, Azure ML, DigitalOcean, RunPod) and a fake OCI Data Science client. No network, no SDK, no cloud."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from app import cloudml as C


def http_provider(cls, env, handler, **kw):
    C.TRANSPORT = httpx.MockTransport(handler)
    try:
        return cls(env, **kw)
    finally:
        C.TRANSPORT = None


class Log(list):
    def handler(self, routes):
        def h(req: httpx.Request):
            try:
                body = json.loads(req.content) if req.content else None
            except ValueError:
                body = req.content.decode()                              # a form post (the Azure token request)
            self.append((req.method, req.url.path + (("?" + req.url.query.decode()) if req.url.query else ""), body, dict(req.headers)))
            for (m, frag), resp in sorted(routes.items(), key=lambda kv: -len(kv[0][1])):
                if req.method == m and frag in str(req.url):
                    return resp(req) if callable(resp) else httpx.Response(200, json=resp)
            return httpx.Response(404, json={"message": "no route"})
        return h


# ------------------------------------------------------------------------------------------------------------------ factory
def test_factory_builds_each_provider_and_refuses_unknown_or_unconfigured():
    assert set(C.PROVIDERS) == {"aws", "gcp", "azure", "digitalocean", "runpod", "oci"}
    p = C.make("runpod", {"RUNPOD_API_KEY": "k"})
    assert isinstance(p, C.RunPod) and p.env == {"RUNPOD_API_KEY": "k"}
    with pytest.raises(C.CloudError, match="unknown provider 'x'"):
        C.make("x")
    with pytest.raises(C.CloudError, match="RunPod is not configured: missing API key"):
        C.make("runpod", {})
    assert C.make("runpod", {"RUNPOD_API_KEY": "k", "OTHER": ""}).env == {"RUNPOD_API_KEY": "k"}


def test_make_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("DIGITALOCEAN_TOKEN", "t")
    assert C.make("digitalocean").env["DIGITALOCEAN_TOKEN"] == "t"


def test_provider_info_never_contains_values():
    info = C.provider_info({"RUNPOD_API_KEY": "super-secret", "AWS_REGION": "us-east-1"})
    assert "super-secret" not in json.dumps(info)
    d = {p["id"]: p for p in info}
    assert d["runpod"]["configured"] and not d["gcp"]["configured"] and d["aws"]["configured"]
    assert all(p["cost_note"] and p["deploy_help"] and p["docs"].startswith("https://") for p in info)
    assert d["oci"]["kinds"] == list(C.KINDS) and d["runpod"]["kinds"] == ["endpoints", "compute"]


def test_base_provider_defaults_refuse_cleanly():
    class Bare(C.Provider):
        id, label = "bare", "Bare"
    p = Bare({})
    for call in (lambda: p.list("models"), lambda: p.describe("models", "x"), lambda: p.deploy({}), lambda: p.delete("models", "x")):
        with pytest.raises(C.CloudError):
            call()
    with pytest.raises(C.CloudError, match="kind must be one of"):
        p.check_kind("nope")
    with pytest.raises(C.CloudError, match="deploy spec is missing: a, b"):
        p.need({"c": 1}, "a", "b")
    assert C.item(1, None, None, "models")["state"] == "unknown" and C.item(1, "n", "ok", "models", details_x="")["details"] == {}


# ------------------------------------------------------------------------------------------------------------------ SageMaker
class FakeSM:
    def __init__(self):
        self.calls, self.fail = [], {}

    def __getattr__(self, op):
        def call(**kw):
            self.calls.append((op, kw))
            if op in self.fail:
                raise self.fail[op]
            impl = getattr(type(self), "_" + op, None)
            return impl(self, **kw) if impl else {}
        return call

    def _list_models(self, **kw):
        return {"Models": [{"ModelName": "m1", "CreationTime": "t0"}]}

    def _list_endpoints(self, **kw):
        return {"Endpoints": [{"EndpointName": "ep1", "EndpointStatus": "InService", "CreationTime": "t1"}]}

    def _list_training_jobs(self, **kw):
        return {"TrainingJobSummaries": [{"TrainingJobName": "job1", "TrainingJobStatus": "Completed"}]}

    def _list_notebook_instances(self, **kw):
        return {"NotebookInstances": [{"NotebookInstanceName": "nb1", "NotebookInstanceStatus": "Stopped", "InstanceType": "ml.t3.medium"}]}

    def _describe_endpoint(self, **kw):
        cfg = {} if self.__dict__.get("no_config") else {"EndpointConfigName": "ep1-config"}
        return {"EndpointName": kw["EndpointName"], **cfg, "CreationTime": object(), "ResponseMetadata": {}}

    def _create_endpoint(self, **kw):
        return {"EndpointArn": "arn:aws:sagemaker:::endpoint/x"}


def sm(**env):
    fake = FakeSM()
    return C.SageMaker({"AWS_REGION": "us-east-1", **env}, clients=lambda s: fake if s == "sagemaker" else None), fake


def test_sagemaker_lists_all_kinds():
    p, f = sm()
    assert [(i["id"], i["state"]) for i in p.list("models")] == [("m1", "available")]
    assert p.list("endpoints")[0]["state"] == "InService" and p.list("jobs")[0]["state"] == "Completed"
    nb = p.list("compute")[0]
    assert nb["details"] == {"instance_type": "ml.t3.medium"}
    assert all(c[1].get("MaxResults") == 100 for c in f.calls)
    with pytest.raises(C.CloudError, match="kind must be"):
        p.list("nope")


def test_sagemaker_describe_makes_values_serialisable():
    p, f = sm()
    d = p.describe("endpoints", "ep1")
    assert d["EndpointName"] == "ep1" and "ResponseMetadata" not in d and isinstance(d["CreationTime"], str)
    for kind, op in (("models", "describe_model"), ("jobs", "describe_training_job"), ("compute", "describe_notebook_instance")):
        p.describe(kind, "x")
        assert f.calls[-1][0] == op


def test_sagemaker_deploy_creates_config_then_endpoint():
    p, f = sm()
    r = p.deploy(dict(model_name="m1", instance_type="ml.m5.large", endpoint_name="live", instance_count=2))
    assert [c[0] for c in f.calls] == ["create_endpoint_config", "create_endpoint"]
    cfg = f.calls[0][1]
    assert cfg["EndpointConfigName"] == "live-config" and cfg["ProductionVariants"] == [dict(VariantName="AllTraffic", ModelName="m1", InitialInstanceCount=2, InstanceType="ml.m5.large")]
    assert r["name"] == "live" and r["arn"].startswith("arn:")
    with pytest.raises(C.CloudError, match="deploy spec is missing: instance_type"):
        p.deploy(dict(model_name="m", endpoint_name="e"))
    with pytest.raises(C.CloudError, match="instance_count must be 1-10"):
        p.deploy(dict(model_name="m", endpoint_name="e", instance_type="t", instance_count=50))


def test_sagemaker_delete_variants():
    p, f = sm()
    assert p.delete("endpoints", "ep1") == dict(deleted="endpoint", name="ep1", also_deleted_config="ep1-config")
    assert [c[0] for c in f.calls[-3:]] == ["describe_endpoint", "delete_endpoint", "delete_endpoint_config"]
    f.no_config = True
    assert p.delete("endpoints", "ep2")["also_deleted_config"] is None
    assert p.delete("models", "m1")["deleted"] == "model" and f.calls[-1][0] == "delete_model"
    assert p.delete("jobs", "job1")["stopped"] == "training job" and f.calls[-1][0] == "stop_training_job"
    with pytest.raises(C.CloudError, match="cannot delete 'compute'"):
        p.delete("compute", "nb1")


def test_sagemaker_errors_are_explained():
    p, f = sm()

    class ClientError(Exception):
        response = {"Error": {"Code": "AccessDeniedException"}}
    f.fail["list_endpoints"] = ClientError("not allowed")
    with pytest.raises(C.CloudError, match="AWS list_endpoints failed: AccessDeniedException: not allowed"):
        p.list("endpoints")
    f.fail["list_models"] = RuntimeError("boom")
    with pytest.raises(C.CloudError, match="AWS list_models failed: boom"):
        p.list("models")


def test_sagemaker_builds_a_boto3_session_from_the_settings(monkeypatch):
    import boto3
    seen = {}

    class Sess:
        def __init__(self, **kw):
            seen.update(kw)

        def client(self, name):
            seen["client"] = name
            return "client!"
    monkeypatch.setattr(boto3, "Session", Sess)
    p = C.SageMaker({"AWS_REGION": "eu-west-1", "AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK", "AWS_SESSION_TOKEN": "ST", "AWS_PROFILE": "dev"})
    assert p.c == "client!" and p.c == "client!"
    assert seen == dict(region_name="eu-west-1", aws_access_key_id="AK", aws_secret_access_key="SK", aws_session_token="ST", profile_name="dev", client="sagemaker")


# --------------------------------------------------------------------------------------------------------------- the REST base
def test_rest_error_mapping():
    log = Log()

    class P(C.Rest):
        id, label, fields = "p", "Pee", []

        def headers(self):
            return {}
    routes = {("GET", "/ok"): {"a": 1}, ("GET", "/empty"): lambda r: httpx.Response(204), ("GET", "/nojson"): lambda r: httpx.Response(200, content=b"<html>"),
              ("GET", "/401"): lambda r: httpx.Response(401), ("GET", "/403"): lambda r: httpx.Response(403),
              ("GET", "/e1"): lambda r: httpx.Response(400, json={"error": "bad thing"}), ("GET", "/e2"): lambda r: httpx.Response(400, json={"error": {"message": "nested"}}),
              ("GET", "/e3"): lambda r: httpx.Response(500, json={"detail": "detail text"}), ("GET", "/e4"): lambda r: httpx.Response(500, text="plain failure"),
              ("GET", "/e5"): lambda r: httpx.Response(409, json={"id": "conflict"}),
              ("GET", "/boom"): lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))}
    p = http_provider(P, {}, log.handler(routes))
    assert p.req("GET", "http://x/ok") == {"a": 1} and p.req("GET", "http://x/empty") == {}
    for path, msg in (("nojson", "not JSON"), ("401", "rejected the credentials"), ("403", "HTTP 403"), ("e1", "API error 400: bad thing"), ("e2", "nested"),
                      ("e3", "detail text"), ("e4", "plain failure"), ("e5", "conflict"), ("boom", r"cannot reach the API \(ConnectError\)")):
        with pytest.raises(C.CloudError, match=msg):
            p.req("GET", f"http://x/{path}")


# ------------------------------------------------------------------------------------------------------------------ Vertex AI
GCP = {"GCP_PROJECT": "proj", "GCP_REGION": "us-central1", "GOOGLE_APPLICATION_CREDENTIALS": "C:/k.json"}
VX = "https://us-central1-aiplatform.googleapis.com/v1/projects/proj/locations/us-central1"


def vertex(routes, token=lambda: "tok"):
    log = Log()
    return http_provider(C.Vertex, GCP, log.handler(routes), token=token), log


def test_vertex_lists_and_describes():
    p, log = vertex({("GET", "/models"): {"models": [{"name": "projects/proj/locations/us-central1/models/123", "displayName": "churn", "createTime": "t"}]},
                     ("GET", "/endpoints"): {"endpoints": [{"name": ".../endpoints/9", "displayName": "live", "deployedModels": [{}, {}]}]},
                     ("GET", "/customJobs"): {"customJobs": [{"name": ".../customJobs/5", "displayName": "train", "state": "JOB_STATE_RUNNING"}]},
                     ("GET", "/endpoints/9"): {"name": "x", "displayName": "live"}})
    assert p.list("models")[0] == C.item("123", "churn", "available", "models", "t")
    e = p.list("endpoints")[0]
    assert e["id"] == "9" and e["details"] == {"deployed_models": 2}
    assert p.list("jobs")[0]["state"] == "JOB_STATE_RUNNING"
    assert log[0][3]["authorization"] == "Bearer tok" and "pageSize=100" in log[0][1]
    assert p.describe("endpoints", "9")["displayName"] == "live" and log[-1][1] == "/v1/projects/proj/locations/us-central1/endpoints/9"
    p.describe("endpoints", "projects/proj/locations/us-central1/endpoints/9")
    assert log[-1][1] == "/v1/projects/proj/locations/us-central1/endpoints/9"
    with pytest.raises(C.CloudError, match="no 'compute' in this tool"):
        p.list("compute")


def test_vertex_deploy_create_endpoint_and_deploy_model():
    p, log = vertex({("POST", "/endpoints"): {"name": "operations/1"}, ("POST", ":deployModel"): {"name": "operations/2"}})
    r = p.deploy(dict(action="create_endpoint", display_name="live"))
    assert log[-1][2] == {"displayName": "live"} and r["operation"] == "operations/1"
    r = p.deploy(dict(endpoint="9", model="123", machine_type="n1-standard-4", accelerator_type="NVIDIA_TESLA_T4", accelerator_count=1, max_replicas=3))
    method, path, body, _ = log[-1]
    assert path.endswith("/endpoints/9:deployModel")
    dm = body["deployedModel"]
    assert dm["model"] == "projects/proj/locations/us-central1/models/123" and dm["dedicatedResources"] == dict(
        machineSpec=dict(machineType="n1-standard-4", acceleratorType="NVIDIA_TESLA_T4", acceleratorCount=1), minReplicaCount=1, maxReplicaCount=3)
    assert body["trafficSplit"] == {"0": 100} and r["operation"] == "operations/2"
    p.deploy(dict(endpoint="9", model="123", machine_type="e2-standard-2", display_name="v2"))
    assert log[-1][2]["deployedModel"]["displayName"] == "v2" and "acceleratorType" not in log[-1][2]["deployedModel"]["dedicatedResources"]["machineSpec"]
    with pytest.raises(C.CloudError, match="action must be"):
        p.deploy(dict(action="explode"))
    with pytest.raises(C.CloudError, match="missing: display_name"):
        p.deploy(dict(action="create_endpoint"))
    with pytest.raises(C.CloudError, match="missing: endpoint, model, machine_type"):
        p.deploy({})


def test_vertex_delete_and_cancel():
    p, log = vertex({("DELETE", "/endpoints/9"): {"name": "operations/3"}, ("DELETE", "/models/123"): {}, ("POST", "/customJobs/5:cancel"): {}})
    assert p.delete("endpoints", "9")["operation"] == "operations/3"
    assert p.delete("models", "123")["deleted"] == "model"
    assert p.delete("jobs", "5")["cancelled"] == "custom job" and log[-1][2] == {}
    with pytest.raises(C.CloudError, match="cannot delete 'compute'"):
        p.delete("compute", "x")


def test_vertex_credential_problems_are_explained(monkeypatch):
    def bad():
        raise ValueError("file missing")
    p, _ = vertex({}, token=bad)
    with pytest.raises(C.CloudError, match="Google credentials could not be used: ValueError: file missing"):
        p.list("models")


def test_vertex_default_token_uses_the_service_account_file(monkeypatch):
    import google.auth.transport.requests as gtr
    from google.oauth2 import service_account
    seen = {}

    class Creds:
        token = "fresh-token"

        def refresh(self, request):
            seen["refreshed"] = True
    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", lambda path, scopes: seen.update(path=path, scopes=scopes) or Creds())
    monkeypatch.setattr(gtr, "Request", lambda: "req")
    p = C.Vertex(GCP)
    assert p._adc() == "fresh-token" and seen["path"] == "C:/k.json" and seen["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"] and seen["refreshed"]


# --------------------------------------------------------------------------------------------------------------------- Azure
AZ = {"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "c", "AZURE_CLIENT_SECRET": "s", "AZURE_SUBSCRIPTION_ID": "sub", "AZURE_RESOURCE_GROUP": "rg", "AZURE_ML_WORKSPACE": "ws"}
WS = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.MachineLearningServices/workspaces/ws"


def azure(routes, token_resp=None):
    log = Log()
    base = {("POST", "login.microsoftonline.com/t/oauth2/v2.0/token"): token_resp or {"access_token": "azt"}}
    return http_provider(C.AzureML, AZ, log.handler({**base, **routes})), log


def test_azure_signs_in_once_and_lists():
    p, log = azure({("GET", "/onlineEndpoints"): {"value": [{"name": "ep", "properties": {"provisioningState": "Succeeded", "authMode": "Key", "scoringUri": "https://x/score"}, "systemData": {"createdAt": "t"}}]},
                    ("GET", "/models"): {"value": [{"name": "m", "properties": {}}]}, ("GET", "/jobs"): {"value": [{"name": "j", "properties": {"status": "Running"}}]},
                    ("GET", "/computes"): {"value": []}})
    e = p.list("endpoints")[0]
    assert e["state"] == "Succeeded" and e["details"] == {"auth_mode": "Key", "scoring_uri": "https://x/score"} and e["created"] == "t"
    assert p.list("models")[0]["state"] == "available" and p.list("jobs")[0]["state"] == "Running" and p.list("compute") == []
    assert sum(1 for c in log if "oauth2" in c[1]) == 1
    form = [c for c in log if "oauth2" in c[1]][0]
    assert any(c[3].get("authorization") == "Bearer azt" for c in log) and "api-version=2025-12-01" in [c for c in log if "onlineEndpoints" in c[1]][0][1]
    assert form[0] == "POST"


def test_azure_describe():
    p, log = azure({("GET", "/models/m/versions"): {"value": [{"name": "1"}, {"name": "2"}]}, ("GET", "/onlineEndpoints/ep"): {"name": "ep"}})
    assert p.describe("models", "m") == {"versions": ["1", "2"]} and p.describe("endpoints", "ep") == {"name": "ep"}


def test_azure_deploy_endpoint_and_deployment():
    p, log = azure({("GET", "/workspaces/ws?"): {"location": "westeurope"}, ("PUT", "/onlineEndpoints/ep"): {}, ("PUT", "/deployments/blue"): {}})
    r = p.deploy(dict(action="create_endpoint", name="ep"))
    put = [c for c in log if c[0] == "PUT"][-1]
    assert put[2] == {"location": "westeurope", "identity": {"type": "SystemAssigned"}, "properties": {"authMode": "Key"}} and r["name"] == "ep"
    p.deploy(dict(endpoint="ep", name="blue", model="azureml:m:1", instance_type="Standard_DS3_v2", instance_count=2, location="northeurope"))
    put = [c for c in log if c[0] == "PUT"][-1]
    assert put[1].startswith(WS + "/onlineEndpoints/ep/deployments/blue")
    assert put[2] == {"location": "northeurope", "sku": {"name": "Default", "capacity": 2},
                      "properties": {"endpointComputeType": "Managed", "model": "azureml:m:1", "instanceType": "Standard_DS3_v2"}}
    with pytest.raises(C.CloudError, match="action must be"):
        p.deploy(dict(action="x"))
    with pytest.raises(C.CloudError, match="missing: name"):
        p.deploy(dict(action="create_endpoint"))
    with pytest.raises(C.CloudError, match="missing: endpoint, name, model, instance_type"):
        p.deploy({})


def test_azure_delete_and_cancel():
    p, log = azure({("DELETE", "/onlineEndpoints/ep"): lambda r: httpx.Response(202), ("POST", "/jobs/j1/cancel"): lambda r: httpx.Response(200, json={})})
    assert p.delete("endpoints", "ep")["name"] == "ep" and p.delete("jobs", "j1")["cancelled"] == "job"
    with pytest.raises(C.CloudError, match="cannot delete 'models'"):
        p.delete("models", "m")


def test_azure_sign_in_failures():
    p, _ = azure({}, token_resp={"error": "invalid_client"})
    with pytest.raises(C.CloudError, match="rejected the client credentials"):
        p.list("models")

    def down(req):
        raise httpx.ConnectError("offline")
    p2 = http_provider(C.AzureML, AZ, down)
    with pytest.raises(C.CloudError, match="cannot reach the sign-in service"):
        p2.list("models")


# ------------------------------------------------------------------------------------------------------------- DigitalOcean
def do(routes):
    log = Log()
    return http_provider(C.DigitalOcean, {"DIGITALOCEAN_TOKEN": "dop"}, log.handler(routes)), log


def test_digitalocean_lists_and_describes():
    p, log = do({("GET", "/v2/gen-ai/models"): {"models": [{"uuid": "u1", "name": "llama", "created_at": "t"}]},
                 ("GET", "/v2/gen-ai/agents/a1"): {"agent": {"uuid": "a1"}},
                 ("GET", "/v2/gen-ai/agents"): {"agents": [{"uuid": "a1", "name": "bot", "deployment": {"status": "STATUS_RUNNING"}, "region": "tor1"}, {"uuid": "a2", "name": "x"}]},
                 ("GET", "/v2/droplets/7"): {"droplet": {"id": 7}},
                 ("GET", "/v2/droplets"): {"droplets": [{"id": 7, "name": "gpu1", "status": "active", "size_slug": "gpu-h100x1-80gb", "region": {"slug": "nyc2"}}, {"id": 8, "name": "web", "status": "off", "size_slug": "s-1vcpu-1gb"}]}})
    assert p.list("models")[0]["id"] == "u1"
    ag = p.list("endpoints")
    assert ag[0]["state"] == "STATUS_RUNNING" and ag[0]["details"] == {"region": "tor1"} and ag[1]["state"] == "available"
    dr = p.list("compute")
    assert dr[0]["details"] == {"size": "gpu-h100x1-80gb", "region": "nyc2", "gpu": True} and "gpu" not in dr[1]["details"]
    assert log[0][3]["authorization"] == "Bearer dop"
    assert p.describe("endpoints", "a1") == {"uuid": "a1"} and p.describe("compute", "7") == {"id": 7}
    with pytest.raises(C.CloudError, match="describe is supported for"):
        p.describe("models", "u1")
    with pytest.raises(C.CloudError, match="no 'jobs' in this tool"):
        p.list("jobs")


def test_digitalocean_deploy_and_delete():
    p, log = do({("POST", "/v2/droplets"): {"droplet": {"id": 9, "name": "t1"}}, ("DELETE", "/v2/gen-ai/agents/a1"): lambda r: httpx.Response(204),
                 ("DELETE", "/v2/droplets/9"): lambda r: httpx.Response(204)})
    r = p.deploy(dict(name="t1", size="gpu-h100x1-80gb", image="gpu-h100x1-base", region="nyc2", ignored="x"))
    assert log[-1][2] == dict(name="t1", size="gpu-h100x1-80gb", image="gpu-h100x1-base", region="nyc2") and r["id"] == 9 and "Billing" in r["note"]
    p.deploy(dict(name="t2", size="s", image="i"))
    assert "region" not in log[-1][2]
    assert p.delete("endpoints", "a1")["deleted"] == "GenAI agent" and p.delete("compute", "9")["destroyed"] == "droplet"
    with pytest.raises(C.CloudError, match="cannot delete 'models'"):
        p.delete("models", "u1")
    with pytest.raises(C.CloudError, match="missing: size, image"):
        p.deploy(dict(name="x"))


# ------------------------------------------------------------------------------------------------------------------ RunPod
def runpod(routes):
    log = Log()
    return http_provider(C.RunPod, {"RUNPOD_API_KEY": "rp"}, log.handler(routes)), log


def test_runpod_uses_the_v2_api():
    p, log = runpod({("GET", "/v2/serverless"): {"endpoints": [{"id": "e1", "name": "api", "createdAt": "t"}]},
                     ("GET", "/v2/pods/p1"): {"id": "p1"}, ("GET", "/v2/serverless/e1"): {"id": "e1"},
                     ("GET", "/v2/pods"): {"pods": [{"id": "p1", "name": "train", "status": "RUNNING", "gpu": {"id": "NVIDIA GeForce RTX 4090", "count": 1}, "cost": 0.44}], "pagination": {}}})
    assert p.list("endpoints")[0]["id"] == "e1" and p.list("endpoints")[0]["state"] == "available"
    pod = p.list("compute")[0]
    assert pod["state"] == "RUNNING" and pod["details"] == {"gpu": "NVIDIA GeForce RTX 4090", "cost_per_hour": 0.44}
    assert log[0][1] == "/v2/serverless" and log[0][3]["authorization"] == "Bearer rp"
    assert p.describe("compute", "p1") == {"id": "p1"} and p.describe("endpoints", "e1") == {"id": "e1"}
    with pytest.raises(C.CloudError, match="no 'models' in this tool"):
        p.list("models")


def test_runpod_deploy_and_delete():
    p, log = runpod({("POST", "/v2/pods"): lambda r: httpx.Response(201, json={"id": "p9", "name": "n", "cost": 0.5}),
                     ("DELETE", "/v2/pods/p9"): lambda r: httpx.Response(204), ("DELETE", "/v2/serverless/e1"): lambda r: httpx.Response(204)})
    r = p.deploy(dict(name="n", image="runpod/pytorch:x", gpu_id="NVIDIA GeForce RTX 4090", gpu_count=2, disk_gb=80))
    assert log[-1][2] == {"name": "n", "image": "runpod/pytorch:x", "disk": 80, "gpu": {"id": "NVIDIA GeForce RTX 4090", "count": 2}} and r["cost_per_hour"] == 0.5
    p.deploy(dict(name="cpu", image="i"))
    assert "gpu" not in log[-1][2] and log[-1][2]["disk"] == 50
    assert p.delete("compute", "p9")["terminated"] == "pod" and p.delete("endpoints", "e1")["deleted"] == "serverless endpoint"
    with pytest.raises(C.CloudError, match="cannot delete 'jobs'"):
        p.delete("jobs", "x")
    with pytest.raises(C.CloudError, match="missing: name, image"):
        p.deploy({})


# ---------------------------------------------------------------------------------------------------------------------- OCI
class FakeOci:
    def __init__(self):
        self.calls, self.fail = [], None

    def __getattr__(self, op):
        def call(*a, **kw):
            self.calls.append((op, a, kw))
            if self.fail:
                raise self.fail
            if op.startswith("list_"):
                return NS(data=[NS(id="ocid1.x", display_name="thing", lifecycle_state="ACTIVE", time_created="t")])
            if op.startswith("get_"):
                return NS(data=NS(id="ocid1.x", name="n", nested=NS(a=1), count=3))
            if op == "create_model_deployment":
                return NS(data=NS(id="ocid1.new"))
            return NS(data=None)
        return call


def oci(**env):
    f = FakeOci()
    return C.OciDataScience({"OCI_COMPARTMENT_ID": "ocid1.comp", **env}, client=f), f


def test_oci_lists_and_describes():
    p, f = oci()
    for kind, op in (("models", "list_models"), ("endpoints", "list_model_deployments"), ("jobs", "list_job_runs"), ("compute", "list_notebook_sessions")):
        assert p.list(kind) == [C.item("ocid1.x", "thing", "ACTIVE", kind, "t")]
        assert f.calls[-1] == (op, (), {"compartment_id": "ocid1.comp"})
    d = p.describe("endpoints", "ocid1.x")
    assert d["id"] == "ocid1.x" and d["count"] == 3 and isinstance(d["nested"], str) and f.calls[-1][0] == "get_model_deployment"
    assert p.describe("models", "m") and f.calls[-1][0] == "get_model"


def test_oci_deploy_delete_and_errors():
    p, f = oci()
    details = {"display_name": "d", "project_id": "p", "compartment_id": "c"}
    assert p.deploy(dict(details=details))["id"] == "ocid1.new" and f.calls[-1] == ("create_model_deployment", (details,), {})
    with pytest.raises(C.CloudError, match="details must be an object"):
        p.deploy(dict(details="x"))
    with pytest.raises(C.CloudError, match="missing: details"):
        p.deploy({})
    for kind, op in (("endpoints", "delete_model_deployment"), ("models", "delete_model"), ("jobs", "cancel_job_run"), ("compute", "delete_notebook_session")):
        assert p.delete(kind, "ocid1.x")["done"] == op
    with pytest.raises(C.CloudError, match="cannot delete 'nope'"):
        p.delete("nope", "x")

    class ServiceError(Exception):
        status, code, message = 404, "NotAuthorizedOrNotFound", "not found"
    f.fail = ServiceError()
    with pytest.raises(C.CloudError, match="OCI list_models failed: 404 NotAuthorizedOrNotFound: not found"):
        p.list("models")
    f.fail = RuntimeError("plain")
    with pytest.raises(C.CloudError, match="OCI list_models failed: plain"):
        p.list("models")


def test_oci_creates_its_client_from_the_config_file(monkeypatch):
    import sys
    made = {}
    fake_oci = NS(config=NS(from_file=lambda f, p: made.update(file=f, profile=p) or {"cfg": 1}),
                  data_science=NS(DataScienceClient=lambda cfg: made.update(client_cfg=cfg) or "SDK"))
    monkeypatch.setitem(sys.modules, "oci", fake_oci)
    p = C.OciDataScience({"OCI_COMPARTMENT_ID": "c", "OCI_CONFIG_FILE": "C:/oci/config", "OCI_PROFILE": "prod"})
    assert p.c == "SDK" and p.c == "SDK" and made == dict(file="C:/oci/config", profile="prod", client_cfg={"cfg": 1})
    p2 = C.OciDataScience({"OCI_COMPARTMENT_ID": "c"})
    p2.c
    assert made["file"] == "~/.oci/config" and made["profile"] == "DEFAULT"


def test_oci_without_the_sdk_says_how_to_install_it(monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda n, *a, **k: (_ for _ in ()).throw(ImportError("no oci")) if n == "oci" else real(n, *a, **k))
    p = C.OciDataScience({"OCI_COMPARTMENT_ID": "c"})
    with pytest.raises(C.CloudError, match="pip install oci"):
        p.list("models")
