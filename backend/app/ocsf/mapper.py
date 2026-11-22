"""Agent Studio events -> OCSF 1.10.0. This is the ONE place that decides how our events look in a security tool: change the mapping here.

Which parts follow the released schema, and which follow a convention:
* RELEASED SCHEMA (OCSF 1.10.0): every class, attribute and enum used below, including the `ai_operation` profile (ai_agent, ai_model, ai_capability,
  ai_status, delegation) that 1.10.0 adds to API Activity, Application Lifecycle, Entity Management and others. Events are validated against the
  vendored schema in the tests.
* CONVENTION (not in the schema): there is no AI event class. Following the OWASP Agent Observability Standard (aos.owasp.org/spec/trace/extend_ocsf),
  an agent's tool use is API Activity (6003) with the tool name as api.operation. Beyond that this mapping chooses:
    - model call            -> API Activity, operation "model.generate", destination = the provider
    - agent run             -> Application Lifecycle (Stop: the run finished), with ai_agent and ai_status
    - memory save/recall    -> API Activity, operation "memory.save" / "memory.recall" / "memory.forget"
    - guardrail, spend cap  -> Detection Finding (2004; this class has no AI profile, so the agent goes in `unmapped`)
    - settings, agents, credentials changed -> Entity Management (3004)
  Process Activity (1007) was not used for model calls: it needs a real process and device, and a model call is neither.
Anything that does not fit an OCSF attribute goes in `unmapped`, never in invented top-level attributes.

Redaction (same levels as the AI-BOM): `public` carries ids, names, counts, durations and costs only. `internal` adds tool arguments and sub-agent tasks
with credentials, paths and user names masked. `full` adds clipped sub-agent results. The user's prompts and the lead agent's answers are never sent at any level.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from ..aibom.builder import app_version
from ..aibom.redact import Redactor
from . import schema

PROFILE = "ai_operation"
READ_TOOLS = {"read_file", "list_dir", "glob_files", "search_text", "rag_search", "rag_list", "memory_search", "load_skill"}
CREATE_TOOLS = {"write_file", "create_skill", "memory_save", "save_artifact", "canvas_create", "propose_mcp_server", "propose_openapi_mcp", "spawn_agent"}
UPDATE_TOOLS = {"edit_file", "memory_notes"}
DELETE_TOOLS = {"memory_forget"}
BUILTIN_TOOLS = READ_TOOLS | CREATE_TOOLS | UPDATE_TOOLS | DELETE_TOOLS | {"run_shell"}
FUNCTION_TOOLS = {"spawn_agent", "call_external_agent"}
STOP_REASONS = {"end_turn": 1, "max_tokens": 2, "max_turn_requests": 2, "tool_use": 3, "cancelled": 4, "refusal": 5}


def _ms(t: float) -> int:
    return int(t * 1000)


class Mapper:
    def __init__(self, level: str = "public", roots: dict[str, str] | None = None, version: str | None = None):
        self.red = Redactor(level, roots)
        self.level = level
        self.version = version or app_version()

    # ------------------------------------------------------------------ building blocks
    def _base(self, cls: str, activity_id: int, t: float, severity_id: int = 1, status_id: int | None = None, message: str = "", ai: bool = False,
              uid: str | None = None) -> dict:
        k = schema.klass(cls)
        a = k["attributes"]
        ev: dict[str, Any] = {
            "class_uid": k["uid"], "class_name": k["caption"], "category_uid": k["category_uid"], "category_name": _category_caption(k),
            "activity_id": activity_id, "activity_name": schema.enum_caption(a["activity_id"], activity_id), "type_uid": k["uid"] * 100 + activity_id,
            "severity_id": severity_id, "severity": schema.enum_caption(a["severity_id"], severity_id), "time": _ms(t),
            "metadata": {"version": schema.VERSION, "product": {"name": "Agent Studio", "vendor_name": "Agent Studio", "version": self.version, "uid": "agent-studio"},
                         "uid": uid or uuid.uuid4().hex, "logged_time": _ms(time.time()), **({"profiles": [PROFILE]} if ai else {})},
        }
        ev["type_name"] = f"{k['caption']}: {ev['activity_name']}"
        if status_id is not None and "status_id" in a:
            ev["status_id"], ev["status"] = status_id, schema.enum_caption(a["status_id"], status_id)
        if message:
            ev["message"] = message
        return ev

    @staticmethod
    def _agent(agent: str, run: str = "", external: bool = False) -> dict:
        ag = {"name": agent, "uid": agent, "type_id": 99 if external else 1}
        if run:
            ag["instance_uid"] = run
        return ag

    def _extra(self, **kw: Any) -> dict:
        return {k: v for k, v in kw.items() if v not in (None, "", [], {})}

    def _text(self, value: Any, limit: int) -> str | None:
        return self.red.text(str(value))[:limit] if value else None

    # ------------------------------------------------------------------ spans
    def from_span(self, span: dict, classes: dict[str, bool] | None = None) -> dict | None:
        """One of Agent Studio's own spans -> an event, or None when this kind of span is switched off or is not mapped."""
        on = classes or {}
        name, attrs, src = span["name"], span.get("attrs") or {}, span["source"]
        t_end, dur_ms = span["end_s"], round((span["end_s"] - span["start_s"]) * 1000, 1)
        ok = span.get("status", "ok") == "ok"
        common = dict(trace_id=span.get("trace_id"), span_id=span.get("span_id"), parent_span_id=span.get("parent_id"), duration_ms=dur_ms)
        if name == "llm.call":
            if not on.get("model_calls", True):
                return None
            ev = self._api("model.generate", 99, t_end, ok, src, agent_run=span.get("run_id", ""),
                           ai_model={"name": str(attrs.get("gen_ai.request.model", "unknown")), "ai_provider": str(attrs.get("gen_ai.provider", "unknown"))})
            ev["dst_endpoint"] = {"name": str(attrs.get("gen_ai.provider", "unknown")), "svc_name": "model provider"}
            ev["unmapped"] = self._extra(**common, input_tokens=attrs.get("gen_ai.usage.input_tokens"), output_tokens=attrs.get("gen_ai.usage.output_tokens"),
                                         cost_usd=attrs.get("cost_usd"), usage_reported=attrs.get("usage_reported"))
            ev["message"] = f"{src} called {attrs.get('gen_ai.request.model', 'a model')}"
            return ev
        if name.startswith("tool."):
            if not on.get("tool_calls", True):
                return None
            tool = str(attrs.get("tool") or name[5:])
            act = 2 if tool in READ_TOOLS else 1 if tool in CREATE_TOOLS else 3 if tool in UPDATE_TOOLS else 4 if tool in DELETE_TOOLS else 99
            ev = self._api(tool, act, t_end, ok, src)
            ev["ai_capability"] = {"name": tool, "type_id": 1, "source_id": 2 if tool in FUNCTION_TOOLS else 3 if tool in BUILTIN_TOOLS else 1}
            ev["observables"] = [{"name": "tool", "type_id": 99, "value": tool}]
            ev["unmapped"] = self._extra(**common, args=self._text(attrs.get("args"), 300) if self.red.internal else None, tool_call_id=attrs.get("tool_call_id"))
            ev["message"] = f"{src} used {tool}"
            return ev
        if name.startswith("memory."):
            if not on.get("tool_calls", True):
                return None
            kind = name.split(".", 1)[1]
            op = {"recalled": ("memory.recall", 2), "saved": ("memory.save", 1), "pending": ("memory.save", 1), "forgot": ("memory.forget", 4)}.get(kind)
            if op is None:
                return None
            ev = self._api(op[0], op[1], t_end, ok, "agent-studio")
            ev["unmapped"] = self._extra(**common, memory_kind=attrs.get("memory.type"), scope=attrs.get("memory.scope"), review=kind == "pending" or None)
            return ev
        if name.startswith("agent.") or name.startswith("acp.turn."):
            if not on.get("agent_runs", True):
                return None
            external = name.startswith("acp.turn.")
            sub = attrs.get("run_id") not in (None, "root")          # a sub-agent's task and result are agent-to-agent messages; the lead agent's are the user's prompt and answer: never sent
            stop = str(attrs.get("stop_reason") or ("end_turn" if ok else "cancelled"))
            ev = self._base("application_lifecycle", 4, t_end, 1 if ok else 3, 1 if ok else 2, f"{src} finished a run" if ok else f"{src} stopped with an error", ai=True)
            ev["ai_agent"] = self._agent(src, str(attrs.get("run_id") or attrs.get("session") or ""), external)
            ev["ai_status"] = {"stop_reason": stop, "stop_reason_id": STOP_REASONS.get(stop, 99)}
            if attrs.get("spawned"):
                ev["delegation"] = {"uid": f"{span.get('parent_id') or 'root'}>{span.get('span_id')}", "created_time": _ms(span["start_s"])}
            ev["unmapped"] = self._extra(**common, model_calls=attrs.get("model_calls"), tool_calls=attrs.get("tool_calls"), cost_usd=attrs.get("cost_usd"), turn=attrs.get("turn"),
                                         spawned=attrs.get("spawned") or None, billing=attrs.get("billing"),
                                         task=self._text(attrs.get("task"), 200) if self.red.internal and sub else None, result=self._text(attrs.get("result"), 300) if self.red.full and sub else None)
            return ev
        return None

    def _api(self, operation: str, activity_id: int, t: float, ok: bool, agent: str, ai_model: dict | None = None, agent_run: str = "") -> dict:
        ev = self._base("api_activity", activity_id, t, 1 if ok else 2, 1 if ok else 2, ai=True)
        ev["actor"] = {"app_name": "Agent Studio", "app_uid": agent}
        ev["api"] = {"operation": operation}
        ev["src_endpoint"] = {"name": "agent-studio", "svc_name": "agent-studio"}
        ev["ai_agent"] = self._agent(agent, agent_run)
        if ai_model:
            ev["ai_model"] = ai_model
        return ev

    # ------------------------------------------------------------------ security findings
    def finding(self, kind: str, title: str, *, severity_id: int = 3, agent: str = "", t: float | None = None, blocked: bool = True, desc: str = "", **extra: Any) -> dict:
        """A guardrail block, spend-cap refusal, denied tool call, injection attempt ... -> Detection Finding."""
        t = t or time.time()
        ev = self._base("detection_finding", 1, t, severity_id, 1, title)
        ev["finding_info"] = {"uid": uuid.uuid4().hex, "title": title, "types": [kind], "created_time": _ms(t),
                              "analytic": {"name": "Agent Studio " + kind.replace("_", " "), "uid": kind, "type_id": 1}, **({"desc": self._text(desc, 300)} if desc and self.red.internal else {})}
        ev["is_alert"] = severity_id >= 4
        ev["action_id"], ev["disposition_id"] = (2, 2) if blocked else (3, 15)
        ev["metadata"]["profiles"] = ["security_control"]                # action_id and disposition_id belong to this profile
        ev["observables"] = [{"name": "kind", "type_id": 99, "value": kind}]
        ev["unmapped"] = self._extra(agent=agent, **{k: (self._text(v, 200) if isinstance(v, str) and k in ("detail", "reason") else v) for k, v in extra.items()})
        return ev

    # ------------------------------------------------------------------ configuration changes
    def entity_change(self, activity_id: int, kind: str, name: str, *, t: float | None = None, detail: dict | None = None, secret: bool = False) -> dict:
        """A setting, agent, credential or MCP server was created (1), updated (3) or deleted (4). Secret values never leave: only that it changed."""
        ev = self._base("entity_management", activity_id, t or time.time(), 3 if secret else 1, 1, f"{kind} '{name}' {schema.enum_caption(schema.klass('entity_management')['attributes']['activity_id'], activity_id).lower()}d", ai=True)
        ev["entity"] = {"name": name, "type": kind, "uid": f"{kind}:{name}", "type_id": 99}
        ev["actor"] = {"app_name": "Agent Studio", "user": {"name": "local-user", "type_id": 1}}
        ev["observables"] = [{"name": kind, "type_id": 99, "value": name}]
        shown = {k: (v if not secret else "(changed)") for k, v in (detail or {}).items()} if self.red.internal else {}
        if shown:
            ev["unmapped"] = {k: (self._text(v, 200) if isinstance(v, str) else v) for k, v in shown.items()}
        return ev


def _category_caption(k: dict) -> str:
    return {1: "System Activity", 2: "Findings", 3: "Identity & Access Management", 6: "Application Activity"}.get(k["category_uid"], "Other")
