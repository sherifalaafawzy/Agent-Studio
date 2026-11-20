"""Mock provider model: free, offline, deterministic - so the harness, sub-agents, sandbox and billing
guard can be demonstrated and tested with zero spend.

Scripting: a user message of lines starting with '!' makes the model emit tool calls, one per step:

    !tool read_file {"path": "a.py"}
    !tool edit_file {"path": "a.py", "old_text": "x", "new_text": "y"}
    !agent explorer {"request": "list the files"}      # delegate to a sub-agent (AgentTool)

After the last scripted step it answers with a summary of the results. Anything else is plain chat and goes
through LiteLLM (mock_response), exactly like Phase 1, so usage/cost accounting is real LiteLLM output.
"""
from __future__ import annotations

import json

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def _usage(prompt_chars: int, out_chars: int) -> types.GenerateContentResponseUsageMetadata:
    p, o = max(1, prompt_chars // 4), max(1, out_chars // 4)
    return types.GenerateContentResponseUsageMetadata(prompt_token_count=p, candidates_token_count=o, total_token_count=p + o)


def _script(text: str) -> list[tuple[str, dict]] | None:
    steps = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("!"):
            continue
        parts = line[1:].split(None, 2)
        if len(parts) < 2:
            continue
        kind, name = parts[0], parts[1]
        if kind not in ("tool", "agent"):
            continue
        try:
            args = json.loads(parts[2]) if len(parts) > 2 else {}
        except json.JSONDecodeError:
            args = {"_bad_json": parts[2]}
        steps.append((name, args))
    return steps or None


class MockLlm(LiteLlm):
    async def generate_content_async(self, llm_request, stream: bool = False):
        contents = llm_request.contents or []
        # latest real user text + number of tool results produced since
        user_text, results, chars = "", [], 0
        for c in contents:
            for pt in c.parts or []:
                chars += len(pt.text or "")
        last_user = max((i for i, c in enumerate(contents) if c.role == "user" and any(p.text for p in c.parts or [])), default=-1)
        if last_user >= 0:
            user_text = "".join(p.text or "" for p in contents[last_user].parts or [])
            for c in contents[last_user + 1:]:
                for pt in c.parts or []:
                    if pt.function_response:
                        results.append((pt.function_response.name, pt.function_response.response))
        steps = _script(user_text)
        if steps is not None:
            if len(results) < len(steps):
                name, args = steps[len(results)]
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]),
                                  usage_metadata=_usage(chars, 40), turn_complete=True)
            else:
                summary = "; ".join(f"{n} -> {json.dumps(r, default=str)[:160]}" for n, r in results)
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=f"[mock] done. {summary}")]),
                                  usage_metadata=_usage(chars, len(summary)), turn_complete=True)
            return
        self._additional_args["mock_response"] = f"[mock:{self.model}] You said: {user_text[:300]}"
        async for r in super().generate_content_async(llm_request, stream=stream):
            yield r
