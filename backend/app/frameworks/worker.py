"""The worker that runs ONE agent turn on a framework (LangGraph, Strands, Pydantic AI, OpenAI Agents SDK, Microsoft Agent
Framework, Claude Agent SDK). It runs in that framework's isolated environment as `python worker.py <framework>`, so it uses only
the standard library plus the framework, and never imports Agent Studio.

Protocol: ONE JSON line on stdin (the job: gateway address, model name, instructions, history, message, tools), then JSON lines on
stdout: {"t": "text", "delta": "..."} while it answers, then {"t": "final", "text": "..."} or {"t": "error", "message": "..."}.
Models and tools are reached ONLY through the run's private gateway address (see gateway.py): the worker holds no provider key.

The framework adapters (`run_*`) are the only parts that call a third-party library; they were written against each project's
published quick-start and could not be exercised here without installing the frameworks (see the phase report), so they are
excluded from the coverage gate. Everything around them is tested.
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request

PROTOCOL = 1


def emit(obj: dict, out=None) -> None:
    (out or sys.stdout).write(json.dumps(obj, ensure_ascii=False) + "\n")
    (out or sys.stdout).flush()


def post_json(url: str, body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return {"ok": False, "error": json.loads(e.read().decode("utf-8")).get("error", {}).get("message", f"HTTP {e.code}")}
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {e.code}"}


def call_tool(job: dict, name: str, args: dict) -> str:
    """Run one of Agent Studio's tools through the bridge; the model sees the JSON result as text."""
    return json.dumps(post_json(job["gateway"] + "/tool", {"name": name, "args": args or {}}), ensure_ascii=False)


def history_text(job: dict) -> str:
    return "\n".join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}" for m in job.get("history") or [])


