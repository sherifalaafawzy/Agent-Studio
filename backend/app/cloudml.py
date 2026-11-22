"""Cloud ML providers behind one interface (abstract factory): AWS SageMaker, Google Vertex AI, Azure ML, DigitalOcean (GenAI + GPU
Droplets), RunPod and Oracle OCI Data Science.

Reading is the default. Every provider exposes the same four resource kinds - `models`, `endpoints` (served/hosted things), `jobs`
(training and batch runs) and `compute` (notebooks, pods, droplets) - and two write operations, `deploy` and `delete`, which the MCP
server only runs when the user switched writes on AND the caller confirmed. Creating cloud resources costs money; each provider's
`cost_note` says how. API shapes were checked against the official references on 2026-11-22:
  Vertex AI  discovery document aiplatform.googleapis.com/v1        SageMaker  boto3 `sagemaker` client
  Azure ML   azure-rest-api-specs stable/2025-12-01                  DigitalOcean  openapi (gen-ai, droplets)
  RunPod     REST API v2 (https://api.runpod.io/v2; v1 is retired)   OCI  oci.data_science.DataScienceClient
Credentials come from environment variables of the MCP server entry (stored write-only); nothing is read from disk but the files
the user points to. HTTP and SDK access are injectable, so the tests use fakes and never touch a cloud.
"""
from __future__ import annotations

import os
from typing import Any, Callable

import httpx

KINDS = ("models", "endpoints", "jobs", "compute")
TRANSPORT: httpx.BaseTransport | None = None          # tests inject httpx.MockTransport
TIMEOUT = 30


class CloudError(Exception):
    """Something to tell the user or the agent as is (missing credential, API refusal, unsupported operation)."""


def F(name: str, label: str, secret: bool = False, required: bool = True, help: str = "") -> dict:
    return dict(name=name, label=label, secret=secret, required=required, help=help)


def item(rid: Any, name: Any, state: Any, kind: str, created: Any = None, **details: Any) -> dict:
    return dict(id=str(rid), name=str(name or rid), state=str(state or "unknown"), kind=kind, created=str(created) if created else None,
                details={k: v for k, v in details.items() if v not in (None, "")})


class Provider:
    id = ""
    label = ""
    fields: list[dict] = []
    cost_note = ""
    deploy_help = ""
    deletes = ("endpoints",)            # kinds `delete` accepts (jobs: stop/cancel)
    docs = ""

    def __init__(self, env: dict[str, str]):
        self.env = env
        missing = [f["label"] for f in self.fields if f["required"] and not env.get(f["name"])]
        if missing:
            raise CloudError(f"{self.label} is not configured: missing {', '.join(missing)} (set them in Catalog > ML tools > Cloud accounts)")

    def list(self, kind: str) -> list[dict]:
        raise CloudError(f"{self.label} does not support listing '{kind}'")

    def describe(self, kind: str, rid: str) -> dict:
        raise CloudError(f"{self.label} does not support describing '{kind}'")

    def deploy(self, spec: dict) -> dict:
        raise CloudError(f"{self.label} does not support deploy")

    def delete(self, kind: str, rid: str) -> dict:
        raise CloudError(f"{self.label} cannot delete '{kind}' (supported: {', '.join(self.deletes)})")

    supported: tuple[str, ...] = KINDS

    def check_kind(self, kind: str) -> None:
        if kind not in KINDS:
            raise CloudError(f"kind must be one of: {', '.join(KINDS)}")
        if kind not in self.supported:
            raise CloudError(f"{self.label} has no '{kind}' in this tool (supported: {', '.join(self.supported)})")

    @staticmethod
    def need(spec: dict, *keys: str) -> None:
        miss = [k for k in keys if spec.get(k) in (None, "")]
        if miss:
            raise CloudError("deploy spec is missing: " + ", ".join(miss))


