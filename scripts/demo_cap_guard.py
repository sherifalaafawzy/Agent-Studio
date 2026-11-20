"""Demo: per-provider cap guard refusing an over-budget call. No network, no spend.
Run:  backend\.venv\Scripts\python scripts\demo_cap_guard.py
"""
import os, sys, tempfile, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ["AGENT_STUDIO_HOME"] = tempfile.mkdtemp(prefix="as-demo-")

import litellm
from fastapi.testclient import TestClient
from app.db import Database
from app.main import State, create_app

invocations = []
_orig = litellm.acompletion
async def spy(*a, **k):
    invocations.append(k.get("model")); return await _orig(*a, **k)
litellm.acompletion = spy

c = TestClient(create_app(State(Database())))
def line(s=""): print(s)
def show(tag, r):
    if r.status_code == 200:
        j = r.json(); u = j["usage"]
        print(f"  {tag:<34} 200 OK   cost ${u['cost_usd']:.4f}  (in {u['input_tokens']} / out {u['output_tokens']} tok)  "
              f"spent ${j['billing']['spent_usd']:.4f} / cap ${j['billing']['cap_usd']:.2f}")
    else:
        d = r.json()["detail"]
        print(f"  {tag:<34} {r.status_code} REFUSED  projected ${d['projected_usd']:.4f} > remaining ${d['remaining_usd']:.4f}  | model contacted: NO")

line("Provider 'mock' (model mock-expensive: $1000/M in, $2000/M out), cap = $0.25 total, max_output_tokens = 64")
line("Pre-flight rule: spent + reserved + projected(worst-case) <= cap, else refuse BEFORE the call.\n")
show("1. chat 'hello'", c.post("/api/chat", json={"provider": "mock", "message": "hello"}))
show("2. chat (longer message)", c.post("/api/chat", json={"provider": "mock", "message": "second message"}))
n_before = len(invocations)
show("3. chat (short message)", c.post("/api/chat", json={"provider": "mock", "message": "third message"}))
show("4. chat (short message)", c.post("/api/chat", json={"provider": "mock", "message": "fourth"}))
line("\nTighten the cap to $0.0001 in Settings (PUT /api/providers/mock) and try a fresh call:")
c.put("/api/providers/mock", json={"cap_usd": 0.0001})
show("5. chat 'hi' with tiny cap", c.post("/api/chat", json={"provider": "mock", "message": "hi"}))
line(f"\nProvider invocations reaching LiteLLM: {len(invocations)} (only the allowed calls; refusals never left the app)")
line("\nLedger (newest first):")
for r in c.get("/api/billing/ledger").json():
    print(f"  #{r['id']:<2} {r['kind']:<8} {r['provider']}/{r['model']:<15} cost ${r['cost_usd']:.4f}  projected ${r['projected_usd']:.4f}  {r['note'] or ''}")
line("\nPer-provider status:")
for p in c.get("/api/billing/summary").json()["providers"]:
    if p["calls"] or p["refused"]:
        print(f"  {p['provider']}: spent ${p['spent_usd']:.4f} of ${p['cap_usd']:.4f}, calls={p['calls']}, refused={p['refused']}")
