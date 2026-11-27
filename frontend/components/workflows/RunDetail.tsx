"use client";
// One run, in full: who started it and what it was given, the workflow as it was drawn when it ran with each box's status, every box's input, output, error and timing,
// the run log, and what you can do with it (repeat it, run again from the box that failed, stop it, approve a step, export it, delete it). Secrets never arrive here: the server removes them.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete } from "@/components/Modal";
import FlowCanvas from "@/components/workflows/FlowCanvas";
import { DataView, Details } from "@/components/workflows/EditorParts";
import { ago, fmtDuration, ORIGIN_LABEL, ROOTS, RUN_STATUS, type Catalog, type Doc, type NodeSpec, type Run, type RunNode } from "@/lib/workflows";

const NODE_TEXT: Record<string, string> = { pending: "waiting", waiting_approval: "needs your OK", running: "running", done: "done", error: "failed", skipped: "skipped", stopped: "stopped" };
const NODE_ICON: Record<string, string> = { done: "✓", error: "✕", running: "◔", waiting_approval: "?", skipped: "–", stopped: "■", pending: "·" };
const POLL_MS = 2500;
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString();

export default function RunDetail({ id, onClose, onChanged, onOpen }: { id: string; onClose: () => void; onChanged: () => void; onOpen: (id: string) => void }) {
  const router = useRouter();
  const [run, setRun] = useState<Run | null>(null), [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<string | null>(null), [ask, setAsk] = useState(false);
  const [graph, setGraph] = useState<Doc | null>(null), [specs, setSpecs] = useState<Record<string, NodeSpec>>({}), [current, setCurrent] = useState<number | null>(null);
  const load = useCallback(() => api<Run>(`/api/workflows/runs/${id}`).then((r) => { setRun(r); setErr(""); }).catch((e) => setErr((e as Error).message)), [id]);
  useEffect(() => { setRun(null); setSel(null); setGraph(null); load(); }, [load]);
  const running = run?.status === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [running, load]);
  const wid = run?.workflow, ver = run?.version;
  useEffect(() => {                                                       // the workflow as it was when this run started, and as it is now
    if (!wid || ver == null) return;
    let live = true;
    api<Doc>(`/api/workflows/${wid}/versions/${ver}`).then((d) => live && setGraph(d)).catch(() => live && setGraph(null));
    api<Doc>(`/api/workflows/${wid}`).then((d) => live && setCurrent(d.version ?? null)).catch(() => live && setCurrent(null));
    return () => { live = false; };
  }, [wid, ver]);
  useEffect(() => { api<Catalog>("/api/workflows/nodes").then((c) => setSpecs(Object.fromEntries(c.nodes.map((n) => [n.type, n])))).catch(() => setSpecs({})); }, []);

  const act = async (fn: () => Promise<unknown>, msg: string) => { setBusy(true); try { await fn(); if (msg) toast(msg); setErr(""); } catch (e) { setErr((e as Error).message); } setBusy(false); };
  const again = (from?: string) => act(async () => { const r = await api<Run>(`/api/workflows/runs/${id}/rerun`, { method: "POST", body: JSON.stringify(from ? { from_node: from } : {}) }); onChanged(); onOpen(r.id); }, from ? "Started again from that box" : "Started again");
  const stop = () => act(async () => { await api(`/api/workflows/runs/${id}/stop`, { method: "POST" }); await load(); onChanged(); }, "Stopping");
  const decide = (node: string, decision: string, values?: Record<string, unknown>) => act(async () => { await api(`/api/workflows/runs/${id}/decide`, { method: "POST", body: JSON.stringify({ node, decision, values }) }); await load(); onChanged(); }, "");
  const remove = () => act(async () => { await api(`/api/workflows/runs/${id}`, { method: "DELETE" }); onChanged(); onClose(); }, "Run deleted");
  const exportRun = () => {
    if (!run) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = `agent-studio-run-${run.id}.json`; a.click();
    URL.revokeObjectURL(url);
  };

  const nodes = useMemo(() => Object.entries(run?.nodes ?? {}).sort(([, a], [, b]) => (a.started ?? a.ended ?? Infinity) - (b.started ?? b.ended ?? Infinity)), [run]);
  const log = useMemo(() => nodes.flatMap(([, n]) => [...(n.started ? [{ t: n.started, text: `${n.label} started` }] : []), ...(n.ended ? [{ t: n.ended, text: `${n.label} ${NODE_TEXT[n.status] ?? n.status}${n.error ? `: ${n.error}` : ""}` }] : [])]).sort((a, b) => a.t - b.t), [nodes]);
  const failed = nodes.find(([, n]) => n.status === "error");
  const waiting = nodes.filter(([, n]) => n.status === "waiting_approval");
  const statuses = useMemo(() => Object.fromEntries(Object.entries(run?.nodes ?? {}).map(([k, n]) => [k, n.status])), [run]);
  const picked: [string, RunNode] | undefined = sel ? nodes.find(([k]) => k === sel) : undefined;
  const st = run ? RUN_STATUS[run.status] ?? { label: run.status, icon: "", tone: "" } : null;
  const took = run ? (run.ended ?? Date.now() / 1000) - (run.started ?? 0) : 0;

  return (
    <aside className="rd" aria-label="Run details" data-testid="run-detail">
      <div className="row rd-head">
        <button className="rd-back" onClick={onClose} aria-label="Back to runs">← Runs</button>
        <b className="grow">Run {id}</b>
        <button className="icon-btn rd-close" aria-label="Close run details" onClick={onClose}>✕</button>
      </div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", margin: "6px 0" }}>{err}</div>}
      {!run && !err && <div className="skeleton" style={{ height: 120, marginTop: 8 }} aria-label="Loading the run" />}
      {run && st && (<>
        <div style={{ margin: "8px 0" }}>
          <button className="link" onClick={() => router.push(`/workflows/edit/?id=${run.workflow}`)}><b>{run.name}</b></button>{" "}
          <span className={`pill ${st.tone}`}><span aria-hidden="true">{st.icon}</span> {st.label}</span>{run.needs_you && <span className="pill warn" style={{ marginLeft: 6 }}>needs you</span>}
          <div className="small mut" style={{ marginTop: 4 }}>
            {ORIGIN_LABEL[run.origin ?? "manual"] ?? run.origin} · started {ago(run.started)} · {running ? "running for" : "took"} {fmtDuration(took)}
            {run.cost_usd > 0 ? ` · $${run.cost_usd.toFixed(4)}` : ""}{run.tokens ? ` · ${run.tokens.toLocaleString()} tokens` : ""} · {run.autonomy === "auto" ? "ran by itself" : run.autonomy === "approve" ? "asked before each step" : "plan only"}
          </div>
          <div className="small mut" data-testid="run-version">
            Ran on version v{run.version}{current != null && current !== run.version ? <> (the workflow is now v{current}; <b>Run again</b> uses v{current})</> : null}.{" "}
            <button className="link" onClick={() => router.push(`/workflows/edit/?id=${run.workflow}&versions=1`)}>See the versions in the editor</button>
          </div>
        </div>
        {run.error && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginBottom: 6 }}>{run.error}</div>}
        <div className="row" style={{ flexWrap: "wrap", gap: 6, margin: "8px 0" }}>
          {running ? <button className="small" disabled={busy} onClick={stop}>Stop</button> : <button className="small pri" disabled={busy} onClick={() => again()}>Run again</button>}
          {!running && failed && <button className="small" disabled={busy} onClick={() => again("failed")}>Run again from “{failed[1].label}”</button>}
          <button className="small" onClick={exportRun}>Export JSON</button>
          <button className="small" onClick={() => router.push(`/workflows/edit/?id=${run.workflow}`)}>Open in editor</button>
          {!running && <button className="small" disabled={busy} onClick={() => setAsk(true)}>Delete…</button>}
          <InfoTip id="wf-runs" />
        </div>

        {waiting.length > 0 && (
          <section className="card" data-testid="run-needs-you" style={{ margin: "8px 0" }}>
            <b className="small">Waiting for you</b>
            {waiting.map(([k, n]) => (
              <div key={k} className="small" style={{ margin: "6px 0" }}><b>{n.label}</b>{n.message && <i> {n.message}</i>}
                <div>{n.form && n.form.length > 0 ? <Details form={n.form} onAnswer={(values) => decide(k, "approve", values)} /> : <button className="pri small" onClick={() => decide(k, "approve")}>{n.type === "approval" ? "Yes" : "Approve"}</button>}{" "}
                  <button className="small" onClick={() => decide(k, "skip")}>{n.type === "approval" ? "No" : "Skip"}</button> <button className="small" onClick={() => decide(k, "stop")}>Stop the run</button></div></div>))}
          </section>)}

        <section style={{ margin: "10px 0" }}>
          <b className="small">{run.origin === "webhook" ? "What the webhook sent (secrets removed)" : "What it was given"}</b>
          <pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 140, overflow: "auto" }} data-testid="run-task">{run.task || "(no task)"}</pre>
        </section>

        {graph && run.status !== "plan" && (
          <section style={{ margin: "10px 0" }}>
            <b className="small">The workflow as it ran (v{run.version})</b>
            <div className="rd-graph" data-testid="run-graph"><FlowCanvas nodes={graph.nodes} edges={graph.edges} specs={specs} selected={sel} statuses={statuses} readOnly onSelect={setSel} onChange={() => {}} /></div>
          </section>)}

        {run.status === "plan" && run.plan.length > 0 && <ol className="small" data-testid="run-plan">{run.plan.map((p) => <li key={p.node}><b>{p.label}</b>: {p.does}</li>)}</ol>}

        {run.status !== "plan" && (
          <section style={{ margin: "10px 0" }}>
            <b className="small">Boxes</b>
            <div data-testid="run-boxes">{nodes.map(([k, n]) => (
              <div key={k} className="rd-node">
                <button className="link rd-node-btn" aria-expanded={sel === k} onClick={() => setSel(sel === k ? null : k)}>
                  <span className={`pill ${n.status === "done" ? "ok" : n.status === "error" ? "bad" : n.status === "running" ? "acc" : n.status === "waiting_approval" ? "warn" : ""}`}><span aria-hidden="true">{NODE_ICON[n.status] ?? ""}</span> {NODE_TEXT[n.status] ?? n.status}</span>{" "}
                  <b>{n.label}</b></button>
                <span className="small mut"> {n.started && n.ended ? fmtDuration(n.ended - n.started) : ""}{n.attempts && n.attempts > 1 ? ` · ${n.attempts} tries` : ""}{n.reused ? " · reused from the earlier run" : ""}{n.pinned ? " · pinned" : ""}</span>
                {n.error && <div className="small" style={{ color: "var(--bad-text)" }}>{n.error}</div>}
              </div>))}</div>
          </section>)}

        {picked && (
          <section className="card" data-testid="run-box" style={{ margin: "10px 0" }}>
            <div className="row"><b>{picked[1].label}</b><span className="small mut">{specs[picked[1].type]?.label ?? picked[1].type}</span><span className="grow" /><button className="icon-btn" aria-label="Close box details" onClick={() => setSel(null)}>✕</button></div>
            {picked[1].error && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{picked[1].error}</div>}
            <div className="small mut">{NODE_TEXT[picked[1].status] ?? picked[1].status}{picked[1].decision ? ` · went ${picked[1].decision}` : ""}{picked[1].started ? ` · started ${clock(picked[1].started)}` : ""}</div>
            <DataView title="Input" text={picked[1].input} />
            <DataView title="Output" text={picked[1].output} />
            {!running && !ROOTS.includes(picked[1].type) && ["done", "error"].includes(picked[1].status) && <button className="small" style={{ marginTop: 6 }} disabled={busy} onClick={() => again(picked[0])}>Run again from this box</button>}
          </section>)}

        {run.final && (<section style={{ margin: "10px 0" }}><b className="small">Result</b><pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto" }} data-testid="run-result">{run.final}</pre></section>)}

        {log.length > 0 && (
          <details style={{ margin: "10px 0" }}><summary><b className="small">Run log</b></summary>
            <ol className="small" data-testid="run-log-list" style={{ paddingLeft: 18 }}>{log.map((l, i) => <li key={i}><span className="mut">{clock(l.t)}</span> {l.text}</li>)}</ol></details>)}
      </>)}
      {ask && <ConfirmDelete what={`run ${id}`} onClose={() => setAsk(false)} onConfirm={() => { setAsk(false); remove(); }} />}
    </aside>
  );
}
