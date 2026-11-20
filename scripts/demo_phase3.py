"""Phase 3 demo: Agent Studio as an ACP client.
  backend\\.venv\\Scripts\\python scripts\\demo_phase3.py               offline scenarios (scripted fake agent) + probe of installed agents
  backend\\.venv\\Scripts\\python scripts\\demo_phase3.py --live claude  also send ONE tiny read-only prompt to that real agent
                                                                       (this uses YOUR account/quota for that vendor; not capped by Agent Studio)
"""
import asyncio, json, os, sys, tempfile, warnings
sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "..", "backend"))
os.environ["AGENT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="as-demo3-")

from app.db import Database
from app.main import State

FAKE = os.path.join(ROOT, "..", "backend", "tests", "fake_acp_agent.py")
st = State(Database())
ws = os.path.join(os.environ["AGENT_STUDIO_HOME"], "ws")
st.db.set_setting("workspace_dir", ws)
st.acp.store.create("fake", "Scripted fake agent", sys.executable, [FAKE])


def H(t): print(f"\n=== {t} ===")


def show(ev):
    t = ev["type"]
    if t == "text": print("   text   ", repr(ev["text"][:100]))
    elif t == "tool_call": print(f'   tool   {ev["kind"]}: {ev["title"][:80]}')
    elif t == "tool_update": print(f'   tool✓  {ev["status"]} {str(ev.get("output") or "")[:80]}')
    elif t == "permission": print(f'   ASK    permission: {ev["title"]} ({ev["kind"]}) options={[o["id"] for o in ev["options"]]}')
    elif t == "auto": print(f'   auto   {ev["decision"]} {ev["title"]} (policy {ev["policy"]})')
    elif t == "fs_write": print(f'   fs     wrote {ev["path"]} ({ev["bytes"]} bytes, inside workspace)')
    elif t == "usage": print(f'   usage  context {ev["used"]}/{ev["size"]} cost={ev["cost"]} {ev["currency"]}')


async def run_turn(sess, text, policy="ask", answer=None):
    def emit(ev):
        show(ev)
        if answer and ev["type"] in ("permission", "question", "plan_approval"):
            asyncio.get_running_loop().call_soon(lambda: st.acp.respond(ev["request_id"], answer(ev)))
    return await st.acp.prompt(sess, text, emit, policy=policy)


async def main():
    H("1. Streamed turn from an ACP agent over real stdio JSON-RPC")
    s = await st.acp.get_or_open("fake", None, None)
    print("   agent:", s.info["title"], s.info["version"], "| modes:", [m["id"] for m in s.modes["available"]])
    r = await run_turn(s, "hello")
    print("   turn result:", {k: r[k] for k in ("stop_reason", "usage", "reported_cost")})

    H("2. Permission flow: the agent must ask; nothing is written until you approve")
    await run_turn(s, "!write notes/a.txt|approved", answer=lambda ev: {"option_id": "reject-once"})
    print("   after REJECT, file exists:", os.path.exists(os.path.join(ws, "notes", "a.txt")))
    await run_turn(s, "!write notes/a.txt|approved", answer=lambda ev: {"option_id": "allow-once"})
    print("   after APPROVE, file content:", repr(open(os.path.join(ws, "notes", "a.txt")).read()))

    H("3. Workspace jail on delegated file access (path traversal refused)")
    open(os.path.join(ws, "..", "outside.txt"), "w").write("secret")
    await run_turn(s, "!read ../outside.txt")
    await run_turn(s, "!write ../escape.txt|x", answer=lambda ev: {"option_id": "allow-once"})
    print("   escape.txt created outside workspace:", os.path.exists(os.path.join(ws, "..", "escape.txt")))

    H("4. Policies: readonly auto-allows reads, auto-rejects everything else (UI never asked)")
    await run_turn(s, "!perm read|read a file", policy="readonly")
    await run_turn(s, "!perm execute|rm -rf /", policy="readonly")

    H("5. Environment hygiene: our provider keys are not inherited by the agent process")
    os.environ["OPENAI_API_KEY"] = "sk-should-not-leak"
    await run_turn(s, "!env OPENAI_API_KEY")
    await st.acp.close_all()

    H("6. Usage ledger (what agents REPORT; the spend caps do not apply to external agents)")
    for u in st.acp.store.usage():
        print("  ", u)

    H("7. Which real agents are installed here? (probe = start, initialize, open a session, close)")
    for a in st.acp.store.list():
        if a["id"] == "fake":
            continue
        if not a["installed"]:
            print(f'   {a["label"]:<12} not installed  (needs `{a["command"]}` on PATH) - {a["notes"][:70]}')
            continue
        r = await st.acp.probe(a["id"]) if True else None
        print(f'   {a["label"]:<12} ', {k: r.get(k) for k in ("ok", "title", "version", "protocol", "auth_methods", "error")},
              "| modes:", [m["id"] for m in (r.get("modes") or {}).get("available", [])])

    if "--live" in sys.argv:
        aid = sys.argv[sys.argv.index("--live") + 1]
        H(f"8. LIVE: one tiny read-only prompt to the real '{aid}' agent (uses your account)")
        s = await st.acp.get_or_open(aid, None, None)
        r = await run_turn(s, "Reply with exactly the single word: OK. Do not use any tools.", policy="readonly")
        print("   result:", {k: r[k] for k in ("stop_reason", "usage", "reported_cost", "seconds")})
        await st.acp.close_all()

asyncio.run(main())