# --------------------------------------------------------------------------------------------------------------- AWS SageMaker
class SageMaker(Provider):
    id, label, docs = "aws", "AWS SageMaker", "https://docs.aws.amazon.com/sagemaker/latest/APIReference/"
    fields = [F("AWS_REGION", "Region", help="e.g. us-east-1"), F("AWS_PROFILE", "Profile (optional)", required=False),
              F("AWS_ACCESS_KEY_ID", "Access key id (optional)", secret=True, required=False),
              F("AWS_SECRET_ACCESS_KEY", "Secret access key (optional)", secret=True, required=False),
              F("AWS_SESSION_TOKEN", "Session token (optional)", secret=True, required=False)]
    cost_note = "A SageMaker real-time endpoint bills per instance-hour (a GPU instance can be several dollars an hour) for as long as it exists - delete it when done."
    deploy_help = "{model_name: an existing SageMaker model, instance_type: 'ml.m5.large', instance_count: 1, endpoint_name: new name}"
    deletes = ("endpoints", "models", "jobs")

    def __init__(self, env: dict[str, str], clients: Callable[[str], Any] | None = None):
        super().__init__(env)
        self._clients = clients or self._boto
        self._c: Any = None

    def _boto(self, service: str) -> Any:
        import boto3
        e = self.env
        kw = {k: e[v] for k, v in (("aws_access_key_id", "AWS_ACCESS_KEY_ID"), ("aws_secret_access_key", "AWS_SECRET_ACCESS_KEY"),
                                   ("aws_session_token", "AWS_SESSION_TOKEN"), ("profile_name", "AWS_PROFILE")) if e.get(v)}
        return boto3.Session(region_name=e["AWS_REGION"], **kw).client(service)

    @property
    def c(self) -> Any:
        if self._c is None:
            self._c = self._clients("sagemaker")
        return self._c

    def _call(self, op: str, **kw: Any) -> Any:
        try:
            return getattr(self.c, op)(**kw)
        except Exception as e:  # noqa: BLE001 - botocore errors carry a useful code/message
            code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
            raise CloudError(f"AWS {op} failed: {code + ': ' if code else ''}{str(e)[:300]}")

    def list(self, kind: str) -> list[dict]:
        self.check_kind(kind)
        if kind == "models":
            return [item(m["ModelName"], m["ModelName"], "available", kind, m.get("CreationTime")) for m in self._call("list_models", MaxResults=100)["Models"]]
        if kind == "endpoints":
            return [item(e["EndpointName"], e["EndpointName"], e["EndpointStatus"], kind, e.get("CreationTime")) for e in self._call("list_endpoints", MaxResults=100)["Endpoints"]]
        if kind == "jobs":
            return [item(j["TrainingJobName"], j["TrainingJobName"], j["TrainingJobStatus"], kind, j.get("CreationTime")) for j in self._call("list_training_jobs", MaxResults=100)["TrainingJobSummaries"]]
        return [item(n["NotebookInstanceName"], n["NotebookInstanceName"], n["NotebookInstanceStatus"], kind, n.get("CreationTime"), instance_type=n.get("InstanceType"))
                for n in self._call("list_notebook_instances", MaxResults=100)["NotebookInstances"]]

    def describe(self, kind: str, rid: str) -> dict:
        self.check_kind(kind)
        op, arg = {"models": ("describe_model", "ModelName"), "endpoints": ("describe_endpoint", "EndpointName"),
                   "jobs": ("describe_training_job", "TrainingJobName"), "compute": ("describe_notebook_instance", "NotebookInstanceName")}[kind]
        r = self._call(op, **{arg: rid})
        return {k: (str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v) for k, v in r.items() if k != "ResponseMetadata"}

    def deploy(self, spec: dict) -> dict:
        self.need(spec, "model_name", "instance_type", "endpoint_name")
        name, n = spec["endpoint_name"], int(spec.get("instance_count", 1))
        if not 1 <= n <= 10:
            raise CloudError("instance_count must be 1-10")
        self._call("create_endpoint_config", EndpointConfigName=f"{name}-config",
                   ProductionVariants=[dict(VariantName="AllTraffic", ModelName=spec["model_name"], InitialInstanceCount=n, InstanceType=spec["instance_type"])])
        r = self._call("create_endpoint", EndpointName=name, EndpointConfigName=f"{name}-config")
        return dict(created="endpoint", name=name, arn=r.get("EndpointArn"), note="Creating - poll list/describe until InService.")

    def delete(self, kind: str, rid: str) -> dict:
        if kind == "endpoints":
            cfg = self._call("describe_endpoint", EndpointName=rid).get("EndpointConfigName")
            self._call("delete_endpoint", EndpointName=rid)
            if cfg:
                self._call("delete_endpoint_config", EndpointConfigName=cfg)
            return dict(deleted="endpoint", name=rid, also_deleted_config=cfg)
        if kind == "models":
            self._call("delete_model", ModelName=rid)
            return dict(deleted="model", name=rid)
        if kind == "jobs":
            self._call("stop_training_job", TrainingJobName=rid)
            return dict(stopped="training job", name=rid)
        return super().delete(kind, rid)


