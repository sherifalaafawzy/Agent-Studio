"use client";
// Evaluations: test sets (prompts with pass/fail checks), runs against an agent, results, and exports for other evaluation frameworks.
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import InfoTip from "@/components/InfoTip";
import { toast } from "@/components/Nav";
import EvalStarters from "@/components/EvalStarters";

type Assertion = { type: string; value?: string | number };
type Case = { id: string; prompt: string; expect: Assertion[] };
type SetRow = { id: string; name: string; description: string; case_count: number; last_run: { id: string; status: string; pass_rate?: number } | null };
type SetFull = { id: string; name: string; description: string; cases: Case[] };
type Result = { id: string; prompt: string; reply: string; ok: boolean; checks: { type: string; ok: boolean; detail: string }[]; cost_usd: number; ms: number; blocked: boolean; error?: string };
type Run = { id: string; set_id: string; agent: string; status: string; started: number; total?: number; done?: number; passed?: number; failed?: number; errors?: number; pass_rate?: number; cost_usd?: number; stopped?: string; results?: Result[] };

const TYPES: [string, string][] = [["contains", "contains"], ["not_contains", "does not contain"], ["equals", "equals"], ["regex", "matches pattern"], ["not_regex", "does not match pattern"],
  ["json_valid", "is valid JSON"], ["max_chars", "at most … characters"], ["min_chars", "at least … characters"], ["llm_judge", "a judge model agrees that…"]];
const NUMERIC = ["max_chars", "min_chars"];
const blankSet = (): SetFull => ({ id: "", name: "", description: "", cases: [{ id: "c1", prompt: "", expect: [] }] });

