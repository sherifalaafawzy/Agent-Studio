"""Cloud ML as an MCP server (stdio): look at, and (only when allowed) change, models and endpoints on AWS SageMaker, Google Vertex AI,
Azure ML, DigitalOcean, RunPod and Oracle OCI Data Science.

READ-ONLY by default. `deploy` and `delete` create or remove billable resources, so they need BOTH: the user switched
"allow create/delete" on in Catalog > ML tools (CLOUDML_ALLOW_WRITE=1 for this server), AND the call passes confirm=true. With
confirm=false they return a preview and the cost note and change nothing. Credentials come from this server's environment.
Launch:  python -m app.mcp_cloudml     (env: PYTHONPATH=<backend>, provider credentials, CLOUDML_ALLOW_WRITE)
"""
from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import cloudml as C

server = MCPServer("agent-studio-cloudml", instructions=(
    "Inspect and manage ML resources in the user's cloud accounts. Start with `providers` to see which accounts are configured. "
    "`list_resources` and `describe` only read. `deploy` and `delete` create or remove billable resources: call them first with "
    "confirm=false to show the user the preview and the cost note, and call with confirm=true only after the user has agreed in "
    "this conversation, naming the provider and resource. They are refused unless the user enabled writes in the app."))

_factory = C.make          # tests replace this


def _env() -> dict:
    return dict(os.environ)


def writes_allowed() -> bool:
    return os.environ.get("CLOUDML_ALLOW_WRITE", "") == "1"


def _provider(name: str) -> C.Provider:
    return _factory(name, _env())


def _guard(fn) -> dict:
    try:
        return {"ok": True, **fn()}
    except C.CloudError as e:
        return {"ok": False, "error": str(e)}


@server.tool(description="List the supported cloud providers, whether each is configured (credentials present), what resource kinds it supports and whether create/delete is enabled.")
def providers() -> dict:
    return {"ok": True, "writes_enabled": writes_allowed(), "providers": [
        {k: p[k] for k in ("id", "label", "configured", "kinds", "deletes", "cost_note", "deploy_help")} for p in C.provider_info(_env())]}


@server.tool(description="List resources of one kind. provider: aws | gcp | azure | digitalocean | runpod | oci. kind: models | endpoints (hosted/served) | jobs (training runs) | compute (notebooks, pods, droplets). Read-only.")
def list_resources(provider: str, kind: str) -> dict:
    return _guard(lambda: {"provider": provider, "kind": kind, "items": _provider(provider).list(kind)})


@server.tool(description="Full details of one resource (read-only). resource_id is the id/name from list_resources.")
def describe(provider: str, kind: str, resource_id: str) -> dict:
    return _guard(lambda: {"provider": provider, "kind": kind, "resource": _provider(provider).describe(kind, resource_id)})


@server.tool(description=(
    "CREATE billable cloud resources (an endpoint, deployment, pod or droplet). spec is provider specific - see `providers` deploy_help. "
    "confirm=false (default) only returns a preview and the cost note. confirm=true really creates it and is refused unless the user "
    "enabled writes in the app. Always ask the user first and name provider, resource and expected cost."))
def deploy(provider: str, spec: dict, confirm: bool = False) -> dict:
    def go():
        p = _provider(provider)
        preview = {"provider": provider, "action": "deploy", "spec": spec, "cost_warning": p.cost_note, "confirmed": False}
        if not confirm:
            return {**preview, "note": "Preview only - nothing was created. Call again with confirm=true after the user agrees."}
        if not writes_allowed():
            raise C.CloudError("creating resources is switched off. The user must turn on 'Allow create/delete' in Catalog > ML tools > Cloud accounts.")
        return {"provider": provider, "action": "deploy", "confirmed": True, "result": p.deploy(spec), "cost_warning": p.cost_note}
    return _guard(go)


@server.tool(description=(
    "DELETE (or stop/cancel for jobs) a cloud resource. kind: endpoints | models | jobs | compute (what the provider supports - see `providers`). "
    "confirm=false (default) only returns a preview; confirm=true deletes and is refused unless the user enabled writes in the app. "
    "Deleting is not reversible. Ask the user first and name the provider and resource."))
def delete(provider: str, kind: str, resource_id: str, confirm: bool = False) -> dict:
    def go():
        p = _provider(provider)
        p.check_kind(kind)
        if kind not in p.deletes:
            raise C.CloudError(f"{p.label} cannot delete '{kind}' here (supported: {', '.join(p.deletes)})")
        preview = {"provider": provider, "action": "delete", "kind": kind, "resource_id": resource_id}
        if not confirm:
            return {**preview, "confirmed": False, "note": "Preview only - nothing was deleted. Call again with confirm=true after the user agrees."}
        if not writes_allowed():
            raise C.CloudError("deleting resources is switched off. The user must turn on 'Allow create/delete' in Catalog > ML tools > Cloud accounts.")
        return {**preview, "confirmed": True, "result": p.delete(kind, resource_id)}
    return _guard(go)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
