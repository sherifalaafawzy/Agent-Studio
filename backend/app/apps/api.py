"""REST API for apps: /api/apps/*. Bodies are plain dicts on purpose (main.py uses postponed annotations).

The pages themselves are NOT served from here: they are served by the separate preview server (server.py) so that an app written by an agent never shares
an origin with this API.
"""
from __future__ import annotations

import asyncio
import base64
import json

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from ..providers import tier_model
from . import choose, design
from .export import Git, deploy_plan, export_zip
from .store import AppError

BUILDER_ID = "app_builder"


async def event_stream(store, aid: str, request, keepalive: float = 15.0):
    """Server-sent events for one app: a line each time files change, a version is made or a change is proposed."""
    q = store.subscribe(aid)
    try:
        yield ": connected\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), keepalive)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(ev)}\n\n"
    finally:
        store.unsubscribe(aid, q)


def register(app: FastAPI) -> None:
    r = APIRouter(prefix="/api/apps", tags=["apps"])

    def st(request: Request):
        return request.app.state.s.apps

    def rn(request: Request):
        return request.app.state.s.app_runner

    def gt(request: Request):
        return Git(request.app.state.s.apps)

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except AppError as e:
            raise HTTPException(404 if str(e).startswith("no such") else 422, str(e))

    def api_url(request: Request) -> str:
        return str(request.base_url).rstrip("/")

    def preview_url(request: Request) -> str:
        srv = request.app.state.s.apps_server
        try:
            return srv.ensure(st(request).config()["preview_port"])
        except RuntimeError as e:
            raise HTTPException(503, str(e))

    # ---- settings, templates, the builder agent
    @r.get("/config")
    def get_config(request: Request):
        return st(request).config()

    @r.put("/config")
    def put_config(body: dict, request: Request):
        return guard(st(request).set_config, body)

    @r.get("/templates")
    def templates(request: Request):
        return st(request).templates()

    @r.get("/builder")
    def builder(request: Request):
        s = request.app.state.s
        a = s.agents.get(BUILDER_ID)
        return dict(id=BUILDER_ID, available=a is not None, name=a.name if a else "")

    @r.get("/server")
    def server_status(request: Request):
        return request.app.state.s.apps_server.status()

    @r.get("/themes")
    def themes():
        return design.catalog()

    @r.get("/components")
    def components():
        return design.component_list()

    @r.get("/models")
    def models(request: Request):
        """The models the builder can use: every provider that is switched on, best first, and which one 'best available' means right now."""
        provs = request.app.state.s.providers.list()
        return dict(options=choose.options(provs, tier_model), best=choose.best(provs, tier_model))

    # ---- apps
    @r.get("")
    def list_apps(request: Request):
        return st(request).list()

    @r.post("")
    def create(body: dict, request: Request):
        s = st(request)
        return guard(s.create, str(body.get("name") or ""), str(body.get("template") or "static-chat"), str(body.get("description") or ""), str(body.get("agent_id") or ""),
                     str(body.get("mode") or "advanced"), str(body.get("session_id") or ""))

    @r.get("/{aid}")
    def get_one(aid: str, request: Request):
        return guard(st(request).get, aid)

    @r.put("/{aid}")
    def update(aid: str, body: dict, request: Request):
        for k in ("name", "description", "mode", "session_id"):
            if body.get(k) is not None and not isinstance(body[k], str):
                raise HTTPException(422, f"{k} must be text")
        return guard(st(request).update, aid, name=body.get("name"), description=body.get("description"), mode=body.get("mode"), session_id=body.get("session_id"))

    @r.delete("/{aid}")
    def delete(aid: str, request: Request, confirm: bool = False):
        if not confirm:
            raise HTTPException(422, "deleting an app removes its files and versions for good: confirm to go ahead")
        s = request.app.state.s
        guard(st(request).get, aid)
        try:
            rn(request).stop(aid)
        except AppError:
            pass
        return guard(st(request).delete, aid)

    @r.post("/{aid}/duplicate")
    def duplicate(aid: str, request: Request):
        return guard(st(request).duplicate, aid)

    @r.get("/{aid}/events")
    async def events(aid: str, request: Request):
        guard(st(request).get, aid)
        return StreamingResponse(event_stream(st(request), aid, request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- files and the manifest
    @r.get("/{aid}/files")
    def files(aid: str, request: Request):
        return guard(st(request).tree, aid)

    @r.get("/{aid}/file")
    def read_file(aid: str, path: str, request: Request):
        return guard(st(request).read, aid, path)

    @r.put("/{aid}/file")
    def write_file(aid: str, body: dict, request: Request):
        return guard(st(request).write, aid, body.get("path"), body.get("content"))

    @r.delete("/{aid}/file")
    def delete_file(aid: str, path: str, request: Request):
        return guard(st(request).delete_file, aid, path)

    @r.post("/{aid}/attachments")
    def attach(aid: str, body: dict, request: Request):
        """An attachment as {name, text} or {name, base64}."""
        if isinstance(body.get("text"), str):
            data = body["text"].encode("utf-8")
        elif isinstance(body.get("base64"), str):
            try:
                data = base64.b64decode(body["base64"], validate=True)
            except ValueError:
                raise HTTPException(422, "the attachment is not valid base64")
        else:
            raise HTTPException(422, "send the attachment as text or base64")
        return guard(st(request).add_asset, aid, str(body.get("name") or ""), data)

    @r.get("/{aid}/manifest")
    def get_manifest(aid: str, request: Request):
        return guard(st(request).manifest, aid)

    @r.put("/{aid}/manifest")
    def put_manifest(aid: str, body: dict, request: Request):
        return guard(st(request).set_manifest, aid, body)

    @r.get("/{aid}/check")
    def check(aid: str, request: Request):
        return guard(st(request).check, aid)

    # ---- the environment and the app's own key
    @r.get("/{aid}/env")
    def get_env(aid: str, request: Request):
        return guard(st(request).env_status, aid)

    @r.put("/{aid}/env")
    def put_env(aid: str, body: dict, request: Request):
        return guard(st(request).set_env, aid, body)

    @r.get("/{aid}/key")
    def key(aid: str, request: Request):
        return guard(st(request).key_info, aid)

    @r.post("/{aid}/key/rotate")
    def rotate(aid: str, request: Request):
        return guard(st(request).rotate_key, aid)

    @r.post("/{aid}/key/revoke")
    def revoke(aid: str, request: Request):
        return guard(st(request).revoke_key, aid)

    # ---- changes waiting for review
    @r.get("/{aid}/proposals")
    def proposals(aid: str, request: Request, status: str = "pending"):
        return guard(st(request).proposals, aid, status)

    @r.post("/{aid}/proposals/{pid}/accept")
    def accept(aid: str, pid: str, request: Request):
        return guard(st(request).accept, aid, pid)

    @r.post("/{aid}/proposals/{pid}/reject")
    def reject(aid: str, pid: str, request: Request):
        return guard(st(request).reject, aid, pid)

    @r.post("/{aid}/accept-all")
    def accept_all(aid: str, request: Request):
        guard(st(request).get, aid)
        return st(request).accept_all(aid)

    @r.post("/{aid}/reject-all")
    def reject_all(aid: str, request: Request):
        guard(st(request).get, aid)
        return st(request).reject_all(aid)

    # ---- versions
    @r.get("/{aid}/versions")
    def versions(aid: str, request: Request):
        return guard(st(request).versions, aid)

    @r.post("/{aid}/versions")
    def snapshot(aid: str, body: dict, request: Request):
        return {"version": guard(st(request).snapshot, aid, str(body.get("note") or "saved on purpose"), "user")}

    @r.get("/{aid}/versions/{n}/diff")
    def diff(aid: str, n: int, request: Request):
        return guard(st(request).diff, aid, n)

    @r.post("/{aid}/restore")
    def restore(aid: str, body: dict, request: Request):
        n = body.get("version")
        if isinstance(n, bool) or not isinstance(n, int):
            raise HTTPException(422, "send the version number to bring back")
        return guard(st(request).restore, aid, n)

    # ---- the plan, the model, what the preview reported, undo and compare
    @r.get("/{aid}/plan")
    def get_plan(aid: str, request: Request):
        return {"plan": guard(st(request).meta, aid)["plan"]}

    @r.delete("/{aid}/plan")
    def clear_plan(aid: str, request: Request):
        return guard(st(request).clear_plan, aid)

    @r.get("/{aid}/model")
    def get_model(aid: str, request: Request):
        provs = request.app.state.s.providers.list()
        saved = guard(st(request).get_model, aid)
        return dict(saved=saved, effective=choose.effective(saved, provs, tier_model))

    @r.put("/{aid}/model")
    def put_model(aid: str, body: dict, request: Request):
        provs = request.app.state.s.providers.list()
        if body.get("mode") == "custom" and not any(p.id == body.get("provider") and p.enabled for p in provs):
            raise HTTPException(422, "that provider is not switched on")
        saved = guard(st(request).set_model, aid, body.get("mode"), body.get("provider"), body.get("model"))
        return dict(saved=saved, effective=choose.effective(saved, provs, tier_model))

    @r.get("/{aid}/console")
    def console(aid: str, request: Request):
        return guard(st(request).console, aid)

    @r.get("/{aid}/verify")
    def verify_view(aid: str, request: Request):
        return guard(st(request).verify, aid, False)

    @r.post("/{aid}/verify")
    def verify_count(aid: str, request: Request):
        """Check the app and count a failed check toward the builder's fix limit (the studio calls this after each of the builder's turns)."""
        return guard(st(request).verify, aid, True)

    @r.post("/{aid}/verify/reset")
    def verify_reset(aid: str, request: Request):
        return guard(st(request).reset_round, aid)

    @r.post("/{aid}/undo")
    def undo(aid: str, request: Request):
        return guard(st(request).undo, aid)

    @r.get("/{aid}/versions/compare")
    def compare(aid: str, request: Request, a: int, b: int):
        return guard(st(request).compare, aid, a, b)

    # ---- look, parts and connections: each is one click of yours and makes one version
    @r.get("/{aid}/theme")
    def get_theme(aid: str, request: Request):
        return guard(st(request).theme_info, aid)

    @r.put("/{aid}/theme")
    def put_theme(aid: str, body: dict, request: Request):
        return guard(st(request).set_theme, aid, str(body.get("theme") or "warm"), str(body.get("font") or "system"), str(body.get("radius") or "soft"))

    @r.post("/{aid}/components")
    def add_components(aid: str, body: dict, request: Request):
        return guard(st(request).add_components, aid, body.get("names"))

    @r.get("/{aid}/connections")
    def connections(aid: str, request: Request):
        return guard(st(request).connections, aid)

    @r.post("/{aid}/connect/agent")
    def connect_agent(aid: str, body: dict, request: Request):
        return guard(st(request).connect_agent, aid, str(body.get("id") or ""), str(body.get("purpose") or ""), body.get("enable_api") is True)

    @r.delete("/{aid}/connect/agent/{agent_id}")
    def disconnect_agent(aid: str, agent_id: str, request: Request):
        return guard(st(request).disconnect_agent, aid, agent_id)

    @r.post("/{aid}/connect/data")
    def connect_data(aid: str, body: dict, request: Request):
        return guard(st(request).connect_data, aid, str(body.get("id") or ""), str(body.get("purpose") or ""))

    @r.delete("/{aid}/connect/data/{source_id}")
    def disconnect_data(aid: str, source_id: str, request: Request):
        return guard(st(request).disconnect_data, aid, source_id)

    # ---- running
    @r.post("/{aid}/run")
    def run(aid: str, request: Request):
        guard(st(request).get, aid)
        return guard(rn(request).start, aid, api_url(request), preview_url(request))

    @r.post("/{aid}/stop")
    def stop(aid: str, request: Request):
        guard(st(request).get, aid)
        return guard(rn(request).stop, aid)

    @r.get("/{aid}/status")
    def status(aid: str, request: Request):
        guard(st(request).get, aid)
        srv = request.app.state.s.apps_server
        return guard(rn(request).status, aid, srv.url())

    @r.get("/{aid}/logs")
    def logs(aid: str, request: Request, tail: int = 200):
        guard(st(request).get, aid)
        return guard(rn(request).logs, aid, tail)

    # ---- getting it out
    @r.get("/{aid}/export.zip")
    def export(aid: str, request: Request):
        data, info = guard(export_zip, st(request), aid)
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{info["slug"]}.zip"', "X-Export-Files": str(info["files"])})

    @r.get("/{aid}/git")
    def git_status(aid: str, request: Request):
        return guard(gt(request).status, aid)

    @r.post("/{aid}/git/init")
    def git_init(aid: str, body: dict, request: Request):
        return guard(gt(request).init, aid, str(body.get("branch") or "main"))

    @r.post("/{aid}/git/commit")
    def git_commit(aid: str, body: dict, request: Request):
        return guard(gt(request).commit, aid, str(body.get("message") or ""))

    @r.get("/{aid}/git/diff")
    def git_diff(aid: str, request: Request):
        return guard(gt(request).diff, aid)

    @r.get("/{aid}/git/log")
    def git_log(aid: str, request: Request):
        return guard(gt(request).log, aid)

    @r.put("/{aid}/git/remote")
    def git_remote(aid: str, body: dict, request: Request):
        return guard(gt(request).set_remote, aid, str(body.get("url") or ""), body.get("token"), str(body.get("branch") or "main"))

    @r.post("/{aid}/git/push")
    def git_push(aid: str, body: dict, request: Request):
        return guard(gt(request).push, aid, body.get("confirm") is True)

    @r.post("/{aid}/git/pull")
    def git_pull(aid: str, body: dict, request: Request):
        return guard(gt(request).pull, aid, body.get("confirm") is True)

    @r.get("/{aid}/deploy-plan")
    def plan(aid: str, request: Request):
        return guard(deploy_plan, st(request), aid)

    app.include_router(r)