# --------------------------------------------------------------------------------------------------------- generic REST provider
class Rest(Provider):
    """Providers that speak JSON over HTTPS with a bearer token."""
    base = ""

    def __init__(self, env: dict[str, str]):
        super().__init__(env)
        self.http = httpx.Client(transport=TRANSPORT, timeout=TIMEOUT)

    def req(self, method: str, url: str, **kw: Any) -> Any:
        try:
            r = self.http.request(method, url, headers=self.headers(), **kw)
        except httpx.HTTPError as e:
            raise CloudError(f"{self.label}: cannot reach the API ({type(e).__name__})")
        if r.status_code in (401, 403):
            raise CloudError(f"{self.label} rejected the credentials or the account lacks permission (HTTP {r.status_code}).")
        if r.status_code >= 400:
            try:
                msg = r.json()
                msg = (msg.get("error") if isinstance(msg.get("error"), str) else (msg.get("error") or {}).get("message")) or msg.get("message") or msg.get("detail") or msg.get("id") or r.text
            except ValueError:
                msg = r.text
            raise CloudError(f"{self.label} API error {r.status_code}: {str(msg)[:300]}")
        if r.status_code == 204 or not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise CloudError(f"{self.label} returned something that is not JSON")


# --------------------------------------------------------------------------------------------------------------- Google Vertex AI
class Vertex(Rest):
    id, label, docs = "gcp", "Google Vertex AI", "https://cloud.google.com/vertex-ai/docs/reference/rest"
    fields = [F("GCP_PROJECT", "Project id"), F("GCP_REGION", "Region", help="e.g. us-central1"),
              F("GOOGLE_APPLICATION_CREDENTIALS", "Service-account JSON file", help="Path to a key file. Give the account only the Vertex AI roles you need (Viewer for read-only).")]
    cost_note = "A Vertex AI endpoint with a deployed model bills per node-hour (more with GPUs) until the model is undeployed and the endpoint deleted."
    deploy_help = ("{action:'create_endpoint', display_name} then {action:'deploy_model', endpoint: endpoint id, model: model id, machine_type:'n1-standard-2', "
                   "min_replicas:1, max_replicas:1, accelerator_type?, accelerator_count?}")
    supported = ("models", "endpoints", "jobs")
    deletes = ("endpoints", "models", "jobs")

    def __init__(self, env: dict[str, str], token: Callable[[], str] | None = None):
        super().__init__(env)
        self._token = token or self._adc

    def _adc(self) -> str:
        import google.auth.transport.requests
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(self.env["GOOGLE_APPLICATION_CREDENTIALS"], scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    def headers(self) -> dict:
        try:
            return {"Authorization": f"Bearer {self._token()}"}
        except Exception as e:  # noqa: BLE001 - bad key file, expired, no network
            raise CloudError(f"Google credentials could not be used: {type(e).__name__}: {str(e)[:200]}")

    @property
    def api(self) -> str:
        return f"https://{self.env['GCP_REGION']}-aiplatform.googleapis.com/v1"

    @property
    def parent(self) -> str:
        return f"projects/{self.env['GCP_PROJECT']}/locations/{self.env['GCP_REGION']}"

    def _path(self, kind: str, rid: str) -> str:
        coll = {"models": "models", "endpoints": "endpoints", "jobs": "customJobs"}[kind]
        return rid if rid.startswith("projects/") else f"{self.parent}/{coll}/{rid}"

    def list(self, kind: str) -> list[dict]:
        self.check_kind(kind)
        coll = {"models": "models", "endpoints": "endpoints", "jobs": "customJobs"}[kind]
        r = self.req("GET", f"{self.api}/{self.parent}/{coll}", params={"pageSize": 100})
        return [item(x["name"].split("/")[-1], x.get("displayName"), x.get("state", "available"), kind, x.get("createTime"),
                     deployed_models=len(x.get("deployedModels", [])) or None) for x in r.get(coll, [])]

    def describe(self, kind: str, rid: str) -> dict:
        self.check_kind(kind)
        return self.req("GET", f"{self.api}/{self._path(kind, rid)}")

    def deploy(self, spec: dict) -> dict:
        action = spec.get("action", "deploy_model")
        if action == "create_endpoint":
            self.need(spec, "display_name")
            r = self.req("POST", f"{self.api}/{self.parent}/endpoints", json={"displayName": spec["display_name"]})
            return dict(created="endpoint (long-running operation started)", operation=r.get("name"))
        if action != "deploy_model":
            raise CloudError("action must be create_endpoint or deploy_model")
        self.need(spec, "endpoint", "model", "machine_type")
        machine = {"machineType": spec["machine_type"]}
        if spec.get("accelerator_type"):
            machine.update(acceleratorType=spec["accelerator_type"], acceleratorCount=int(spec.get("accelerator_count", 1)))
        body = {"deployedModel": {"model": self._path("models", spec["model"]), "displayName": spec.get("display_name") or spec["model"],
                                  "dedicatedResources": {"machineSpec": machine, "minReplicaCount": int(spec.get("min_replicas", 1)),
                                                         "maxReplicaCount": int(spec.get("max_replicas", 1))}}, "trafficSplit": {"0": 100}}
        r = self.req("POST", f"{self.api}/{self._path('endpoints', spec['endpoint'])}:deployModel", json=body)
        return dict(deploying="model", operation=r.get("name"), note="Long-running operation; poll describe on the endpoint.")

    def delete(self, kind: str, rid: str) -> dict:
        if kind in ("endpoints", "models"):
            r = self.req("DELETE", f"{self.api}/{self._path(kind, rid)}")
            return dict(deleted=kind[:-1], name=rid, operation=r.get("name"))
        if kind == "jobs":
            self.req("POST", f"{self.api}/{self._path('jobs', rid)}:cancel", json={})
            return dict(cancelled="custom job", name=rid)
        return super().delete(kind, rid)


# ------------------------------------------------------------------------------------------------------------------- Azure ML
class AzureML(Rest):
    id, label, docs = "azure", "Azure Machine Learning", "https://learn.microsoft.com/rest/api/azureml/"
    API = "2025-12-01"
    fields = [F("AZURE_TENANT_ID", "Tenant id"), F("AZURE_CLIENT_ID", "Client (app) id"), F("AZURE_CLIENT_SECRET", "Client secret", secret=True),
              F("AZURE_SUBSCRIPTION_ID", "Subscription id"), F("AZURE_RESOURCE_GROUP", "Resource group"), F("AZURE_ML_WORKSPACE", "ML workspace name")]
    cost_note = "An Azure ML managed online deployment bills for its VM instances (per hour, GPUs cost the most) for as long as the deployment exists."
    deploy_help = ("{action:'create_endpoint', name} then {action:'create_deployment', endpoint, name, model:'azureml:<model>:<version>', "
                   "instance_type:'Standard_DS3_v2', instance_count:1}")
    deletes = ("endpoints", "jobs")

    def __init__(self, env: dict[str, str]):
        super().__init__(env)
        self._tok: str | None = None

    def headers(self) -> dict:
        if self._tok is None:
            try:
                r = self.http.post(f"https://login.microsoftonline.com/{self.env['AZURE_TENANT_ID']}/oauth2/v2.0/token",
                                   data=dict(client_id=self.env["AZURE_CLIENT_ID"], client_secret=self.env["AZURE_CLIENT_SECRET"],
                                             scope="https://management.azure.com/.default", grant_type="client_credentials"))
            except httpx.HTTPError as e:
                raise CloudError(f"Azure: cannot reach the sign-in service ({type(e).__name__})")
            if r.status_code != 200 or "access_token" not in r.json():
                raise CloudError("Azure rejected the client credentials (check tenant, client id and secret).")
            self._tok = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._tok}"}

    @property
    def ws(self) -> str:
        e = self.env
        return (f"https://management.azure.com/subscriptions/{e['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{e['AZURE_RESOURCE_GROUP']}"
                f"/providers/Microsoft.MachineLearningServices/workspaces/{e['AZURE_ML_WORKSPACE']}")

    def list(self, kind: str) -> list[dict]:
        self.check_kind(kind)
        coll = {"models": "models", "endpoints": "onlineEndpoints", "jobs": "jobs", "compute": "computes"}[kind]
        r = self.req("GET", f"{self.ws}/{coll}", params={"api-version": self.API})
        out = []
        for x in r.get("value", []):
            p = x.get("properties") or {}
            out.append(item(x.get("name"), x.get("name"), p.get("provisioningState") or p.get("status") or "available", kind,
                            (x.get("systemData") or {}).get("createdAt"), auth_mode=p.get("authMode"), scoring_uri=p.get("scoringUri")))
        return out

    def describe(self, kind: str, rid: str) -> dict:
        self.check_kind(kind)
        coll = {"models": "models", "endpoints": "onlineEndpoints", "jobs": "jobs", "compute": "computes"}[kind]
        if kind == "models":
            r = self.req("GET", f"{self.ws}/{coll}/{rid}/versions", params={"api-version": self.API})
            return {"versions": [v.get("name") for v in r.get("value", [])]}
        return self.req("GET", f"{self.ws}/{coll}/{rid}", params={"api-version": self.API})

    def _location(self) -> str:
        return self.req("GET", self.ws, params={"api-version": self.API})["location"]

    def deploy(self, spec: dict) -> dict:
        action = spec.get("action", "create_deployment")
        q = {"api-version": self.API}
        if action == "create_endpoint":
            self.need(spec, "name")
            self.req("PUT", f"{self.ws}/onlineEndpoints/{spec['name']}", params=q, json={
                "location": spec.get("location") or self._location(), "identity": {"type": "SystemAssigned"}, "properties": {"authMode": spec.get("auth_mode", "Key")}})
            return dict(created="endpoint", name=spec["name"], note="Provisioning started; poll list/describe.")
        if action != "create_deployment":
            raise CloudError("action must be create_endpoint or create_deployment")
        self.need(spec, "endpoint", "name", "model", "instance_type")
        self.req("PUT", f"{self.ws}/onlineEndpoints/{spec['endpoint']}/deployments/{spec['name']}", params=q, json={
            "location": spec.get("location") or self._location(), "sku": {"name": "Default", "capacity": int(spec.get("instance_count", 1))},
            "properties": {"endpointComputeType": "Managed", "model": spec["model"], "instanceType": spec["instance_type"]}})
        return dict(created="deployment", endpoint=spec["endpoint"], name=spec["name"], note="Provisioning started; poll describe on the endpoint.")

    def delete(self, kind: str, rid: str) -> dict:
        q = {"api-version": self.API}
        if kind == "endpoints":
            self.req("DELETE", f"{self.ws}/onlineEndpoints/{rid}", params=q)
            return dict(deleted="endpoint (and its deployments)", name=rid)
        if kind == "jobs":
            self.req("POST", f"{self.ws}/jobs/{rid}/cancel", params=q)
            return dict(cancelled="job", name=rid)
        return super().delete(kind, rid)