export default function EvalsPage() {
  const [sets, setSets] = useState<SetRow[]>([]);
  const [sel, setSel] = useState("");
  const [f, setF] = useState<SetFull | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [provs, setProvs] = useState<{ id: string; label: string; enabled: boolean }[]>([]);
  const [rf, setRf] = useState({ agent: "", provider: "", model: "", judge: "" });
  const [err, setErr] = useState("");

  const loadSets = useCallback(() => api<{ sets: SetRow[] }>("/api/evals/sets").then((r) => setSets(r.sets)).catch((e) => setErr(e.message)), []);
  useEffect(() => {
    loadSets();
    api<{ id: string; name: string }[]>("/api/agents").then(setAgents).catch(() => setAgents([]));
    api<{ id: string; label: string; enabled: boolean }[]>("/api/providers").then(setProvs).catch(() => setProvs([]));
  }, [loadSets]);
  const open = async (id: string) => {
    setSel(id); setIsNew(false); setRun(null); setErr("");
    try { setF(await api<SetFull>(`/api/evals/sets/${id}`)); setRuns((await api<{ runs: Run[] }>(`/api/evals/runs?set_id=${id}`)).runs); } catch (e) { setErr((e as Error).message); }
  };
  const running = run?.status === "running";
  useEffect(() => {
    if (!running || !run) return;
    const t = setInterval(() => api<Run>(`/api/evals/runs/${run.id}`).then((r) => { setRun(r); if (r.status !== "running") { loadSets(); api<{ runs: Run[] }>(`/api/evals/runs?set_id=${r.set_id}`).then((x) => setRuns(x.runs)); } }).catch(() => {}), 1500);
    return () => clearInterval(t);
  }, [running, run, loadSets]);

  const save = async () => {
    if (!f) return;
    try {
      const saved = await api<SetFull>("/api/evals/sets", { method: "PUT", body: JSON.stringify({ id: f.id || undefined, name: f.name, description: f.description, cases: f.cases }) });
      toast("Test set saved"); setIsNew(false); setSel(saved.id); setF(saved); setErr(""); loadSets();
    } catch (e) { setErr((e as Error).message); }
  };
  const del = async () => {
    if (!f || !confirm(`Delete “${f.name}” and its past runs?`)) return;
    try { await api(`/api/evals/sets/${f.id}`, { method: "DELETE" }); setF(null); setSel(""); setRun(null); loadSets(); } catch (e) { setErr((e as Error).message); }
  };
  const start = async () => {
    try {
      const r = await api<Run>("/api/evals/runs", { method: "POST", body: JSON.stringify({ set_id: sel, agent: rf.agent, provider: rf.provider, model: rf.model || null, judge: rf.judge ? { provider: rf.judge } : null }) });
      setRun(r); setErr("");
    } catch (e) { setErr((e as Error).message); }
  };
  const setCase = (i: number, c: Case) => setF((s) => s && { ...s, cases: s.cases.map((x, n) => (n === i ? c : x)) });
  const view = async (id: string) => { try { setRun(await api<Run>(`/api/evals/runs/${id}`)); } catch (e) { setErr((e as Error).message); } };

  return (
    <div>
      <div className="page-head"><h2>Evaluations</h2><InfoTip id="evals" /></div>
      <p className="page-sub">A test set is a list of prompts with simple pass/fail checks. Run it against an agent before and after you change its instructions or model. Every prompt uses model credits, and the agent&apos;s guardrails and spend limits apply.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="ag-layout">
        <div className="ag-side"><div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}><b>Test sets</b><button onClick={() => { setIsNew(true); setF(blankSet()); setSel(""); setRun(null); }}>+ New</button></div>
          {sets.length === 0 && <p className="small mut">None yet.</p>}
          {sets.map((s) => (
            <div key={s.id} role="button" tabIndex={0} aria-pressed={sel === s.id} className={`ag-item ${sel === s.id ? "on" : ""}`} onClick={() => open(s.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(s.id); } }}>
              {s.name} <span className="mut small">· {s.case_count} cases</span>
              {s.last_run && <span className={`pill ${s.last_run.status === "done" && (s.last_run.pass_rate ?? 0) >= 80 ? "ok" : "warn"}`} style={{ marginLeft: 6 }}>{s.last_run.pass_rate ?? 0}%</span>}
            </div>))}
        </div><EvalStarters onAdded={(id) => { loadSets(); open(id); }} /></div>
        {!f && <div className="ag-main"><div className="empty" data-testid="evals-empty">Pick a test set on the left to see its cases and run it, or press <b>+ New</b> to write your own.</div></div>}
        {f && (
          <div className="ag-main">
            <div className="card">
              <div className="grid">
                <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
                <label>Id {isNew ? "(optional)" : ""}<input value={f.id} disabled={!isNew} placeholder="made from the name" onChange={(e) => setF({ ...f, id: e.target.value })} /></label>
              </div>
              <label style={{ marginTop: 8 }}>Description<input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
              {f.cases.map((c, i) => (
                <div key={i} className="card" data-testid="case">
                  <div className="row"><b>Case {i + 1}</b><span className="grow" /><button className="ghost small" onClick={() => setF({ ...f, cases: f.cases.filter((_, n) => n !== i) })}>Remove case</button></div>
                  <label>Prompt<textarea rows={2} value={c.prompt} onChange={(e) => setCase(i, { ...c, prompt: e.target.value })} /></label>
                  {c.expect.map((a, j) => (
                    <div key={j} className="row" style={{ marginTop: 4 }}>
                      <select aria-label="Check" value={a.type} onChange={(e) => setCase(i, { ...c, expect: c.expect.map((x, n) => (n === j ? { type: e.target.value } : x)) })}>{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                      {a.type !== "json_valid" && <input aria-label="Value" style={{ flex: 1 }} value={a.value ?? ""} onChange={(e) => setCase(i, { ...c, expect: c.expect.map((x, n) => (n === j ? { ...x, value: NUMERIC.includes(x.type) ? Number(e.target.value) : e.target.value } : x)) })} />}
                      <button className="ghost small" aria-label="Remove check" onClick={() => setCase(i, { ...c, expect: c.expect.filter((_, n) => n !== j) })}>✕</button>
                    </div>))}
                  <button className="small" style={{ marginTop: 6 }} onClick={() => setCase(i, { ...c, expect: [...c.expect, { type: "contains", value: "" }] })}>Add a check</button>
                </div>))}
              <div className="row" style={{ marginTop: 8 }}>
                <button onClick={() => setF({ ...f, cases: [...f.cases, { id: `c${f.cases.length + 1}`, prompt: "", expect: [] }] })}>Add a case</button>
                <button className="pri" onClick={save}>Save</button>
                {!isNew && <button onClick={del}>Delete</button>}
              </div>
            </div>

            {!isNew && sel && (
              <div className="card" data-testid="run-panel">
                <div className="card-head"><b>Run it</b></div>
                <div className="grid">
                  <label>Agent<select value={rf.agent} onChange={(e) => setRf({ ...rf, agent: e.target.value })}><option value="">Choose…</option>{agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                  <label>Model provider<select value={rf.provider} onChange={(e) => setRf({ ...rf, provider: e.target.value })}><option value="">Choose…</option>{provs.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
                  <label>Model (optional)<input value={rf.model} onChange={(e) => setRf({ ...rf, model: e.target.value })} placeholder="the agent's usual one" /></label>
                  <label>Judge model for “a judge agrees” checks<select value={rf.judge} onChange={(e) => setRf({ ...rf, judge: e.target.value })}><option value="">No judge</option>{provs.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="pri" disabled={!rf.agent || !rf.provider || running} onClick={start}>Start run</button>
                  {running && <button onClick={async () => { try { setRun(await api<Run>(`/api/evals/runs/${run!.id}/cancel`, { method: "POST" })); } catch (e) { setErr((e as Error).message); } }}>Stop</button>}
                  <span className="grow" />
                  {(["promptfoo", "inspect", "jsonl"] as const).map((k) => <a key={k} className="small" href={`${API_BASE}/api/evals/export?kind=set&id=${sel}&format=${k}`} download>Export for {k}</a>)}
                </div>
              </div>)}

            {run && (
              <div className="card" data-testid="run-result">
                <div className="card-head"><b>Result</b><span className={`pill ${run.status === "done" ? "ok" : run.status === "running" ? "acc" : "bad"}`}>{run.status}</span>
                  <span className="small mut">{run.done ?? 0}/{run.total ?? 0} cases · {run.pass_rate ?? 0}% passed · {run.passed ?? 0} passed, {run.failed ?? 0} failed, {run.errors ?? 0} errors · ${(run.cost_usd ?? 0).toFixed(4)}</span></div>
                {run.stopped && <p className="small err">Stopped: {run.stopped}</p>}
                {(run.results ?? []).map((r) => (
                  <div key={r.id} style={{ padding: "6px 0", borderTop: "1px solid var(--line)" }}>
                    <div className="row"><span className={`pill ${r.error ? "bad" : r.ok ? "ok" : "warn"}`}><span aria-hidden="true">{r.error ? "✕" : r.ok ? "✓" : "!"}</span> {r.error ? "error" : r.ok ? "passed" : "failed"}</span><b>{r.id}</b><span className="small mut">{r.ms} ms · ${r.cost_usd.toFixed(4)}{r.blocked ? " · stopped by a guardrail" : ""}</span></div>
                    <div className="small">{r.prompt}</div>
                    <div className="small mut" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{r.error ?? r.reply}</div>
                    {r.checks.map((c, i) => <div key={i} className="small"><span aria-hidden="true">{c.ok ? "✓" : "✕"}</span> {c.type}: {c.detail}</div>)}
                  </div>))}
                {run.status !== "running" && <div className="row" style={{ marginTop: 6 }}>
                  {(["csv", "deepeval", "ragas", "jsonl"] as const).map((k) => <a key={k} className="small" href={`${API_BASE}/api/evals/export?kind=run&id=${run.id}&format=${k}`} download>Export for {k}</a>)}</div>}
              </div>)}

            {!isNew && runs.length > 0 && (
              <div className="card"><div className="card-head"><b>Past runs</b></div>
                {runs.map((r) => <div key={r.id} className="row small" style={{ padding: "3px 0" }}><span className="mut">{new Date(r.started * 1000).toLocaleString()}</span> {r.agent} · {r.pass_rate ?? 0}% · {r.status}<span className="grow" /><button className="small" onClick={() => view(r.id)}>Open</button></div>)}
              </div>)}
          </div>)}
      </div>
    </div>
  );
}