# ------------------------------------------------------------------------------------------------ framework adapters
async def run_langgraph(job: dict, say) -> str:  # pragma: no cover - needs the framework
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
    from langchain_core.tools import StructuredTool
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition

    tools = [StructuredTool(name=t["name"], description=t["description"], args_schema=t["parameters"],
                            func=(lambda _n: (lambda **kw: call_tool(job, _n, kw)))(t["name"])) for t in job["tools"]]
    llm = ChatOpenAI(model=job["model"], base_url=job["gateway"] + "/v1", api_key="run", streaming=True)
    llm = llm.bind_tools(tools) if tools else llm

    def agent(state: MessagesState):
        return {"messages": [llm.invoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_edge(START, "agent")
    if tools:
        g.add_node("tools", ToolNode(tools))
        g.add_conditional_edges("agent", tools_condition)
        g.add_edge("tools", "agent")
    else:
        g.add_edge("agent", END)
    graph = g.compile()
    msgs = [SystemMessage(content=job["instructions"])]
    for m in job["history"]:
        msgs.append(HumanMessage(content=m["text"]) if m["role"] == "user" else AIMessage(content=m["text"]))
    msgs.append(HumanMessage(content=job["message"]))
    final = ""
    async for chunk, meta in graph.astream({"messages": msgs}, {"recursion_limit": job["max_steps"] * 2 + 2}, stream_mode="messages"):
        if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "agent" and isinstance(chunk.content, str) and chunk.content:
            say(chunk.content)
            final += chunk.content
    return final


async def run_strands(job: dict, say) -> str:  # pragma: no cover - needs the framework
    from strands import Agent, tool
    from strands.models.openai import OpenAIModel

    def make(t: dict):
        @tool(name=t["name"], description=t["description"], inputSchema=t["parameters"])
        def fn(**kw):
            return call_tool(job, t["name"], kw)
        return fn

    model = OpenAIModel(client_args={"api_key": "run", "base_url": job["gateway"] + "/v1"}, model_id=job["model"])
    messages = [{"role": m["role"], "content": [{"text": m["text"]}]} for m in job["history"]]
    agent = Agent(model=model, system_prompt=job["instructions"], tools=[make(t) for t in job["tools"]], messages=messages, callback_handler=None)
    final = ""
    async for ev in agent.stream_async(job["message"]):
        if isinstance(ev, dict) and ev.get("data"):
            say(ev["data"])
            final += ev["data"]
    return final


async def run_pydantic_ai(job: dict, say) -> str:  # pragma: no cover - needs the framework
    from pydantic_ai import Agent, Tool
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    def make(t: dict):
        def fn(**kw):
            return call_tool(job, t["name"], kw)
        return Tool.from_schema(function=fn, name=t["name"], description=t["description"], json_schema=t["parameters"], takes_ctx=False)

    model = OpenAIChatModel(job["model"], provider=OpenAIProvider(base_url=job["gateway"] + "/v1", api_key="run"))
    agent = Agent(model, instructions=job["instructions"], tools=[make(t) for t in job["tools"]])
    hist = [ModelRequest(parts=[UserPromptPart(content=m["text"])]) if m["role"] == "user" else ModelResponse(parts=[TextPart(content=m["text"])])
            for m in job["history"]]
    final = ""
    async with agent.run_stream(job["message"], message_history=hist) as result:
        async for delta in result.stream_text(delta=True):
            say(delta)
            final += delta
    return final


async def run_openai_agents(job: dict, say) -> str:  # pragma: no cover - needs the framework
    from agents import Agent, FunctionTool, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
    from openai import AsyncOpenAI
    from openai.types.responses import ResponseTextDeltaEvent

    set_tracing_disabled(True)                                  # nothing is sent to OpenAI's tracing service

    def make(t: dict):
        async def invoke(_ctx, args_json: str) -> str:
            return await asyncio.to_thread(call_tool, job, t["name"], json.loads(args_json or "{}"))
        return FunctionTool(name=t["name"], description=t["description"], params_json_schema=t["parameters"], on_invoke_tool=invoke, strict_json_schema=False)

    client = AsyncOpenAI(base_url=job["gateway"] + "/v1", api_key="run")
    agent = Agent(name="agent", instructions=job["instructions"], model=OpenAIChatCompletionsModel(model=job["model"], openai_client=client),
                  tools=[make(t) for t in job["tools"]])
    inp = [{"role": m["role"], "content": m["text"]} for m in job["history"]] + [{"role": "user", "content": job["message"]}]
    result = Runner.run_streamed(agent, input=inp, max_turns=job["max_steps"])
    async for ev in result.stream_events():
        if ev.type == "raw_response_event" and isinstance(ev.data, ResponseTextDeltaEvent):
            say(ev.data.delta)
    return str(result.final_output or "")


async def run_ms_agent(job: dict, say) -> str:  # pragma: no cover - needs the framework
    from agent_framework import Agent, tool
    from agent_framework.openai import OpenAIChatClient

    def make(t: dict):
        @tool(name=t["name"], description=t["description"], schema=t["parameters"])
        def fn(**kw) -> str:
            return call_tool(job, t["name"], kw)
        return fn

    client = OpenAIChatClient(api_key="run", model=job["model"], base_url=job["gateway"] + "/v1")
    agent = Agent(client=client, instructions=job["instructions"], tools=[make(t) for t in job["tools"]])
    prompt = (history_text(job) + "\n\n" if job["history"] else "") + job["message"]
    final = ""
    async for update in agent.run(prompt, stream=True):
        if getattr(update, "text", None):
            say(update.text)
            final += update.text
    return final


async def run_claude_sdk(job: dict, say) -> str:  # pragma: no cover - needs the framework
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, create_sdk_mcp_server, query, tool)

    def make(t: dict):
        @tool(t["name"], t["description"], t["parameters"])
        async def fn(args):
            return {"content": [{"type": "text", "text": await asyncio.to_thread(call_tool, job, t["name"], args)}]}
        return fn

    server = create_sdk_mcp_server(name="studio", version="1.0.0", tools=[make(t) for t in job["tools"]])
    builtin = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task", "TodoWrite"]
    opts = ClaudeAgentOptions(system_prompt=job["instructions"], mcp_servers={"studio": server}, max_turns=job["max_steps"],
                              allowed_tools=[f"mcp__studio__{t['name']}" for t in job["tools"]], disallowed_tools=builtin,
                              permission_mode="default", cwd=job.get("workspace") or None, model=job["model"] or None)
    prompt = (history_text(job) + "\n\n" if job["history"] else "") + job["message"]
    final = ""
    async for m in query(prompt=prompt, options=opts):
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, TextBlock):
                    say(b.text)
        elif isinstance(m, ResultMessage):
            final = m.result or final
    return final


RUNNERS = {"langgraph": run_langgraph, "strands": run_strands, "pydantic_ai": run_pydantic_ai, "openai_agents": run_openai_agents,
           "ms_agent": run_ms_agent, "claude_sdk": run_claude_sdk}


async def serve(fid: str, stdin, stdout, runners: dict | None = None) -> int:
    """Read the job, run it, report. Returns the process exit code."""
    runner = (runners or RUNNERS).get(fid)
    if runner is None:
        emit({"t": "error", "message": f"unknown framework '{fid}'"}, stdout)
        return 2
    try:
        job = json.loads(stdin.readline())
    except ValueError:
        emit({"t": "error", "message": "the job was not valid JSON"}, stdout)
        return 2
    if job.get("protocol") != PROTOCOL:
        emit({"t": "error", "message": "worker and Agent Studio disagree about the protocol version"}, stdout)
        return 2
    streamed: list[str] = []

    def say(delta: str) -> None:
        streamed.append(delta)
        emit({"t": "text", "delta": delta}, stdout)

    try:
        final = await runner(job, say)
    except Exception as e:  # noqa: BLE001 - reported to Agent Studio, which shows it in the chat
        emit({"t": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"}, stdout)
        return 1
    emit({"t": "final", "text": final or "".join(streamed)}, stdout)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: worker.py <framework>", file=sys.stderr)
        return 2
    return asyncio.run(serve(argv[1], sys.stdin, sys.stdout))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