# ------------------------------------------------------------------------------------------------------------------ DigitalOcean
class DigitalOcean(Rest):
    id, label, docs = "digitalocean", "DigitalOcean (GenAI + GPU Droplets)", "https://docs.digitalocean.com/reference/api/"
    base = "https://api.digitalocean.com/v2"
    fields = [F("DIGITALOCEAN_TOKEN", "API token", secret=True, help="A personal access token. A read-only token is enough for list/describe.")]
    cost_note = "A GPU Droplet bills per hour from creation until it is destroyed (also while powered off); GenAI agents bill per use plus any dedicated resources."
    deploy_help = "{name, size: a Droplet size slug (GPU sizes start with 'gpu-'), image: image slug, region: e.g. 'tor1'}"
    supported = ("models", "endpoints", "compute")
    deletes = ("endpoints", "compute")

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.env['DIGITALOCEAN_TOKEN']}"}

    def list(self, kind: str) -> list[dict]:
        self.check_kind(kind)
        if kind == "models":
            r = self.req("GET", f"{self.base}/gen-ai/models", params={"per_page": 100})
            return [item(m.get("uuid"), m.get("name"), "available", kind, m.get("created_at")) for m in r.get("models", [])]
        if kind == "endpoints":
            r = self.req("GET", f"{self.base}/gen-ai/agents", params={"per_page": 100})
            return [item(a.get("uuid"), a.get("name"), (a.get("deployment") or {}).get("status", "available"), kind, a.get("created_at"), region=a.get("region")) for a in r.get("agents", [])]
        r = self.req("GET", f"{self.base}/droplets", params={"per_page": 100})
        return [item(d["id"], d.get("name"), d.get("status"), kind, d.get("created_at"), size=d.get("size_slug"), region=(d.get("region") or {}).get("slug"),
                     gpu=str(d.get("size_slug", "")).startswith("gpu-") or None) for d in r.get("droplets", [])]

    def describe(self, kind: str, rid: str) -> dict:
        self.check_kind(kind)
        if kind == "endpoints":
            return self.req("GET", f"{self.base}/gen-ai/agents/{rid}").get("agent", {})
        if kind == "compute":
            return self.req("GET", f"{self.base}/droplets/{rid}").get("droplet", {})
        raise CloudError("describe is supported for endpoints (GenAI agents) and compute (Droplets)")

    def deploy(self, spec: dict) -> dict:
        self.need(spec, "name", "size", "image")
        body = {k: spec[k] for k in ("name", "size", "image", "region") if spec.get(k)}
        d = self.req("POST", f"{self.base}/droplets", json=body).get("droplet", {})
        return dict(created="droplet", id=d.get("id"), name=d.get("name"), note="Billing started. Destroy it when you are done.")

    def delete(self, kind: str, rid: str) -> dict:
        if kind == "endpoints":
            self.req("DELETE", f"{self.base}/gen-ai/agents/{rid}")
            return dict(deleted="GenAI agent", id=rid)
        if kind == "compute":
            self.req("DELETE", f"{self.base}/droplets/{rid}")
            return dict(destroyed="droplet", id=rid)
        return super().delete(kind, rid)


