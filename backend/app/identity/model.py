"""What an agent's identity settings look like (stored in `agents.identity`) and the scopes Agent Studio itself understands."""
from __future__ import annotations

import re

SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,40}$")
AGENT_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
# the scopes the app itself enforces or records; any other scope name is allowed too (it is just carried in the token)
KNOWN_SCOPES = {
    "files:read": "read files in the workspace",
    "files:write": "change files in the workspace",
    "shell:run": "run commands in the sandbox",
    "web:fetch": "fetch web pages",
    "memory:read": "read what is remembered",
    "memory:write": "save to memory",
    "artifacts:write": "keep results as artifacts",
    "agents:delegate": "hand work to other agents",
    "mcp:call": "call MCP servers",
}
MAX_SCOPES = 30


def clean_identity(v) -> dict:
    """Validate an agent's identity settings; {} = defaults. Raises ValueError with a plain explanation."""
    if v in (None, {}):
        return {}
    if not isinstance(v, dict) or set(v) - {"scopes", "delegates"}:
        raise ValueError("identity may only contain 'scopes' and 'delegates'")
    out: dict = {}
    for key, rx, what in (("scopes", SCOPE_RE, "scope"), ("delegates", AGENT_RE, "agent id")):
        if key not in v:                       # missing = automatic (derived from the agent's tools / sub-agents); [] = nothing
            continue
        items = v[key]
        if not isinstance(items, list) or len(items) > MAX_SCOPES or any(not isinstance(x, str) or not rx.match(x) for x in items):
            raise ValueError(f"identity {key} must be a list of at most {MAX_SCOPES} valid {what}s")
        out[key] = list(dict.fromkeys(items))
    return out
