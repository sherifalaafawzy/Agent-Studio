"""A scripted ACP *agent* for tests/demos (no model, no network, no spend).
Prompt grammar (everything else is echoed):
  !perm <kind>|<title>          ask the client for permission (tool kind = read|edit|execute|fetch|...)
  !write <path>|<content>       permission (kind=edit) then fs/write_text_file via the client
  !read <path>                  fs/read_text_file via the client
  !ask                          cursor/ask_question extension (blocking)
  !plan                         cursor/create_plan extension (blocking)
  !env <NAME>                   report whether NAME is in this process' environment
  !slow                         run ~20s unless cancelled
  !die                          exit the process abruptly
Env: FAKE_AUTH=1 -> session/new requires authenticate("fake_login") first.
"""
import asyncio
import os
import sys

import acp
from acp import schema as S


class FakeAgent:
    def __init__(self):
        self.conn = None
        self.authed = os.environ.get("FAKE_AUTH") != "1"
        self.cancel_flags: set[str] = set()
        self.mode = "default"

    def on_connect(self, conn):
        self.conn = conn

    async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kw):
        self.caps = client_capabilities
        methods = [S.AuthMethodAgent(id="fake_login", name="Fake login"), S.AuthMethodAgent(id="gemini-api-key", name="API key")] if os.environ.get("FAKE_AUTH") == "1" else []
        return S.InitializeResponse(protocol_version=protocol_version, auth_methods=methods,
                                    agent_info=S.Implementation(name="fake-acp-agent", title="Fake Agent", version="9.9.9"))

    async def authenticate(self, method_id, **kw):
        if method_id == "gemini-api-key" and not os.environ.get("GEMINI_API_KEY"):
            raise acp.RequestError.auth_required()
        self.auth_method = method_id
        self.authed = True
        return S.AuthenticateResponse()

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kw):
        if not self.authed:
            raise acp.RequestError.auth_required()
        modes = S.SessionModeState(current_mode_id="default", available_modes=[
            S.SessionMode(id="default", name="Default"), S.SessionMode(id="plan", name="Plan")])
        return S.NewSessionResponse(session_id="fake-" + os.urandom(3).hex(), modes=modes)

    async def set_session_mode(self, session_id, mode_id, **kw):
        self.mode = mode_id
        return S.SetSessionModeResponse()

    async def cancel(self, session_id, **kw):
        self.cancel_flags.add(session_id)

    async def _say(self, sid, text):
        await self.conn.session_update(session_id=sid, update=S.AgentMessageChunk(content=S.TextContentBlock(text=text)))

    async def _perm(self, sid, kind, title):
        tc = S.ToolCallUpdate(tool_call_id="tc-" + os.urandom(2).hex(), title=title, kind=kind, raw_input={"title": title})
        await self.conn.session_update(session_id=sid, update=S.ToolCallStart(tool_call_id=tc.tool_call_id, title=title, kind=kind, status="pending"))
        r = await self.conn.request_permission(session_id=sid, tool_call=tc, options=[
            S.PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
            S.PermissionOption(option_id="reject-once", name="Reject", kind="reject_once")])
        ok = getattr(r.outcome, "outcome", None) == "selected" and r.outcome.option_id == "allow-once"
        await self.conn.session_update(session_id=sid, update=S.ToolCallProgress(
            tool_call_id=tc.tool_call_id, status="completed" if ok else "failed"))
        return ok, r.outcome

    async def prompt(self, prompt, session_id, **kw):
        text = "".join(getattr(b, "text", "") for b in prompt).strip()
        if text.startswith("!perm "):
            kind, _, title = text[6:].partition("|")
            ok, out = await self._perm(session_id, kind.strip(), title.strip() or "do a thing")
            await self._say(session_id, f"perm:{'allowed' if ok else 'denied'}:{getattr(out, 'outcome', '')}")
        elif text.startswith("!write "):
            path, _, content = text[7:].partition("|")
            ok, _ = await self._perm(session_id, "edit", f"write {path}")
            if ok:
                try:
                    await self.conn.write_text_file(session_id=session_id, path=path, content=content)
                    await self._say(session_id, f"wrote {path}")
                except acp.RequestError as e:
                    await self._say(session_id, f"write-failed: {e}")
            else:
                await self._say(session_id, "write-denied")
        elif text.startswith("!read "):
            try:
                r = await self.conn.read_text_file(session_id=session_id, path=text[6:].strip())
                await self._say(session_id, "content:" + r.content)
            except acp.RequestError as e:
                await self._say(session_id, f"read-failed: {e}")
        elif text == "!ask":
            r = await self.conn.ext_method("cursor/ask_question", {"toolCallId": "q", "title": "pick", "questions": [
                {"id": "q1", "prompt": "mode?", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}]})
            await self._say(session_id, f"asked:{r}")
        elif text == "!plan":
            r = await self.conn.ext_method("cursor/create_plan", {"toolCallId": "p", "name": "P", "plan": "1. do", "todos": []})
            await self._say(session_id, f"plan:{r}")
        elif text.startswith("!env "):
            await self._say(session_id, f"env:{text[5:].strip()}={'present' if text[5:].strip() in os.environ else 'absent'}")
        elif text == "!authmethod":
            await self._say(session_id, f"auth:{getattr(self, 'auth_method', None)}")
        elif text == "!slow":
            for _ in range(200):
                if session_id in self.cancel_flags:
                    self.cancel_flags.discard(session_id)
                    return S.PromptResponse(stop_reason="cancelled")
                await asyncio.sleep(0.1)
            await self._say(session_id, "slow-done")
        elif text == "!die":
            os._exit(3)
        else:
            await self._say(session_id, "echo: ")
            await self._say(session_id, text)
        await self.conn.session_update(session_id=session_id, update=S.UsageUpdate(
            used=120, size=200000, cost=S.Cost(amount=0.0042, currency="USD")))
        return S.PromptResponse(stop_reason="end_turn", usage=S.Usage(total_tokens=30, input_tokens=20, output_tokens=10))


if __name__ == "__main__":
    asyncio.run(acp.run_agent(FakeAgent()))
    sys.exit(0)
