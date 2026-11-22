"""Phase 2 demo (no network, no spend): sub-agents + Pro/Flash tiering, diff edit + mandatory verify/rollback,
Docker sandbox isolation, fail-closed behaviour, skill authoring, MCP approval gate, guard halting a turn.
Run:  backend\.venv\Scripts\python scripts\demo_phase2.py
"""
import json, os, shutil, sys, tempfile, warnings
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ["AGENT_STUDIO_ENABLE_MOCK"] = "1"   # demos run on the offline mock provider
os.environ["AGENT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="as-demo2-")

from fastapi.testclient import TestClient
from app.db import Database
from app.main import State, create_app
from app.harness import sandbox as sbx

st = State(Database())
c = TestClient(create_app(st))
ws = os.path.join(os.environ["AGENT_STUDIO_HOME"], "ws")
os.makedirs(ws)
open(os.path.join(ws, "calc.py"), "w").write("def add(a, b):\n    return a - b\n")
open(os.path.join(ws, "notes.txt"), "w").write("hello\n")
c.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
c.put("/api/settings", json={"workspace_dir": ws})


def chat(agent, msg, **kw):
    r = c.post("/api/chat", json={"provider": "mock", "agent": agent, "message": msg, **kw})
    return r.status_code, r.json()


def H(t): print(f"\n=== {t} ===")


print("sandbox:", sbx.status())

H("1. Orchestrator (Pro tier) delegates to isolated sub-agent (Flash tier)")
code, j = chat("engineer", '!agent explorer {"request": "!tool list_dir {\\"path\\": \\".\\"}"}')
for s in j["steps"]:
    print(("  ▶ " if s["type"] == "call" else "      ◀ ") + f'{s["agent"]}.{s["tool"]}  {(s.get("args") or s.get("result") or "")[:90]}')
for m in j["model_calls"]:
    print(f'  $ {m["agent"]:<9} model={m["model"]:<14} cost ${m["cost_usd"]:.4f}')

H("2. edit_file: diff-based edit, auto-verified")
code, j = chat("coder", '!tool read_file {"path": "calc.py"}\n!tool edit_file {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"}')
r = [s for s in j["steps"] if s["type"] == "result" and s["tool"] == "edit_file"][0]["result"]
print("  result:", r[:260])
print("  file now:", repr(open(os.path.join(ws, "calc.py")).read()))

H("3. edit_file with a SYNTAX ERROR -> rolled back automatically")
code, j = chat("coder", '!tool read_file {"path": "calc.py"}\n!tool edit_file {"path": "calc.py", "old_text": "return a + b", "new_text": "return (a + b"}')
r = [s for s in j["steps"] if s["type"] == "result" and s["tool"] == "edit_file"][0]["result"]
print("  result:", r[:300])
print("  file after (unchanged):", repr(open(os.path.join(ws, "calc.py")).read()))

H("4. Lint (ruff in the sandbox) catches an undefined name -> rolled back")
code, j = chat("coder", '!tool write_file {"path": "bad.py", "content": "def f():\\n    return nope\\n"}')
r = [s for s in j["steps"] if s["type"] == "result"][0]["result"]
print("  result:", r[:330]); print("  bad.py exists:", os.path.exists(os.path.join(ws, "bad.py")))

H("5. run_shell executes ONLY in the Docker sandbox (non-root, no network, host paths invisible)")
code, j = chat("coder", '!tool run_shell {"command": "id -u; python -c \\"import urllib.request as u; u.urlopen(\'http://1.1.1.1\', timeout=3)\\" 2>&1 | tail -1; ls /c /h 2>&1 | head -2"}')
print("  ", [s["result"] for s in j["steps"] if s["type"] == "result"][0][:400])

H("6. Fail-closed: Docker unavailable -> shell REFUSED, nothing runs on the host")
orig = shutil.which
shutil.which = lambda *a, **k: None
code, j = chat("coder", '!tool run_shell {"command": "echo LEAK > leaked.txt"}')
shutil.which = orig
print("  ", [s["result"] for s in j["steps"] if s["type"] == "result"][0][:200])
print("  leaked.txt on host:", os.path.exists(os.path.join(ws, "leaked.txt")))

H("7. Agent-authored skill + MCP proposal (needs human approval)")
code, j = chat("engineer", '!tool create_skill {"name": "release-notes", "description": "Draft release notes from git log. Use when asked for a changelog.", "body": "1. git log\\n2. group by type", "mcp_servers": ["git"]}\n'
                          '!tool propose_mcp_server {"id": "sqlite", "label": "SQLite", "description": "query local db", "command": "uvx", "args": ["mcp-server-sqlite==1.0.0"]}')
for s in j["steps"]:
    if s["type"] == "result": print("  ◀", s["tool"], s["result"][:170])
print("  proposed server ready to run?", st.mcp.get("sqlite").ready(), "(status:", st.mcp.get("sqlite").status + ")")

H("8. Billing guard halts a whole agent turn (sub-agent on a provider with a tiny cap)")
c.put("/api/providers/openai", json={"enabled": True, "api_key": "sk-never-sent", "cap_usd": 0.0001})
c.post("/api/agents", json={"id": "pricey", "name": "Pricey", "provider": "openai", "tier": "pro", "tools": ["list_dir"]})
c.post("/api/agents", json={"id": "boss", "name": "Boss", "sub_agents": ["pricey"]})
code, j = chat("boss", '!agent pricey {"request": "hi"}')
print(f"  HTTP {code}; halted: {j['halted']['error']} on {j['halted']['provider']} (projected ${j['halted']['projected_usd']:.4f} > remaining ${j['halted']['remaining_usd']:.4f})")
print("  model calls that were actually made:", [(m['agent'], m['model']) for m in j["model_calls"]])
for p in c.get("/api/billing/summary").json()["providers"]:
    if p["provider"] in ("mock", "openai"): print(f"  {p['provider']}: spent ${p['spent_usd']:.4f} / cap ${p['cap_usd']:.4f}, calls={p['calls']}, refused={p['refused']}")

for cid in os.popen("docker ps -aq --filter label=agent-studio=1").read().split(): os.system(f"docker rm -f {cid} >nul 2>&1")
