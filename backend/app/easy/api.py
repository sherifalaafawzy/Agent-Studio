"""REST API for easy setup: /api/easy/*  (system check, onboarding, Ollama install + library, coding-agent installs, agent wizard)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..budget import estimate_tokens
from ..db import data_dir
from ..models import forget as forget_models
from . import agent_templates, ollama_local, onboarding as ob, syscheck
from .agent_installer import AgentInstaller, InstallError, Registry
from .ollama_library import Library
from .ollama_setup import OllamaInstaller, SetupError, find_ollama


class OnboardingPatch(BaseModel):
    status: str | None = None
    step: str | None = None
    choice: str | None = None
    completed: list[str] | None = None
    skipped: list[str] | None = None


class OllamaInstallIn(BaseModel):
    accept_unverified: bool = False


class LibraryRefreshIn(BaseModel):
    query: str = Field(default="", max_length=80)
    force: bool = False


class PullIn(BaseModel):
    model: str


class UseIn(BaseModel):
    model: str


class AgentInstallIn(BaseModel):
    agent: str
    method: str
    confirm_command: str = ""


class AgentUninstallIn(BaseModel):
    agent: str


class DraftIn(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    name: str = Field(default="", max_length=80)
    tools: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


@dataclass
class Easy:
    """The long-lived helpers behind the routes (one per app, so installs keep their state between requests)."""
    ollama: OllamaInstaller = field(default_factory=OllamaInstaller)
    library: Library = field(default_factory=Library)
    agents: AgentInstaller | None = None
    registry: Registry = field(default_factory=Registry)


def register(app: FastAPI) -> Easy:
    easy = Easy()
    app.state.easy = easy
    r = APIRouter(prefix="/api/easy", tags=["easy"])

    def S(request: Request):
        return request.app.state.s

    def agents(s=Depends(S)) -> AgentInstaller:
        if easy.agents is None:
            easy.agents = AgentInstaller(s.acp.store, easy.registry)
        return easy.agents

    def ollama_base(s) -> str | None:
        p = next((x for x in s.providers.list() if x.kind == "ollama"), None)
        return (p.api_base if p and p.api_base else None)

    # ---------------------------------------------------------------- system + onboarding
    @r.get("/system")
    async def system(s=Depends(S)):
        return await asyncio.to_thread(lambda: syscheck.check(s.chat.workspace_for(None), data_dir(), ollama=lambda: syscheck.ollama_state(ollama_base(s) or syscheck.OLLAMA_URL)))

    @r.get("/onboarding")
    async def onboarding_get(s=Depends(S), inst: AgentInstaller = Depends(agents)):
        rec = ob.Onboarding(s.db).get()
        providers = [p.public() for p in s.providers.list()]
        oll = await asyncio.to_thread(syscheck.ollama_state, ollama_base(s) or syscheck.OLLAMA_URL)
        found = bool(find_ollama())
        try:
            models = len(await asyncio.to_thread(ollama_local.installed, ollama_base(s))) if oll["running"] else 0
        except ollama_local.LocalError:
            models = 0
        acp = sum(1 for a in s.acp.store.list() if a["installed"])
        sidebot_on = s.db.get_setting("sidebot_enabled", "1") == "1"
        chats = s.db.one("SELECT COUNT(*) n FROM chat_sessions")["n"]
        custom = s.db.one("SELECT COUNT(*) n FROM agents WHERE builtin=0")["n"]
        usable = any(p["enabled"] and p["kind"] != "mock" and (p.get("auth_ready") or p["kind"] == "ollama") for p in providers)
        items = ob.checklist(providers=providers, ollama=dict(oll, installed=found), installed_models=models, workspace=s.chat.workspace_for(None).as_posix(),
                             acp_installed=acp, assistant_ready=sidebot_on and usable, chats=chats, custom_agents=custom)
        return dict(record=rec, steps=ob.STEPS, checklist=items, show=rec["status"] == "new", resume=rec["status"] == "in_progress")

    @r.put("/onboarding")
    def onboarding_put(body: OnboardingPatch, s=Depends(S)):
        try:
            return ob.Onboarding(s.db).update(body.model_dump(exclude_none=True))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.post("/onboarding/reset")
    def onboarding_reset(s=Depends(S)):
        return ob.Onboarding(s.db).reset()

    # ---------------------------------------------------------------- Ollama install
    @r.get("/ollama/status")
    async def ollama_status(s=Depends(S)):
        st = await asyncio.to_thread(syscheck.ollama_state, ollama_base(s) or syscheck.OLLAMA_URL)
        return dict(st, installed=bool(find_ollama()), path=find_ollama(), install=easy.ollama.status())

    @r.get("/ollama/plan")
    async def ollama_plan():
        return await asyncio.to_thread(easy.ollama.plan)

    @r.post("/ollama/install")
    def ollama_install(body: OllamaInstallIn):
        try:
            return easy.ollama.start(body.accept_unverified)
        except SetupError as e:
            raise HTTPException(409, str(e))

    @r.get("/ollama/install/status")
    def ollama_install_status():
        return easy.ollama.status()

    @r.post("/ollama/install/cancel")
    def ollama_install_cancel():
        return easy.ollama.cancel()

    @r.post("/ollama/start")
    async def ollama_start():
        try:
            return await asyncio.to_thread(easy.ollama.start_server)
        except SetupError as e:
            raise HTTPException(409, str(e))

    # ---------------------------------------------------------------- Ollama models
    @r.get("/ollama/models")
    async def ollama_models(s=Depends(S)):
        try:
            have = await asyncio.to_thread(ollama_local.installed, ollama_base(s))
            running = await asyncio.to_thread(ollama_local.loaded, ollama_base(s))
        except ollama_local.LocalError as e:
            return dict(ok=False, message=str(e), models=[], loaded=[])
        return dict(ok=True, message="", models=have, loaded=running)

    @r.get("/ollama/models/detail")
    async def ollama_model_detail(name: str, s=Depends(S)):
        try:
            return await asyncio.to_thread(ollama_local.show, name, ollama_base(s))
        except ollama_local.LocalError as e:
            raise HTTPException(422, str(e))

    @r.delete("/ollama/models")
    async def ollama_model_delete(name: str, s=Depends(S)):
        try:
            await asyncio.to_thread(ollama_local.delete, name, ollama_base(s))
        except ollama_local.LocalError as e:
            raise HTTPException(422, str(e))
        for p in s.providers.list():
            if p.kind == "ollama":
                forget_models(p.id)
        return dict(ok=True)

    @r.post("/ollama/pull")
    async def ollama_pull(body: PullIn, s=Depends(S)):
        base = ollama_base(s)

        async def gen():
            async for ev in ollama_local.pull(body.model.strip(), base):
                if ev["type"] == "done":
                    for p in s.providers.list():
                        if p.kind == "ollama":
                            forget_models(p.id)
                yield "data: " + json.dumps(ev) + "\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.post("/ollama/use")
    def ollama_use(body: UseIn, s=Depends(S)):
        """Switch the Ollama provider on and make `model` its default, so it shows up in every model picker."""
        p = next((x for x in s.providers.list() if x.kind == "ollama"), None)
        if p is None:
            raise HTTPException(404, "there is no Ollama provider")
        s.providers.update(p.id, dict(enabled=True, default_model=body.model.strip()))
        forget_models(p.id)
        return dict(ok=True, provider=p.id, model=body.model.strip())

    # ---------------------------------------------------------------- Ollama library
    @r.get("/ollama/library")
    async def ollama_library(q: str = "", s=Depends(S)):
        sysinfo = await asyncio.to_thread(lambda: syscheck.check(s.chat.workspace_for(None), data_dir(), ollama=lambda: dict(running=False, version=None)))
        try:
            names = [m["name"] for m in await asyncio.to_thread(ollama_local.installed, ollama_base(s))]
        except ollama_local.LocalError:
            names = []
        return dict(models=easy.library.catalog(names, sysinfo["ram_gb"], sysinfo["vram_gb"], q), ram_gb=sysinfo["ram_gb"], vram_gb=sysinfo["vram_gb"])

    @r.post("/ollama/library/refresh")
    async def ollama_library_refresh(body: LibraryRefreshIn):
        return await asyncio.to_thread(easy.library.refresh, body.query.strip(), body.force)

    @r.get("/ollama/library/tags")
    async def ollama_library_tags(model: str):
        return await asyncio.to_thread(easy.library.tags, model)

    # ---------------------------------------------------------------- coding agents
    @r.get("/agents")
    async def agents_catalog(inst: AgentInstaller = Depends(agents)):
        return await asyncio.to_thread(inst.catalog)

    @r.post("/agents/registry/refresh")
    async def agents_refresh(inst: AgentInstaller = Depends(agents)):
        reg = await asyncio.to_thread(easy.registry.get, True)
        return dict(source=reg["source"], count=len(reg["agents"]))

    @r.post("/agents/install")
    async def agents_install(body: AgentInstallIn, inst: AgentInstaller = Depends(agents)):
        try:
            return await asyncio.to_thread(inst.start, body.agent, body.method, body.confirm_command)
        except InstallError as e:
            raise HTTPException(409, str(e))

    @r.post("/agents/uninstall")
    async def agents_uninstall(body: AgentUninstallIn, inst: AgentInstaller = Depends(agents)):
        try:
            return await asyncio.to_thread(inst.uninstall, body.agent)
        except InstallError as e:
            raise HTTPException(409, str(e))

    @r.get("/agents/status")
    def agents_status(inst: AgentInstaller = Depends(agents)):
        return inst.status()

    # ---------------------------------------------------------------- agent wizard
    @r.get("/wizard")
    def wizard():
        return agent_templates.wizard_data()

    @r.post("/wizard/draft")
    async def wizard_draft(body: DraftIn, s=Depends(S)):
        """'Draft it for me': one guarded model call that writes the instruction text. Counts against the provider's spend cap."""
        import litellm

        from ..providers import litellm_target
        p = s.providers.get(body.provider) if body.provider else next((x for x in s.providers.list() if x.enabled and x.kind != "mock"), None)
        if p is None or not p.enabled:
            raise HTTPException(409, "No model is switched on yet, so there is nothing to draft with. Add a provider or a local model first, or write the instruction yourself.")
        model = body.model or p.default_model
        try:
            target, kw = litellm_target(p, model)
        except ValueError as e:
            raise HTTPException(422, str(e))
        prompt = agent_templates.draft_prompt(body.goal, body.name, body.tools)
        max_out = min(p.max_output_tokens, 700)
        res = s.guard.reserve(p.id, model, estimate_tokens(len(prompt) + len(agent_templates.DRAFT_SYSTEM), 2), max_out, target)
        try:
            resp = await litellm.acompletion(model=target, messages=[{"role": "system", "content": agent_templates.DRAFT_SYSTEM}, {"role": "user", "content": prompt}],
                                             max_tokens=max_out, **kw)
        except BaseException:
            s.guard.release(res, note="wizard_draft_failed")
            raise
        text = (resp.choices[0].message.content or "").strip()
        u = getattr(resp, "usage", None)
        cost = s.guard.settle(res, getattr(u, "prompt_tokens", None) if u else None, getattr(u, "completion_tokens", None) if u else None, note="wizard_draft")
        return dict(instruction=text, provider=p.id, model=model, cost_usd=cost)

    app.include_router(r)
    return easy


__all__ = ["register", "Easy"]