# ---------------------------------------------------------------------------------------------------------------------- RunPod
class RunPod(Rest):
    id, label, docs = "runpod", "RunPod", "https://docs.runpod.io/api-reference-v2/overview"
    base = "https://api.runpod.io/v2"
    fields = [F("RUNPOD_API_KEY", "API key", secret=True, help="Create it in the RunPod console. Use a read-only key for list/describe.")]
    cost_note = "A RunPod Pod bills per second while it exists (a stopped Pod still bills for its disk); GPU Pods start at a few cents to several dollars an hour."
    deploy_help = "{name, image: container image, gpu_id: e.g. 'NVIDIA GeForce RTX 4090', gpu_count: 1, disk_gb: 50}"
    supported = ("endpoints", "compute")
    deletes = ("endpoints", "compute")

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.env['RUNPOD_API_KEY']}"}

    def list(self, kind: str) -> list[dict]:
        self.check_kind(kind)
        if kind == "endpoints":
            r = self.req("GET", f"{self.base}/serverless")
            return [item(e.get("id"), e.get("name"), e.get("status", "available"), kind, e.get("createdAt")) for e in r.get("endpoints", [])]
        r = self.req("GET", f"{self.base}/pods")
        return [item(p.get("id"), p.get("name"), p.get("status"), kind, p.get("createdAt"), gpu=(p.get("gpu") or {}).get("id"), cost_per_hour=p.get("cost")) for p in r.get("pods", [])]

    def describe(self, kind: str, rid: str) -> dict:
        self.check_kind(kind)
        return self.req("GET", f"{self.base}/{'serverless' if kind == 'endpoints' else 'pods'}/{rid}")

    def deploy(self, spec: dict) -> dict:
        self.need(spec, "name", "image")
        body: dict = {"name": spec["name"], "image": spec["image"], "disk": int(spec.get("disk_gb", 50))}
        if spec.get("gpu_id"):
            body["gpu"] = {"id": spec["gpu_id"], "count": int(spec.get("gpu_count", 1))}
        p = self.req("POST", f"{self.base}/pods", json=body)
        return dict(created="pod", id=p.get("id"), name=p.get("name"), cost_per_hour=p.get("cost"), note="Billing started. Terminate it when you are done.")

    def delete(self, kind: str, rid: str) -> dict:
        if kind == "compute":
            self.req("DELETE", f"{self.base}/pods/{rid}")
            return dict(terminated="pod", id=rid)
        if kind == "endpoints":
            self.req("DELETE", f"{self.base}/serverless/{rid}")
            return dict(deleted="serverless endpoint", id=rid)
        return super().delete(kind, rid)


