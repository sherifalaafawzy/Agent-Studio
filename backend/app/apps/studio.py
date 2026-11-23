"""What makes the builder smart, beyond the plain store: a plan, a look at the preview's own errors, undo and compare, themes and components, and one-step
connections to your agents and data sources. These are methods of AppStore (see store.py); they live here only to keep each file readable.

* PLAN: the builder writes down what it is going to make (pages, features, steps) before it writes any file, and ticks the steps off. It is shown to you.
* VERIFY: the files are read for broken references and blocked code (lint.py) and the preview reports its own errors (studio_js.py). The result is what
  the builder reads to fix its own mistakes. The number of failed checks in a row is counted here, so a builder that cannot fix something stops after a
  few tries and tells you instead of looping.
* NEVER automatic: switching on an agent's API access, and anything that deploys or pushes. Those stay one explicit click of yours.
"""
from __future__ import annotations

import json
import re
import time

from . import design
from .errors import AppError
from .lint import lint
from .manifest import MAX_AGENTS

META_KEY = "apps_meta"
MAX_FIX = 3                                      # how many times in a row the builder may try to fix the same problem on its own
MAX_CONSOLE = 30
RUNTIME_KINDS = ("error", "resource", "blocked", "console.error")        # what the preview reports that counts as broken (warnings do not)
MODEL_MODES = ("best", "custom")


def _text(v: object, n: int, what: str, empty_ok: bool = True) -> str:
    if not isinstance(v, str) or (not empty_ok and not v.strip()):
        raise AppError(f"{what} must be text" + ("" if empty_ok else " and not empty"))
    return v.strip()[:n]


