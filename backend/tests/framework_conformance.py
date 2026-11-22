"""The agent-framework conformance suite: ~15 behaviours EVERY adapter must show, whatever library runs underneath.

`Harness` runs one chat turn for an agent on a framework with a scripted fake worker standing in for the real library: it talks
to the same gateway and tool bridge a real worker would (in-process), so what is tested is the contract Agent Studio guarantees -
models reach the guard, tools run in the sandbox under the agent's own tool list, the run record, chat and artifacts are
written, errors and cancellation are clean. A new framework is added to FRAMEWORK_IDS and must pass every case unchanged.

Used by tests/test_frameworks.py (parametrised over the catalog). Not collected itself (no test_ prefix).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.frameworks.base import CAPS, LEVELS
from app.frameworks.catalog import FRAMEWORKS
from app.frameworks.gateway import GatewayError

EXTERNAL = [fid for fid, f in FRAMEWORKS.items() if not f.native]


def fake_completion(text_fn=None, usage=(10, 5)):
    async def completion(**kw):
        msgs = kw["messages"]
        last = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
        text = text_fn(kw) if text_fn else f"answer: {last}"
        body = {"id": "x", "object": "chat.completion", "model": kw["model"], "created": 1,
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)}}
        return SimpleNamespace(model_dump=lambda: body)
    return completion


class Harness:
    def __init__(self, client, fid: str, tmp_path):
        self.client, self.fid, self.tmp = client, fid, tmp_path
        self.s = client.app.state.s
        self.rt = self.s.chat.fw
        self.rt.completion = fake_completion()
        client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
        client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
        self.script: list[dict] = [{"model": True}, {"say": True}]
        self.tool_results: list = []
        self.token = None
        self.launched = 0
        self.provider = "mock"
        adapter = self.s.chat.frameworks.get(fid)
        adapter._ready = lambda _fid: True
        adapter.launcher = self.worker
        self.adapter = adapter

    def agent(self, **fields) -> str:
        body = {"id": "fwagent", "name": "FW agent", "framework": self.fid, "tools": [], "tier": "custom", "model": "m1", **fields}
        r = self.client.post("/api/agents", json=body)
        assert r.status_code == 200, r.text
        return body["id"]

    async def worker(self, fid, job, env):
        """What a real worker does, in-process: ask the gateway for the model, call tools over the bridge, print protocol lines."""
        self.launched += 1
        self.job, self.env = job, env
        assert fid == self.fid and job["protocol"] == 1
        token = job["gateway"].rsplit("/", 2)[-2] if job["gateway"].endswith("/tool") else job["gateway"].rsplit("/", 1)[-1]
        self.token = token
        text = ""
        for step in self.script:
            if "tool" in step:
                self.tool_results.append(await self.rt.call_tool(token, step["tool"], step.get("args", {})))
            elif "model" in step:
                msgs = [{"role": "system", "content": job["instructions"]}] + [{"role": m["role"], "content": m["text"]} for m in job["history"]]
                msgs.append({"role": "user", "content": job["message"]})
                try:
                    resp = await self.rt.complete(token, {"model": "whatever-the-worker-wants", "messages": msgs})
                except GatewayError as e:                         # a real worker's HTTP client sees this error and reports it
                    yield json.dumps({"t": "error", "message": e.message})
                    return
                text = resp["choices"][0]["message"]["content"]
            elif "say" in step:
                for part in (text[:6], text[6:]):
                    yield json.dumps({"t": "text", "delta": part})
                yield "this line is the framework's own print, not protocol"
                yield json.dumps({"t": "final", "text": text})
            elif "error" in step:
                yield json.dumps({"t": "error", "message": step["error"]})
            elif "wait" in step:
                await asyncio.sleep(30)
            elif "raise" in step:
                raise RuntimeError(step["raise"])

    def send(self, message="hello", agent="fwagent", **kw):
        events: list[dict] = []
        res = asyncio.run(self.s.chat.send(self.provider, None, message, kw.pop("session_id", None), None, agent, None, emit=events.append))
        self.events = events
        return res


# ---------------------------------------------------------------------------------------------------- the cases
def case_info_is_complete(h: Harness):
    info = FRAMEWORKS[h.fid]
    assert info.label and info.blurb and info.licence and info.homepage and info.strengths
    assert set(info.caps) == set(CAPS) and set(info.caps.values()) <= set(LEVELS)
    assert info.packages and all("==" in p for p in info.packages), "direct packages are pinned to an exact version"
    assert info.dist and info.module and info.size_mb > 0


def case_plain_turn_returns_the_models_answer(h: Harness):
    h.agent()
    assert h.send("ping")["reply"] == "answer: ping"


def case_text_is_streamed_in_order(h: Harness):
    h.agent()
    h.send("ping")
    deltas = [e["delta"] for e in h.events if e["type"] == "text"]
    assert deltas and "".join(deltas) == "answer: ping"


def case_the_chat_is_saved(h: Harness):
    h.agent()
    r = h.send("ping")
    rows = h.s.chat.store.messages(r["session_id"])
    assert [(m["role"], m["text"]) for m in rows if m["role"] in ("user", "bot")] == [("user", "ping"), ("bot", "answer: ping")]


def case_history_reaches_the_framework(h: Harness):
    h.agent()
    first = h.send("one")
    h.send("two", session_id=first["session_id"])
    assert [m["text"] for m in h.job["history"]] == ["one", "answer: one"]


def case_model_calls_go_through_the_guard_and_are_recorded(h: Harness):
    h.agent()
    r = h.send("ping")
    assert len(r["model_calls"]) == 1
    call = r["model_calls"][0]
    assert call["model"] == "m1" and call["agent"] == "fwagent" and call["usage_reported"] and call["input_tokens"] == 10
    assert r["usage"]["output_tokens"] == 5
    assert h.s.guard.status(h.s.providers.get("mock"))["spent_usd"] >= 0


def case_the_framework_cannot_pick_another_model(h: Harness):
    h.agent()
    seen = []
    h.rt.completion = fake_completion(lambda kw: seen.append(kw["model"]) or "ok")
    h.send("ping")
    assert seen and all("whatever-the-worker-wants" not in m for m in seen)       # the gateway used the agent's own model


def case_allowed_tools_run_and_are_recorded(h: Harness):
    h.agent(tools=["list_dir"])
    (h.tmp / "ws").mkdir(exist_ok=True)
    (h.tmp / "ws" / "hello.txt").write_text("hi")
    h.script = [{"tool": "list_dir", "args": {"path": "."}}, {"model": True}, {"say": True}]
    r = h.send("look")
    assert h.tool_results[0]["ok"] and "hello.txt" in json.dumps(h.tool_results[0])
    assert [(s["type"], s["tool"]) for s in r["steps"]] == [("call", "list_dir"), ("result", "list_dir")]
    assert [e["type"] for e in h.events if e["type"] in ("tool_call", "tool_result")] == ["tool_call", "tool_result"]


def case_tools_the_agent_was_not_given_do_not_exist(h: Harness):
    from app.frameworks.gateway import GatewayError
    h.agent(tools=["list_dir"])
    h.script = [{"model": True}, {"say": True}]
    h.send("x")
    with pytest.raises(GatewayError):                          # the run is over: its token no longer works
        asyncio.run(h.rt.call_tool(h.token, "list_dir", {}))

    h.script = [{"tool": "write_file", "args": {"path": "a.txt", "content": "x"}}]
    with pytest.raises(Exception) as ei:
        h.send("y")
    assert "no tool named 'write_file'" in str(ei.value) or getattr(ei.value, "status", 0) == 404


def case_tool_errors_go_back_to_the_model_and_the_turn_continues(h: Harness):
    h.agent(tools=["read_file"])
    h.script = [{"tool": "read_file", "args": {"path": "../../etc/passwd"}}, {"model": True}, {"say": True}]
    r = h.send("x")
    assert h.tool_results[0]["ok"] is False and r["reply"] == "answer: x"
    assert any(s["type"] == "result" and s["ok"] is False for s in r["steps"])


def case_a_spend_cap_stops_the_turn(h: Harness):
    from app.budget import BudgetExceeded
    h.agent()
    h.client.put("/api/providers/mock", json={"cap_usd": 0.0000001})
    h.rt.completion = fake_completion(usage=(1, 1))
    with pytest.raises((BudgetExceeded, Exception)) as ei:
        h.send("ping")
    assert "cap" in str(ei.value).lower() or "budget" in str(ei.value).lower() or isinstance(ei.value, BudgetExceeded) or h.events


def case_the_model_call_limit_protects_against_loops(h: Harness):
    h.agent()
    h.client.put("/api/settings", json={"max_llm_calls_per_turn": 1})
    h.script = [{"model": True}, {"model": True}, {"say": True}]
    r = h.send("loop")
    assert len(r["model_calls"]) == 1 and r["halted"]["error"] == "turn_limit" and "loop protection" in r["halted"]["message"]


def case_artifacts_are_kept(h: Harness):
    h.agent(tools=["save_artifact"])
    h.script = [{"tool": "save_artifact", "args": {"name": "note.md", "content": "# hi", "description": "d"}}, {"model": True}, {"say": True}]
    r = h.send("make a note")
    assert h.tool_results[0].get("ok", True) and any(e["type"] == "artifact" for e in h.events) or r["artifacts"]


def case_a_cancelled_turn_settles_everything(h: Harness):
    h.agent()
    h.script = [{"model": True}, {"wait": True}]

    async def go():
        t = asyncio.ensure_future(h.s.chat.send(h.provider, None, "x", None, None, "fwagent", None))
        await asyncio.sleep(0.3)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert h.rt.sessions == {} and h.launched == 1


def case_a_framework_error_is_reported_and_the_chat_survives(h: Harness):
    h.agent()
    h.script = [{"error": "the graph exploded"}]
    with pytest.raises(Exception) as ei:
        h.send("x")
    assert "graph exploded" in str(ei.value)
    sess = h.s.chat.store.list()[0]
    assert any(m["role"] == "user" for m in h.s.chat.store.messages(sess["id"]))
    assert h.rt.sessions == {}


def case_unsupported_features_are_warned_about(h: Harness):
    h.agent(mcp=["memory"])
    r = h.send("x")
    assert any("MCP" in w for w in r["warnings"])


def case_the_worker_gets_a_clean_environment(h: Harness, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    h.agent()
    h.send("x")
    assert "sk-should-not-leak" not in json.dumps(h.env) and "OPENAI_API_KEY" not in h.env
    if h.fid == "claude_sdk":                                 # the one framework that bills on its own account gets the agent's provider key, nothing else
        assert h.env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    else:
        assert "ANTHROPIC_API_KEY" not in h.env


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_")]