# ------------------------------------------------------------------------------------------------------------ Oracle OCI Data Science
class OciDataScience(Provider):
    id, label, docs = "oci", "Oracle OCI Data Science", "https://docs.oracle.com/iaas/api/#/en/data-science/"
    fields = [F("OCI_COMPARTMENT_ID", "Compartment OCID"), F("OCI_CONFIG_FILE", "Config file", required=False, help="Default ~/.oci/config"),
              F("OCI_PROFILE", "Profile", required=False, help="Default DEFAULT")]
    cost_note = "An OCI model deployment bills for its compute shape (GPU shapes cost the most) per hour while it is active; notebook sessions bill while active."
    deploy_help = "{details: the CreateModelDeploymentDetails object from the OCI API reference (display_name, project_id, compartment_id, model_deployment_configuration_details)}"
    deletes = ("endpoints", "models", "jobs", "compute")

    def __init__(self, env: dict[str, str], client: Any = None):
        super().__init__(env)
        self._client = client

    @property
    def c(self) -> Any:
        if self._client is None:
            try:
                import oci
            except ImportError:
                raise CloudError("the OCI SDK is not installed in this environment (pip install oci)")
            cfg = oci.config.from_file(self.env.get("OCI_CONFIG_FILE") or "~/.oci/config", self.env.get("OCI_PROFILE") or "DEFAULT")
            self._client = oci.data_science.DataScienceClient(cfg)
        return self._client

    def _call(self, op: str, *a: Any, **kw: Any) -> Any:
        try:
            return getattr(self.c, op)(*a, **kw).data
        except CloudError:
            raise
        except Exception as e:  # noqa: BLE001 - oci.exceptions.ServiceError has status/code/message
            status, code = getattr(e, "status", None), getattr(e, "code", None)
            raise CloudError(f"OCI {op} failed: {f'{status} {code}: ' if status else ''}{str(getattr(e, 'message', e))[:300]}")

    def list(self, kind: str) -> list[dict]:
        self.check_kind(kind)
        op = {"models": "list_models", "endpoints": "list_model_deployments", "jobs": "list_job_runs", "compute": "list_notebook_sessions"}[kind]
        return [item(x.id, getattr(x, "display_name", None), getattr(x, "lifecycle_state", None), kind, getattr(x, "time_created", None))
                for x in self._call(op, compartment_id=self.env["OCI_COMPARTMENT_ID"])]

    def describe(self, kind: str, rid: str) -> dict:
        self.check_kind(kind)
        op = {"models": "get_model", "endpoints": "get_model_deployment", "jobs": "get_job_run", "compute": "get_notebook_session"}[kind]
        d = self._call(op, rid)
        return {k: v if isinstance(v, (str, int, float, bool, type(None))) else str(v) for k, v in vars(d).items()}

    def deploy(self, spec: dict) -> dict:
        self.need(spec, "details")
        if not isinstance(spec["details"], dict):
            raise CloudError("details must be an object")
        d = self._call("create_model_deployment", spec["details"])
        return dict(created="model deployment", id=getattr(d, "id", None), note="Creating; poll list/describe until ACTIVE.")

    def delete(self, kind: str, rid: str) -> dict:
        op = {"endpoints": "delete_model_deployment", "models": "delete_model", "jobs": "cancel_job_run", "compute": "delete_notebook_session"}.get(kind)
        if op is None:
            return super().delete(kind, rid)
        self._call(op, rid)
        return dict(done=op, id=rid)


PROVIDERS: dict[str, type[Provider]] = {c.id: c for c in (SageMaker, Vertex, AzureML, DigitalOcean, RunPod, OciDataScience)}


def make(provider: str, env: dict[str, str] | None = None, **inject: Any) -> Provider:
    """The factory: a configured provider from environment-style settings (os.environ when env is None)."""
    cls = PROVIDERS.get(provider)
    if cls is None:
        raise CloudError(f"unknown provider '{provider}' (choose from {', '.join(PROVIDERS)})")
    return cls({k: v for k, v in (os.environ if env is None else env).items() if v}, **inject)


def provider_info(env: dict[str, str]) -> list[dict]:
    """What the UI shows: each provider, its fields (set or not - never the values), cost note and whether it is configured."""
    out = []
    for pid, cls in PROVIDERS.items():
        fields = [dict(f, set=bool(env.get(f["name"]))) for f in cls.fields]
        out.append(dict(id=pid, label=cls.label, docs=cls.docs, cost_note=cls.cost_note, deploy_help=cls.deploy_help, kinds=list(cls.supported),
                        deletes=list(cls.deletes), fields=fields, configured=all(f["set"] for f in fields if f["required"])))
    return out
