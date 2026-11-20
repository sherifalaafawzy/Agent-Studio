"""Smoke-test the default-on MCP servers for real (downloads pinned packages via npx/uvx on first run).
Run:  backend\.venv\Scripts\python scripts\check_mcp.py [server ids...]
"""
import asyncio, os, sys, tempfile, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("AGENT_STUDIO_HOME", tempfile.mkdtemp(prefix="as-mcp-"))

from app.db import Database
from app.mcp_registry import McpStore, build_toolsets


async def main(ids):
    store = McpStore(Database())
    ws = tempfile.mkdtemp(prefix="as-ws-")
    __import__("subprocess").run(["git", "init", "-q", ws])  # the git server requires a repository
    for sid in ids:
        warnings_: list[str] = []
        sets = await build_toolsets(store, [sid], ws, warnings_)
        if not sets:
            print(f"[FAIL] {sid}: {warnings_}")
            continue
        tools = await sets[0].get_tools()
        print(f"[ OK ] {sid}: {len(tools)} tools -> {[t.name for t in tools][:6]}")
        await sets[0].close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["filesystem", "fetch", "git", "memory"]))