def _list(v: object, n: int, each: int, what: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > n:
        raise AppError(f"{what} must be a list of at most {n} items")
    return [_text(x, each, f"each of {what}", empty_ok=False) for x in v]


class StudioMixin:
    def _init_studio(self) -> None:
        self._console: dict[str, dict] = {}

    # ---- per-app notes that are not files: the plan, the chosen model, the fix counter
    def _metas(self) -> dict:
        try:
            m = json.loads(self.db.get_setting(META_KEY, "{}") or "{}")
        except ValueError:
            return {}
        return m if isinstance(m, dict) else {}

    def meta(self, app_id: str) -> dict:
        self._row(app_id)
        m = self._metas().get(app_id, {})
        return {"plan": m.get("plan"), "model": m.get("model") or {"mode": "best", "provider": "", "model": ""}, "fix_round": int(m.get("fix_round", 0))}

    def _save_meta(self, app_id: str, patch: dict) -> None:
        all_ = self._metas()
        all_[app_id] = {**all_.get(app_id, {}), **patch}
        self.db.set_setting(META_KEY, json.dumps(all_))

    def forget(self, app_id: str) -> None:
        """Drop everything kept about an app that is being deleted."""
        all_ = self._metas()
        if all_.pop(app_id, None) is not None:
            self.db.set_setting(META_KEY, json.dumps(all_))
        self._console.pop(app_id, None)

    # ---- the plan
    def set_plan(self, app_id: str, plan: object) -> dict:
        self._on()
        self._row(app_id)
        if not isinstance(plan, dict):
            raise AppError("the plan must be an object")
        steps_in = plan.get("steps")
        if not isinstance(steps_in, list) or not 1 <= len(steps_in) <= 20:
            raise AppError("the plan needs 1 to 20 steps")
        steps = [{"title": _text(s["title"] if isinstance(s, dict) else s, 160, "each step", empty_ok=False), "done": bool(isinstance(s, dict) and s.get("done"))} for s in steps_in]
        clean = {"title": _text(plan.get("title", ""), 80, "the title", empty_ok=False), "summary": _text(plan.get("summary", ""), 600, "the summary"),
                 "pages": _list(plan.get("pages"), 12, 80, "pages"), "features": _list(plan.get("features"), 20, 160, "features"),
                 "data": _list(plan.get("data"), 10, 160, "data"), "agents": _list(plan.get("agents"), 10, 80, "agents"), "steps": steps, "updated": time.time()}
        self._save_meta(app_id, {"plan": clean})
        self._emit(app_id, {"type": "plan"})
        return clean

    def plan_done(self, app_id: str, steps: object) -> dict:
        """Tick steps off (1-based numbers). Steps that are already done stay done."""
        self._on()
        plan = self.meta(app_id)["plan"]
        if not plan:
            raise AppError("there is no plan yet: write one with app_plan first")
        if not isinstance(steps, list) or not steps or any(isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= len(plan["steps"]) for i in steps):
            raise AppError(f"send step numbers from 1 to {len(plan['steps'])}")
        for i in steps:
            plan["steps"][i - 1]["done"] = True
        plan["updated"] = time.time()
        self._save_meta(app_id, {"plan": plan})
        self._emit(app_id, {"type": "plan"})
        return plan

    def clear_plan(self, app_id: str) -> dict:
        self._row(app_id)
        self._save_meta(app_id, {"plan": None})
        self._emit(app_id, {"type": "plan"})
        return {"plan": None}

    # ---- the model the builder uses for this app
    def get_model(self, app_id: str) -> dict:
        return self.meta(app_id)["model"]

    def set_model(self, app_id: str, mode: object, provider: object = "", model: object = "") -> dict:
        self._on()
        self._row(app_id)
        if mode not in MODEL_MODES:
            raise AppError("mode must be best or custom")
        prov = _text(provider or "", 60, "the provider")
        mod = _text(model or "", 120, "the model")
        if mode == "custom" and not prov:
            raise AppError("choose a provider for a custom model")
        out = {"mode": mode, "provider": prov if mode == "custom" else "", "model": mod if mode == "custom" else ""}
        self._save_meta(app_id, {"model": out})
        return out

    # ---- what the running preview reported
    def add_console(self, app_id: str, kind: str, text: str, page: str = "", source: str = "", line: int = 0) -> None:
        self._row(app_id)
        st = self._console.setdefault(app_id, {"loaded": False, "errors": []})
        if kind == "ready":
            st["loaded"] = True
            self._emit(app_id, {"type": "console", "loaded": True, "errors": len(st["errors"])})
            return
        for e in st["errors"]:
            if e["kind"] == kind and e["text"] == text[:600]:
                e["count"] += 1
                return
        if len(st["errors"]) >= MAX_CONSOLE:
            return
        st["errors"].append(dict(kind=kind, text=text[:600], page=page[:100], source=source[:100], line=line, count=1, at=time.time()))
        self._emit(app_id, {"type": "console", "loaded": st["loaded"], "errors": len(st["errors"])})

    def console(self, app_id: str) -> dict:
        self._row(app_id)
        st = self._console.get(app_id, {"loaded": False, "errors": []})
        return {"loaded": st["loaded"], "errors": [dict(e) for e in st["errors"]]}

    def new_files(self, app_id: str, author: str) -> None:
        """The files just changed: what the preview reported about the old ones is out of date, and a change by you starts the fix counter again."""
        self._console[app_id] = {"loaded": False, "errors": []}
        if author in ("user", "restore") and self._metas().get(app_id, {}).get("fix_round"):
            self._save_meta(app_id, {"fix_round": 0})

    # ---- verify: the files and the preview, in one report the builder can act on
    def verify(self, app_id: str, count: bool = False) -> dict:
        problems: list[dict] = []
        chk = self.check(app_id)
        problems += [dict(level=p["level"], path="", text=p["text"]) for p in chk["problems"] if p["level"] in ("error", "warn")]
        try:
            problems += lint(self.files_dict(app_id), self.manifest(app_id))
        except AppError:
            pass                                                   # check() already said the manifest is broken
        con = self.console(app_id)
        runtime = [e for e in con["errors"] if e["kind"] in RUNTIME_KINDS]
        ok = not any(p["level"] == "error" for p in problems) and not runtime
        rnd = self.meta(app_id)["fix_round"]
        if count:
            rnd = 0 if ok else rnd + 1
            self._save_meta(app_id, {"fix_round": rnd})
        give_up = (not ok) and rnd > MAX_FIX
        lines = [f"- {p['level']}{(' in ' + p['path']) if p['path'] else ''}: {p['text']}" for p in problems if p["level"] == "error"]
        lines += [f"- warning{(' in ' + p['path']) if p['path'] else ''}: {p['text']}" for p in problems if p["level"] == "warn"]
        lines += [f"- the preview reported ({e['kind']}{(', ' + e['source'] + ':' + str(e['line'])) if e['source'] else ''}): {e['text']}" + (f" (x{e['count']})" if e["count"] > 1 else "") for e in runtime]
        if not con["loaded"]:
            lines.append("- the preview has not loaded since the last change, so runtime errors could not be checked (the files were still checked)")
        report = "\n".join(lines) if lines else "No problems found."
        return dict(ok=ok, problems=problems, console=con["errors"], preview_loaded=con["loaded"], round=rnd, max_rounds=MAX_FIX, give_up=give_up, report=report)

    def reset_round(self, app_id: str) -> dict:
        self._row(app_id)
        self._save_meta(app_id, {"fix_round": 0})
        return {"round": 0}

    # ---- applying changes directly (the user's own clicks: a theme, a component, a connection). An agent's changes go through propose() instead.
    def apply_direct(self, app_id: str, changes: list, note: str, author: str = "user") -> dict:
        self._on()
        clean = self._clean_changes(changes)
        overlay, _ = self._plan(app_id, clean)
        self._commit(app_id, overlay)
        v = self.snapshot(app_id, note, author)
        self._sync_key(app_id)
        self._emit(app_id, {"type": "applied", "version": v, "author": author})
        return {"version": v}

    # ---- undo and compare
    def undo(self, app_id: str) -> dict:
        """Go back one change. Undoing an undo goes forward again (a restore remembers the version it replaced)."""
        vs = self.versions(app_id)
        if not vs:
            raise AppError("there is nothing to undo")
        last = vs[0]
        m = re.search(r"\(was v(\d+)\)", last["note"]) if last["author"] == "restore" else None
        target = int(m.group(1)) if m else last["version"] - 1
        if target < 1 or not any(v["version"] == target for v in vs):
            raise AppError("there is no earlier version to go back to")
        return self.restore(app_id, target)

    def compare(self, app_id: str, a: int, b: int) -> list[dict]:
        from .store import _is_text
        old, new = self.version_files(app_id, a), self.version_files(app_id, b)
        out = []
        for rel in sorted(set(old) | set(new)):
            x, y = old.get(rel), new.get(rel)
            if x == y:
                continue
            ent = dict(path=rel, status="added" if x is None else "removed" if y is None else "changed")
            for k, v in (("old", x), ("new", y)):
                ent[k] = v.decode("utf-8") if v is not None and _is_text(v) and len(v) <= 200_000 else None
            out.append(ent)
        return out

    # ---- look and parts
    def _read_text(self, app_id: str, rel: str) -> str:
        p = self.root(app_id) / rel
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def theme_info(self, app_id: str) -> dict:
        self._row(app_id)
        return {**design.current_theme(self._read_text(app_id, "public/style.css")), **design.catalog()}

    def theme_change(self, app_id: str, theme: str = "warm", font: str = "system", radius: str = "soft") -> list[dict]:
        self._row(app_id)
        try:
            css = design.apply_theme(self._read_text(app_id, "public/style.css"), theme, font, radius)
        except ValueError as e:
            raise AppError(str(e))
        return [{"op": "write", "path": "public/style.css", "content": css}]

    def set_theme(self, app_id: str, theme: str, font: str = "system", radius: str = "soft", author: str = "user") -> dict:
        return {**self.apply_direct(app_id, self.theme_change(app_id, theme, font, radius), f"theme: {theme}, {font}, {radius} corners", author), **design.current_theme(self._read_text(app_id, "public/style.css"))}

    def component_changes(self, app_id: str, names: object) -> list[dict]:
        self._row(app_id)
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            raise AppError("send a list of component names")
        try:
            design.bundle(names)                                                           # unknown names are refused here, with the list to choose from
            all_names = design.merge_names(self._read_text(app_id, design.UI_CSS_FILE), names)
            css, js = design.bundle(all_names)
        except ValueError as e:
            raise AppError(str(e))
        changes = [{"op": "write", "path": design.UI_CSS_FILE, "content": css}, {"op": "write", "path": design.UI_JS_FILE, "content": js}]
        index = self._read_text(app_id, "public/index.html")
        wired = design.wire_html(index) if index else index
        if wired != index:
            changes.append({"op": "write", "path": "public/index.html", "content": wired})
        return changes

    def add_components(self, app_id: str, names: list, author: str = "user") -> dict:
        changes = self.component_changes(app_id, names)
        v = self.apply_direct(app_id, changes, "added components: " + ", ".join(names), author)["version"]
        return dict(version=v, usage={n: design.USAGE[n] for n in names})

    # ---- one-step connections to your agents and data sources
    def connections(self, app_id: str) -> dict:
        m = self.manifest(app_id)
        have_a, have_d = {a["id"]: a["purpose"] for a in m["agents"]}, {d["id"]: d["purpose"] for d in m["data"]}
        agents = [dict(id=a.id, name=a.name, description=(a.description or "")[:160], api_access=bool(a.api_access), connected=a.id in have_a, purpose=have_a.get(a.id, ""),
                       runtime=getattr(a, "runtime", "local"))
                  for a in (self.agents.list() if self.agents is not None else []) if a.id != "app_builder"]
        data = [dict(id=s["id"], label=s["label"], kind=s["kind"], description=s.get("description", ""), enabled=bool(s["enabled"]), connected=s["id"] in have_d, purpose=have_d.get(s["id"], ""))
                for s in (self.data.public() if self.data is not None else [])]
        return dict(agents=agents, data=data)

    def _manifest_change(self, app_id: str, patch: dict) -> dict:
        cur = self.manifest(app_id)
        return {"op": "write", "path": "app.json", "content": json.dumps({**cur, **patch}, indent=2) + "\n"}

    def agent_changes(self, app_id: str, agent_id: str, purpose: str = "") -> tuple[list[dict], object]:
        """The file changes that list an agent in the manifest and point the page at it, and the agent."""
        ag = self.agents.get(agent_id) if self.agents is not None else None
        if ag is None:
            raise AppError(f"no such agent '{agent_id}'")
        m = self.manifest(app_id)
        agents = [a for a in m["agents"] if a["id"] != agent_id]
        if len(agents) >= MAX_AGENTS:
            raise AppError(f"an app can call at most {MAX_AGENTS} agents")
        agents.append({"id": agent_id, "purpose": (purpose or "").strip()[:200] or "The agent this app talks to"})
        changes = [self._manifest_change(app_id, {"agents": agents})]
        index = self._read_text(app_id, "public/index.html")
        if 'data-agent=""' in index:
            changes.append({"op": "write", "path": "public/index.html", "content": index.replace('data-agent=""', f'data-agent="{agent_id}"', 1)})
        return changes, ag

    def connect_agent(self, app_id: str, agent_id: str, purpose: str = "", enable_api: bool = False, author: str = "user") -> dict:
        """List an agent in the manifest (so the page may call it) and point the page at it. Switching the agent's API access on is only done when `enable_api` is
        true, which only your own click in the app sends: the builder never turns it on for itself."""
        changes, ag = self.agent_changes(app_id, agent_id, purpose)
        enabled = False
        if enable_api and not ag.api_access:
            self.agents.update(agent_id, {"api_access": True})
            enabled = True
        v = self.apply_direct(app_id, changes, f"connected the agent {agent_id}", author)["version"]
        return dict(version=v, agent=agent_id, api_access=bool(ag.api_access) or enabled, enabled_now=enabled)

    def disconnect_agent(self, app_id: str, agent_id: str, author: str = "user") -> dict:
        m = self.manifest(app_id)
        if not any(a["id"] == agent_id for a in m["agents"]):
            raise AppError(f"the app does not use the agent '{agent_id}'")
        v = self.apply_direct(app_id, [self._manifest_change(app_id, {"agents": [a for a in m["agents"] if a["id"] != agent_id]})], f"disconnected the agent {agent_id}", author)["version"]
        return dict(version=v)

    def data_changes(self, app_id: str, source_id: str, purpose: str = "") -> list[dict]:
        """The file changes that list a data source in the manifest and add the small DataClient the page uses to query it."""
        from ..datasources.base import DataError
        from .templates import DATA_CLIENT_JS
        if self.data is None:
            raise AppError("data sources are not available")
        try:
            src = self.data.get(source_id)
        except DataError as e:
            raise AppError(str(e))
        if not src["enabled"]:
            raise AppError(f"the data source '{source_id}' is switched off")
        m = self.manifest(app_id)
        data = [d for d in m["data"] if d["id"] != source_id] + [{"id": source_id, "purpose": (purpose or src.get("description", "") or "").strip()[:200]}]
        changes = [self._manifest_change(app_id, {"data": data})]
        if self._read_text(app_id, "public/data-client.js") != DATA_CLIENT_JS:
            changes.append({"op": "write", "path": "public/data-client.js", "content": DATA_CLIENT_JS})
        index = self._read_text(app_id, "public/index.html")
        if index and "data-client.js" not in index:
            tag = '<script src="data-client.js"></script>\n'
            i = index.find('<script src="app.js"')
            i = i if i >= 0 else index.lower().rfind("</body>")
            changes.append({"op": "write", "path": "public/index.html", "content": index[:i] + tag + index[i:] if i >= 0 else index + tag})
        return changes

    def connect_data(self, app_id: str, source_id: str, purpose: str = "", author: str = "user") -> dict:
        """List a data source in the manifest (read-only, rows limited, audited) and add the small DataClient the page uses to query it."""
        v = self.apply_direct(app_id, self.data_changes(app_id, source_id, purpose), f"connected the data source {source_id}", author)["version"]
        return dict(version=v, source=source_id)

    def disconnect_data(self, app_id: str, source_id: str, author: str = "user") -> dict:
        m = self.manifest(app_id)
        if not any(d["id"] == source_id for d in m["data"]):
            raise AppError(f"the app does not use the data source '{source_id}'")
        v = self.apply_direct(app_id, [self._manifest_change(app_id, {"data": [d for d in m["data"] if d["id"] != source_id]})], f"disconnected the data source {source_id}", author)["version"]
        return dict(version=v)
