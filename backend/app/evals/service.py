"""Evaluation service: test sets, runs against an agent, results, and exports for other evaluation frameworks.

A run sends every case's prompt to the chosen agent as one chat turn (the agent's real tools, guardrails and spend limits apply),
checks the reply with the case's assertions, and keeps the reply, the cost and the time. The chat the probe created is deleted:
evaluations never fill up your chat history. Runs are sequential and can be cancelled between cases."""
from __future__ import annotations

import csv
import io
import json
import re
import time
import uuid

import litellm
import yaml

from ..budget import BudgetExceeded
from . import assertions as A

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
MAX_CASES, MAX_ASSERTIONS, MAX_PROMPT = 200, 10, 4000
EXPORTS = {"promptfoo": ("set", "yaml"), "inspect": ("set", "py"), "jsonl": ("both", "jsonl"), "csv": ("run", "csv"), "deepeval": ("run", "py"), "ragas": ("run", "json")}


class EvalError(ValueError):
    """A request the evaluation service refuses. Safe to show."""


def default_judge(chat, provider_id: str, model: str | None):
    p = chat.providers.get(provider_id)
    if p is None:
        raise EvalError(f"unknown judge provider '{provider_id}'")
    use_model = model or p.default_model
    from ..providers import litellm_target

    async def call(prompt: str) -> str:
        lite, kwargs = litellm_target(p, use_model)
        max_out = min(p.max_output_tokens, 200)
        r = chat.guard.reserve(p.id, use_model, len(prompt) // 3 + 50, max_out, lite)
        try:
            resp = await litellm.acompletion(model=lite, messages=[{"role": "user", "content": prompt}], max_tokens=max_out,
                                             **({"mock_response": "PASS the reply meets the criterion"} if p.kind == "mock" else {}), **kwargs)
        except BaseException:
            chat.guard.release(r, note="eval_judge_failed")
            raise
        u = getattr(resp, "usage", None)
        chat.guard.settle(r, getattr(u, "prompt_tokens", None) if u else None, getattr(u, "completion_tokens", None) if u else None, note="eval_judge")
        return (resp.choices[0].message.content or "").strip()
    return call


class EvalService:
    def __init__(self, db, chat, judge_factory=None):
        self.db, self.chat = db, chat
        self.judge_factory = judge_factory or (lambda cfg: default_judge(chat, cfg.get("provider", ""), cfg.get("model")))
        self.cancelled: set[str] = set()
        db.execute("UPDATE eval_runs SET status='interrupted', ended=? WHERE status='running'", (time.time(),))

    # ------------------------------------------------------------------ test sets
    def _set(self, r) -> dict:
        cases = json.loads(r["cases"])
        return {"id": r["id"], "name": r["name"], "description": r["description"], "cases": cases, "case_count": len(cases), "created": r["created"], "updated": r["updated"]}

    def sets(self) -> list[dict]:
        out = []
        for r in self.db.query("SELECT * FROM eval_sets ORDER BY updated DESC"):
            d = self._set(r)
            last = self.db.one("SELECT id, status, summary, started FROM eval_runs WHERE set_id=? ORDER BY started DESC LIMIT 1", (r["id"],))
            out.append({**{k: d[k] for k in ("id", "name", "description", "case_count", "updated")},
                        "last_run": {"id": last["id"], "status": last["status"], "started": last["started"], **json.loads(last["summary"] or "{}")} if last else None})
        return out

    def get_set(self, sid: str) -> dict:
        r = self.db.one("SELECT * FROM eval_sets WHERE id=?", (sid,))
        if r is None:
            raise KeyError(sid)
        return self._set(r)

    def save_set(self, body: dict) -> dict:
        sid = (body.get("id") or "").strip() or "set-" + uuid.uuid4().hex[:6]
        if not ID_RE.match(sid):
            raise EvalError("the id must be lowercase letters, digits, - or _, starting with a letter (2-41 characters)")
        old = self.db.one("SELECT * FROM eval_sets WHERE id=?", (sid,))
        name = str(body.get("name", old["name"] if old else sid)).strip()
        if not name or len(name) > 80:
            raise EvalError("give the test set a name (at most 80 characters)")
        desc = str(body.get("description", old["description"] if old else ""))[:300]
        raw = body.get("cases", json.loads(old["cases"]) if old else None)
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CASES:
            raise EvalError(f"a test set needs between 1 and {MAX_CASES} cases")
        cases, seen = [], set()
        for i, c in enumerate(raw, 1):
            if not isinstance(c, dict) or not isinstance(c.get("prompt"), str) or not c["prompt"].strip() or len(c["prompt"]) > MAX_PROMPT:
                raise EvalError(f"case {i}: write a prompt (at most {MAX_PROMPT} characters)")
            cid = str(c.get("id") or f"c{i}")
            if not re.match(r"^[A-Za-z0-9_-]{1,40}$", cid) or cid in seen:
                raise EvalError(f"case {i}: the id must be unique and use letters, digits, - or _")
            seen.add(cid)
            expect = c.get("expect") or []
            if not isinstance(expect, list) or len(expect) > MAX_ASSERTIONS:
                raise EvalError(f"case {i}: at most {MAX_ASSERTIONS} checks")
            try:
                cases.append({"id": cid, "prompt": c["prompt"], "expect": [A.clean(a) for a in expect]})
            except A.AssertionError_ as e:
                raise EvalError(f"case {i}: {e}") from e
        now = time.time()
        self.db.execute("INSERT INTO eval_sets(id,name,description,cases,created,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                        "description=excluded.description,cases=excluded.cases,updated=excluded.updated", (sid, name, desc, json.dumps(cases), now, now))
        return self.get_set(sid)

    def delete_set(self, sid: str) -> None:
        self.get_set(sid)
        self.db.execute("DELETE FROM eval_runs WHERE set_id=? AND status!='running'", (sid,))
        self.db.execute("DELETE FROM eval_sets WHERE id=?", (sid,))

    # ------------------------------------------------------------------ runs
    def begin(self, set_id: str, agent: str, provider: str, model: str | None, judge: dict | None) -> str:
        """Validate and record a run that is about to start. Returns its id."""
        total = len(self.get_set(set_id)["cases"])
        if self.chat.agents.get(agent) is None:
            raise EvalError(f"unknown agent '{agent}'")
        if self.chat.providers.get(provider) is None:
            raise EvalError(f"unknown provider '{provider}'")
        if judge is not None and (not isinstance(judge, dict) or self.chat.providers.get(judge.get("provider", "")) is None):
            raise EvalError("the judge needs a known provider")
        if self.db.one("SELECT 1 FROM eval_runs WHERE status='running'"):
            raise EvalError("another evaluation is still running - wait for it or cancel it")
        rid = uuid.uuid4().hex[:10]
        self.db.execute("INSERT INTO eval_runs(id,set_id,agent,provider,started,status,summary) VALUES(?,?,?,?,?,'running',?)",
                        (rid, set_id, agent, provider, time.time(), json.dumps({"model": model, "judge": judge, "total": total, "done": 0})))
        return rid

    async def execute(self, rid: str, model: str | None = None, judge: dict | None = None) -> dict:
        row = self.db.one("SELECT * FROM eval_runs WHERE id=?", (rid,))
        cases = self.get_set(row["set_id"])["cases"]
        results, status, stopped = [], "done", ""
        judge_call = self.judge_factory(judge) if judge else None
        for case in cases:
            if rid in self.cancelled:
                status = "cancelled"
                break
            r = await self._one(case, row, model, judge_call)
            results.append(r)
            self.db.execute("UPDATE eval_runs SET results=?, summary=? WHERE id=?",
                            (json.dumps(results), json.dumps({**self._summary(results), "model": model, "judge": judge, "total": len(cases), "done": len(results)}), rid))
            if r.get("stop"):
                status, stopped = "error", r["error"]
                break
        self.cancelled.discard(rid)
        summary = {**self._summary(results), "model": model, "judge": judge, "total": len(cases), "done": len(results), **({"stopped": stopped} if stopped else {})}
        self.db.execute("UPDATE eval_runs SET status=?, ended=?, summary=?, results=? WHERE id=?", (status, time.time(), json.dumps(summary), json.dumps(results), rid))
        return self.run(rid)

    async def _one(self, case: dict, row, model: str | None, judge_call) -> dict:
        t0 = time.time()
        out = {"id": case["id"], "prompt": case["prompt"][:300], "reply": "", "ok": False, "checks": [], "cost_usd": 0.0, "ms": 0, "blocked": False}
        try:
            res = await self.chat.send(row["provider"], model, case["prompt"], None, None, row["agent"], None)
        except BudgetExceeded as e:
            return {**out, "error": e.detail["message"], "stop": True, "ms": round((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001 - one broken case must not end the run
            return {**out, "error": f"{type(e).__name__}: {str(e)[:200]}", "ms": round((time.time() - t0) * 1000)}
        self.chat.store.delete(res["session_id"])
        reply = res.get("reply") or ""
        out.update(reply=reply[:2000], cost_usd=round((res.get("usage") or {}).get("cost_usd", 0.0), 6), ms=round((time.time() - t0) * 1000),
                   blocked=any(g.get("action") == "blocked" for g in res.get("guardrails") or []))
        for a in case["expect"]:
            if a["type"] == "llm_judge":
                if judge_call is None:
                    out["checks"].append({"type": "llm_judge", "ok": False, "detail": "no judge model was chosen for this run"})
                    continue
                try:
                    ok, why = A.parse_judge(await judge_call(A.JUDGE_PROMPT.format(criterion=a["value"], prompt=case["prompt"], reply=reply[:4000])))
                    out["checks"].append({"type": "llm_judge", "ok": ok, "detail": why or a["value"][:80]})
                except BudgetExceeded as e:
                    out["checks"].append({"type": "llm_judge", "ok": False, "detail": e.detail["message"]})
                except A.AssertionError_ as e:
                    out["checks"].append({"type": "llm_judge", "ok": False, "detail": str(e)})
            else:
                ok, why = A.check_exact(a, reply)
                out["checks"].append({"type": a["type"], "ok": ok, "detail": why})
        out["ok"] = all(c["ok"] for c in out["checks"]) and bool(reply.strip() or out["checks"])
        return out

    @staticmethod
    def _summary(results: list[dict]) -> dict:
        n = len(results)
        errors = sum(1 for r in results if r.get("error"))
        passed = sum(1 for r in results if r["ok"] and not r.get("error"))
        return {"passed": passed, "failed": n - passed - errors, "errors": errors, "pass_rate": round(100 * passed / n) if n else 0,
                "cost_usd": round(sum(r["cost_usd"] for r in results), 6), "avg_ms": round(sum(r["ms"] for r in results) / n) if n else 0,
                "blocked_by_guardrails": sum(1 for r in results if r["blocked"])}

    def cancel(self, rid: str) -> dict:
        row = self.db.one("SELECT status FROM eval_runs WHERE id=?", (rid,))
        if row is None:
            raise KeyError(rid)
        if row["status"] != "running":
            raise EvalError("this run is not running")
        self.cancelled.add(rid)
        return self.run(rid)

    def runs(self, set_id: str | None = None, limit: int = 30) -> list[dict]:
        where, args = (" WHERE set_id=?", (set_id,)) if set_id else ("", ())
        rows = self.db.query(f"SELECT * FROM eval_runs{where} ORDER BY started DESC LIMIT ?", (*args, max(1, min(limit, 200))))
        return [self._run(r, False) for r in rows]

    def run(self, rid: str) -> dict:
        r = self.db.one("SELECT * FROM eval_runs WHERE id=?", (rid,))
        if r is None:
            raise KeyError(rid)
        return self._run(r, True)

    @staticmethod
    def _run(r, full: bool) -> dict:
        d = {"id": r["id"], "set_id": r["set_id"], "agent": r["agent"], "provider": r["provider"], "started": r["started"], "ended": r["ended"], "status": r["status"],
             **json.loads(r["summary"] or "{}")}
        if full:
            d["results"] = json.loads(r["results"] or "[]")
        return d

    # ------------------------------------------------------------------ exports
    def export(self, kind: str, ident: str, fmt: str) -> tuple[str, str, str]:
        """(file name, media type, text) of a test set (kind 'set') or a finished run (kind 'run') in another framework's format."""
        if fmt not in EXPORTS:
            raise EvalError(f"format must be one of: {', '.join(EXPORTS)}")
        need, ext = EXPORTS[fmt]
        if kind not in ("set", "run") or need not in (kind, "both"):
            raise EvalError(f"{fmt} is exported from a {'finished run' if need == 'run' else 'test set'}")
        if kind == "set":
            s = self.get_set(ident)
            body = {"promptfoo": promptfoo, "inspect": inspect_task, "jsonl": jsonl_set}[fmt](s)
            name = s["id"]
        else:
            r = self.run(ident)
            body = {"deepeval": deepeval_cases, "ragas": ragas_rows, "csv": csv_rows, "jsonl": jsonl_run}[fmt](r, self.get_set(r["set_id"]))
            name = r["id"]
        media = {"yaml": "application/x-yaml", "py": "text/x-python", "jsonl": "application/x-ndjson", "csv": "text/csv", "json": "application/json"}[ext]
        return f"{name}-{fmt}.{ext}", media, body


# ---------------------------------------------------------------------------------------------------- export formats
PROMPTFOO_ASSERT = {"contains": "icontains", "not_contains": "not-icontains", "equals": "equals", "regex": "regex", "not_regex": "not-regex", "json_valid": "is-json", "llm_judge": "llm-rubric"}


def _pf_assert(a: dict) -> dict:
    if a["type"] in ("max_chars", "min_chars"):
        return {"type": "javascript", "value": f"output.length {'<=' if a['type'] == 'max_chars' else '>='} {a['value']}"}
    return {"type": PROMPTFOO_ASSERT[a["type"]], **({"value": a["value"]} if "value" in a else {})}


def promptfoo(s: dict) -> str:
    cfg = {"description": s["name"], "prompts": ["{{prompt}}"],
           "providers": [{"id": "https", "config": {"url": "http://127.0.0.1:8765/api/redteam/target/<token>/v1/chat/completions", "method": "POST",
                                                      "headers": {"Content-Type": "application/json"},
                                                      "body": {"messages": [{"role": "user", "content": "{{prompt}}"}]},
                                                      "transformResponse": "json.choices[0].message.content"}}],
           "tests": [{"description": c["id"], "vars": {"prompt": c["prompt"]}, "assert": [_pf_assert(a) for a in c["expect"]]} for c in s["cases"]]}
    return "# Point the provider url at any OpenAI-style endpoint for your agent (a red-team run gives you a temporary one).\n" + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)


def inspect_task(s: dict) -> str:
    samples = []
    for c in s["cases"]:
        target = next((a["value"] for a in c["expect"] if a["type"] == "contains"), "")
        samples.append({"id": c["id"], "input": c["prompt"], "target": target})
    return ('"""Inspect (UK AISI) task generated by Agent Studio. Run: inspect eval this_file.py --model <provider/model>"""\n'
            "from inspect_ai import Task, task\nfrom inspect_ai.dataset import Sample\nfrom inspect_ai.scorer import includes\nfrom inspect_ai.solver import generate\n\n"
            f"SAMPLES = {json.dumps(samples, indent=2, ensure_ascii=False)}\n\n\n"
            "@task\ndef agent_studio_eval():\n"
            "    return Task(dataset=[Sample(input=s['input'], target=s['target'], id=s['id']) for s in SAMPLES], solver=generate(), scorer=includes())\n\n\n"
            "# Only 'contains' checks become targets here; the other assertion types are not translated.\n")


def jsonl_set(s: dict) -> str:
    return "\n".join(json.dumps({"id": c["id"], "prompt": c["prompt"], "expect": c["expect"]}, ensure_ascii=False) for c in s["cases"]) + "\n"


def jsonl_run(r: dict, s: dict) -> str:
    return "\n".join(json.dumps({k: x[k] for k in ("id", "prompt", "reply", "ok", "checks", "cost_usd", "ms")}, ensure_ascii=False) for x in r["results"]) + "\n"


def csv_rows(r: dict, s: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "passed", "prompt", "reply", "checks", "cost_usd", "ms", "error"])
    for x in r["results"]:
        w.writerow([x["id"], x["ok"], x["prompt"], x["reply"], "; ".join(f"{c['type']}:{'ok' if c['ok'] else 'FAIL'}" for c in x["checks"]), x["cost_usd"], x["ms"], x.get("error", "")])
    return out.getvalue()


def deepeval_cases(r: dict, s: dict) -> str:
    rows = [{"input": x["prompt"], "actual_output": x["reply"]} for x in r["results"] if not x.get("error")]
    return ('"""DeepEval test cases generated by Agent Studio from a finished run. Run: deepeval test run this_file.py"""\n'
            "from deepeval import assert_test\nfrom deepeval.metrics import AnswerRelevancyMetric\nfrom deepeval.test_case import LLMTestCase\nimport pytest\n\n"
            f"ROWS = {json.dumps(rows, indent=2, ensure_ascii=False)}\n\n\n"
            "@pytest.mark.parametrize('row', ROWS)\ndef test_reply(row):\n"
            "    assert_test(LLMTestCase(input=row['input'], actual_output=row['actual_output']), [AnswerRelevancyMetric(threshold=0.7)])\n")


def ragas_rows(r: dict, s: dict) -> str:
    refs = {c["id"]: next((a["value"] for a in c["expect"] if a["type"] == "equals"), None) for c in s["cases"]}
    rows = [{"user_input": x["prompt"], "response": x["reply"], **({"reference": refs[x["id"]]} if refs.get(x["id"]) else {})} for x in r["results"] if not x.get("error")]
    return json.dumps({"note": "Rows for ragas.EvaluationDataset.from_list(). 'reference' appears only where the case has an 'equals' check.", "rows": rows}, indent=2, ensure_ascii=False)
