"use client";
// The pieces around the canvas of the workflow editor: the box palette, the right-hand settings panel, the builder chat and the bottom drawer.
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { ago, AUTONOMY, ENTRY, ROOTS, type Catalog, type Doc, type FormField, type NodeSpec, type Problem, type Run, type Settings, type WNode } from "@/lib/workflows";
import Fields, { type Ctx } from "@/components/workflows/Fields";
import TryBox from "@/components/workflows/TryBox";
import WebhookBox from "@/components/workflows/WebhookBox";
import ScheduleBox from "@/components/workflows/ScheduleBox";
import { Modal } from "@/components/Modal";
import InfoTip from "@/components/InfoTip";

// ------------------------------------------------------------------ palette
export function Palette({ catalog, ops, onAdd, onClose }: { catalog: Catalog; ops: Ctx["srcs"]; onAdd: (type: string, preset?: Record<string, unknown>, label?: string) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const ql = q.trim().toLowerCase();
  const groups = useMemo(() => catalog.groups.map((g) => ({ ...g, items: catalog.nodes.filter((n) => n.group === g.id && !ENTRY.includes(n.type) && (!ql || `${n.label} ${n.desc} ${n.type}`.toLowerCase().includes(ql))) })).filter((g) => g.items.length), [catalog, ql]);
  const apiItems = ops.flatMap((s) => s.operations.filter((o) => o.enabled).map((o) => ({ s, o }))).filter(({ s, o }) => !ql || `${s.name} ${o.id} ${o.path}`.toLowerCase().includes(ql)).slice(0, 80);
  return (
    <aside className="wf-palette" role="dialog" aria-label="Add a box" data-testid="palette">
      <div className="row"><b>Add a box</b><span className="grow" /><button className="icon-btn" aria-label="Close palette" onClick={onClose}>✕</button></div>
      <input autoFocus value={q} placeholder="Search boxes (type / to open)" aria-label="Search boxes" onChange={(e) => setQ(e.target.value)} style={{ margin: "8px 0" }} />
      <div className="wf-palette-list">
        {groups.map((g) => (<div key={g.id}><div className="wf-group">{g.label}</div>
          {g.items.map((n) => (<button key={n.type} className="wf-item-btn" draggable onDragStart={(e) => e.dataTransfer.setData("application/x-wf-node", n.type)} onClick={() => onAdd(n.type)}><b>{n.label}</b><span className="small mut">{n.desc}</span></button>))}</div>))}
        {apiItems.length > 0 && (<div><div className="wf-group">Your API endpoints</div>
          {apiItems.map(({ s, o }) => (<button key={`${s.id}.${o.id}`} className="wf-item-btn" onClick={() => onAdd("openapi", { source: s.id, operation: o.id, args: {}, confirmed: false }, o.id)}><b><span className={`pill ${o.method === "get" ? "ok" : "warn"}`}>{o.method.toUpperCase()}</span> {s.name}</b><span className="small mut">{o.path}</span></button>))}</div>)}
        {!groups.length && !apiItems.length && <div className="small mut">No box matches &quot;{q}&quot;.</div>}
      </div>
    </aside>
  );
}

// ------------------------------------------------------------------ settings panel
export function DataView({ title, text, onPin }: { title: string; text: string | undefined; onPin?: () => void }) {
  const [mode, setMode] = useState<"text" | "json" | "table">("text");
  if (!text) return <div className="small mut" data-testid={`${title}-empty`}>Nothing yet. Run the workflow to see the {title.toLowerCase()} of this box.</div>;
  let parsed: unknown = null;
  try { parsed = JSON.parse(text); } catch { parsed = null; }
  const rows = Array.isArray(parsed) && parsed.every((x) => x && typeof x === "object" && !Array.isArray(x)) ? (parsed as Record<string, unknown>[]) : null;
  const cols = rows ? Array.from(new Set(rows.flatMap((r) => Object.keys(r)))).slice(0, 8) : [];
  const shown = mode === "json" && parsed !== null ? JSON.stringify(parsed, null, 2) : text;
  return (
    <div data-testid={`${title}-data`}>
      <div className="row"><div className="seg" role="tablist">{(["text", "json", ...(rows ? ["table"] : [])] as const).map((m) => <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => setMode(m as "text")}>{m}</button>)}</div>
        <span className="grow" />{onPin && <button className="small" onClick={onPin}>Pin this output</button>}<button className="small" onClick={() => navigator.clipboard?.writeText(text)}>Copy</button></div>
      {mode === "table" && rows ? <div style={{ overflow: "auto", maxHeight: 280 }}><table className="tbl"><thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead><tbody>{rows.slice(0, 50).map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c] ?? "")}</td>)}</tr>)}</tbody></table></div>
        : <pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>{shown.slice(0, 6000)}</pre>}
    </div>
  );
}

export function NodePanel({ node, spec, ctx, run, doc, saved, runFromWhy, onRunFrom, onSwitchEntry, onChange, onDelete, onDuplicate, onClose }: {
  node: WNode; spec: NodeSpec | undefined; ctx: Ctx; run: Run | null; doc: Doc; saved: boolean; runFromWhy: string; onRunFrom: () => void; onSwitchEntry: (type: string) => void;
  onChange: (n: WNode) => void; onDelete: () => void; onDuplicate: () => void; onClose: () => void;
}) {
  const [tab, setTab] = useState<"settings" | "input" | "output" | "notes">("settings");
  const st = node.settings ?? {};
  const setSt = (p: Partial<Settings>) => onChange({ ...node, settings: { ...st, ...p } });
  const rn = run?.nodes[node.id];
  return (
    <aside className="wf-panel" role="region" aria-label={`Settings of ${node.label}`} data-testid="node-panel">
      <div className="row"><span className="pill acc">{spec?.label ?? node.type}</span><span className="grow" />
        <button className="icon-btn" aria-label="Close settings" onClick={onClose}>✕</button></div>
      <label style={{ marginTop: 8 }}>Name<input value={node.label} aria-label="Box name" onChange={(e) => onChange({ ...node, label: e.target.value })} /></label>
      <div className="seg" role="tablist" style={{ margin: "10px 0" }}>{(["settings", "input", "output", "notes"] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
      {tab === "settings" && (<>
        {ENTRY.includes(node.type) && <label>This workflow starts when<select value={node.type} aria-label="Starts when" onChange={(e) => onSwitchEntry(e.target.value)}><option value="input">a task is typed, sent over the API or run by another workflow</option><option value="webhook">something calls its secret address (Webhook)</option><option value="schedule">a schedule says so (Schedule)</option></select></label>}
        {spec && <Fields spec={spec} node={node} ctx={ctx} onChange={(config) => onChange({ ...node, config })} />}
        {node.type === "webhook" && <WebhookBox workflowId={doc.id} saved={saved} />}
        {node.type === "schedule" && <ScheduleBox workflowId={doc.id} saved={saved} />}
        {!ROOTS.includes(node.type) && node.type !== "note" && <TryBox key={node.id} doc={doc} node={node} lastInput={rn?.input} canRunFrom={!runFromWhy} runFromWhy={runFromWhy} onRunFrom={onRunFrom} />}
        {!ROOTS.includes(node.type) && node.type !== "note" && (
          <details style={{ marginTop: 12 }} data-testid="common-settings"><summary><b className="small">When something goes wrong</b> <InfoTip id="wf-common-settings" /></summary>
            <div className="stack" style={{ marginTop: 6 }}>
              <label>If this box fails<select value={st.on_error ?? "stop"} aria-label="On error" onChange={(e) => setSt({ on_error: e.target.value })}><option value="stop">Stop the run</option><option value="continue">Carry on with nothing</option><option value="branch">Follow its error output</option></select></label>
              <label>Try again up to<input type="number" min={0} max={3} value={st.retries ?? 0} aria-label="Retries" onChange={(e) => setSt({ retries: Number(e.target.value) })} /></label>
              <label>Give up after (seconds, 0 = never)<input type="number" min={0} max={600} value={st.timeout_s ?? 0} aria-label="Time limit" onChange={(e) => setSt({ timeout_s: Number(e.target.value) })} /></label>
              <label className="check"><input type="checkbox" checked={!!st.disabled} onChange={(e) => setSt({ disabled: e.target.checked })} /> Switch this box off (it passes its input through)</label>
              {st.pin != null && <div className="small">Pinned output is used instead of running this box. <button className="small" onClick={() => setSt({ pin: null })}>Unpin</button></div>}
            </div></details>)}
        <div className="row" style={{ marginTop: 12 }}>{!ENTRY.includes(node.type) && <><button onClick={onDuplicate}>Duplicate</button><button onClick={onDelete}>Delete</button></>}</div>
      </>)}
      {tab === "input" && <DataView title="Input" text={rn?.input} />}
      {tab === "output" && (<>
        {rn?.error && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{rn.error}</div>}
        {rn?.status && <div className="small mut">{rn.status.replace("_", " ")}{rn.attempts && rn.attempts > 1 ? ` after ${rn.attempts} tries` : ""}{rn.decision ? ` · went ${rn.decision}` : ""}</div>}
        <DataView title="Output" text={rn?.output} onPin={rn?.output ? () => setSt({ pin: rn.output }) : undefined} /></>)}
      {tab === "notes" && <label>Notes for you or your team<textarea rows={6} value={st.notes ?? ""} aria-label="Notes" onChange={(e) => setSt({ notes: e.target.value })} /></label>}
    </aside>
  );
}

// ------------------------------------------------------------------ builder chat
type Msg = { role: "user" | "assistant"; content: string; diff?: { added: string[]; removed: string[]; changed: string[]; edges_added: number; edges_removed: number }; workflow?: Doc; applied?: string[]; refused?: string[]; done?: boolean };

export function BuilderChat({ doc, ctx, autonomy, onApply, onClose, initial }: { doc: Doc; ctx: Ctx; autonomy: string; onApply: (d: Doc) => void; onClose: () => void; initial?: string }) {
  const [msgs, setMsgs] = useState<Msg[]>(initial ? [{ role: "assistant", content: initial }] : []);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(autonomy);
  const [agents, setAgents] = useState<string[]>(ctx.agents.map((a) => a.id));
  const [err, setErr] = useState("");
  const send = async () => {
    if (!text.trim() || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content: text.trim() }];
    setMsgs(next); setText(""); setBusy(true); setErr("");
    try {
      const r = await api<{ reply: string; diff: Msg["diff"]; workflow: Doc; applied: string[]; refused: string[]; note: string | null }>("/api/workflows/builder/chat",
        { method: "POST", body: JSON.stringify({ messages: next.map(({ role, content }) => ({ role, content })), doc, autonomy: auto, systems: { agents } }) });
      setMsgs([...next, { role: "assistant", content: r.reply + (r.note ? ` (${r.note})` : ""), diff: r.diff, workflow: r.workflow, applied: r.applied, refused: r.refused }]);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const changes = (d: NonNullable<Msg["diff"]>) => d.added.length + d.removed.length + d.changed.length + d.edges_added + d.edges_removed;
  return (
    <aside className="wf-chat" role="region" aria-label="Workflow builder chat" data-testid="builder-chat">
      <div className="row"><b>Build with an agent</b><InfoTip id="wf-builder" /><span className="grow" /><button className="icon-btn" aria-label="Close chat" onClick={onClose}>✕</button></div>
      <div className="small mut" style={{ margin: "6px 0" }}>Describe what you want. The agent proposes changes; nothing happens until you press Apply, and it can never confirm or run anything for you.</div>
      <details><summary className="small">How it should work · systems it may use</summary>
        <label>How much should the workflow ask you?<select value={auto} aria-label="Autonomy" onChange={(e) => setAuto(e.target.value)}>{AUTONOMY.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        <div className="small" style={{ marginTop: 6 }}>{ctx.agents.map((a) => <label key={a.id} className="check" style={{ display: "block" }}><input type="checkbox" checked={agents.includes(a.id)} onChange={() => setAgents(agents.includes(a.id) ? agents.filter((x) => x !== a.id) : [...agents, a.id])} /> {a.name}</label>)}</div></details>
      <div className="wf-chat-log">
        {msgs.map((m, i) => (<div key={i} className={`wf-msg ${m.role}`}><div>{m.content}</div>
          {m.diff && (<div className="card small" data-testid="diff-card" style={{ marginTop: 6 }}>
            {changes(m.diff) === 0 ? "No changes proposed." : <>Proposed: {m.diff.added.length > 0 && `+${m.diff.added.length} boxes `}{m.diff.changed.length > 0 && `~${m.diff.changed.length} changed `}{m.diff.removed.length > 0 && `−${m.diff.removed.length} removed `}{(m.diff.edges_added + m.diff.edges_removed) > 0 && `· ${m.diff.edges_added} new / ${m.diff.edges_removed} removed connections`}
              <ul style={{ margin: "4px 0", paddingLeft: 16 }}>{(m.applied ?? []).map((a, j) => <li key={j}>{a}</li>)}</ul></>}
            {(m.refused ?? []).length > 0 && <div style={{ color: "var(--warn-text)" }}>Left out: {(m.refused ?? []).join("; ")}</div>}
            {changes(m.diff) > 0 && <div className="row" style={{ marginTop: 6 }}><button className="pri small" disabled={m.done} onClick={() => { onApply(m.workflow!); setMsgs(msgs.map((x, j) => (j === i ? { ...x, done: true } : x))); }}>{m.done ? "Applied" : "Apply"}</button>
              {!m.done && <button className="small" onClick={() => setMsgs(msgs.map((x, j) => (j === i ? { ...x, diff: undefined } : x)))}>Dismiss</button>}</div>}
          </div>)}</div>))}
        {busy && <div className="small mut">Thinking…</div>}
        {err && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{err}</div>}
      </div>
      <div className="row"><textarea rows={2} className="grow" value={text} aria-label="Tell the builder" placeholder="e.g. When a customer writes in, classify it and draft a reply for me to approve" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="pri" disabled={busy || !text.trim()} onClick={send}>Send</button></div>
    </aside>
  );
}

// ------------------------------------------------------------------ bottom drawer
type Saved = { id: string; status: string; task: string; started: number; cost_usd: number; version: number };
export type DrawerTab = "run" | "problems" | "runs";
export function Details({ form, onAnswer }: { form: FormField[]; onAnswer: (values: Record<string, unknown>) => void }) {
  const [v, setV] = useState<Record<string, unknown>>({});
  return (
    <span data-testid="approval-form" style={{ display: "block", margin: "4px 0" }}>
      {form.map((f) => (
        <label key={f.name} className={f.type === "bool" ? "check" : undefined} style={{ display: "block" }}>{f.label || f.name}{f.required ? " *" : ""}
          {f.type === "select" ? <select value={String(v[f.name] ?? "")} aria-label={f.label || f.name} onChange={(e) => setV({ ...v, [f.name]: e.target.value })}><option value="">choose…</option>{f.options.map((o) => <option key={o}>{o}</option>)}</select>
            : f.type === "bool" ? <input type="checkbox" checked={!!v[f.name]} aria-label={f.label || f.name} onChange={(e) => setV({ ...v, [f.name]: e.target.checked })} />
            : <input type={f.type === "number" ? "number" : "text"} value={String(v[f.name] ?? "")} aria-label={f.label || f.name} onChange={(e) => setV({ ...v, [f.name]: e.target.value })} />}</label>))}
      <button className="pri small" onClick={() => onAnswer(v)}>Send answers and continue</button>
    </span>
  );
}

export function Drawer({ run, problems, workflowId, tab, onTab, onDecide, onStop, onOpenRun, onSelect, onClose }: {
  run: Run | null; problems: Problem[]; workflowId: string; tab: DrawerTab; onTab: (t: DrawerTab) => void; onDecide: (node: string, d: string, values?: Record<string, unknown>) => void; onStop: () => void; onOpenRun: (id: string) => void; onSelect: (id: string) => void; onClose: () => void;
}) {
  const [saved, setSaved] = useState<Saved[] | null>(null);
  const setTab = onTab;
  const open = async (t: DrawerTab) => { setTab(t); if (t === "runs") setSaved((await api<{ runs: Saved[] }>(`/api/workflows/${workflowId}/runs`).catch(() => ({ runs: [] }))).runs); };
  return (
    <section className="wf-drawer" aria-label="Run details" data-testid="drawer">
      <div className="row"><div className="seg" role="tablist">{([["run", "Run log"], ["problems", `Problems${problems.length ? ` (${problems.length})` : ""}`], ["runs", "Earlier runs"]] as const).map(([k, t]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => open(k)}>{t}</button>)}</div>
        <span className="grow" />{run?.status === "running" && <button className="small" onClick={onStop}>Stop</button>}<button className="icon-btn" aria-label="Close drawer" onClick={onClose}>✕</button></div>
      {tab === "run" && (!run ? <div className="small mut" style={{ marginTop: 8 }}>No run yet. Type a task and press Run.</div> : (
        <div style={{ marginTop: 8 }} data-testid="run-log">
          <div className="small"><span className={`pill ${run.status === "done" ? "ok" : run.status === "error" ? "bad" : "acc"}`}>{run.status === "plan" ? "plan only" : run.status}</span>{run.cost_usd > 0 && <span className="mut"> ${run.cost_usd.toFixed(4)}</span>}</div>
          {run.error && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{run.error}</div>}
          {run.status === "plan" && <ol className="small" data-testid="plan">{run.plan.map((p) => <li key={p.node}><b>{p.label}</b>: {p.does}</li>)}</ol>}
          {run.status !== "plan" && Object.entries(run.nodes).map(([id, n]) => (<div key={id} className="small" data-testid="run-node" style={{ margin: "4px 0" }}>
            <button className="link" onClick={() => onSelect(id)}><b>{n.label}</b></button> <span className="mut">{n.status.replace("_", " ")}{n.decision ? ` → ${n.decision}` : ""}{n.reused ? " · reused from the earlier run" : ""}</span>
            {n.status === "waiting_approval" && <span style={{ marginLeft: 8 }}>{n.message && <i>{n.message} </i>}
              {n.form && n.form.length > 0 ? <Details form={n.form} onAnswer={(values) => onDecide(id, "approve", values)} /> : <button className="pri small" onClick={() => onDecide(id, "approve")}>{n.type === "approval" ? "Yes" : "Approve"}</button>}
              {" "}<button className="small" onClick={() => onDecide(id, "skip")}>{n.type === "approval" ? "No" : "Skip"}</button> <button className="small" onClick={() => onDecide(id, "stop")}>Stop</button></span>}
            {n.error && <div style={{ color: "var(--bad-text)" }}>{n.error}</div>}</div>))}
          {run.final && <div style={{ marginTop: 6 }}><b className="small">Result</b><pre className="small" style={{ whiteSpace: "pre-wrap" }} data-testid="run-final">{run.final}</pre></div>}
        </div>))}
      {tab === "problems" && (<div style={{ marginTop: 8 }} data-testid="problems">{problems.length === 0 ? <div className="small mut">No problems found.</div> : <ul className="small" style={{ paddingLeft: 18 }}>{problems.map((p, i) => <li key={i} style={{ color: p.severity === "error" ? "var(--bad)" : "var(--warn)" }}>{p.node ? <button className="link" onClick={() => onSelect(p.node)}>{p.node}</button> : null}{p.node ? ": " : ""}{p.message}</li>)}</ul>}</div>)}
      {tab === "runs" && (<div style={{ marginTop: 8 }} data-testid="earlier-runs">{!saved ? <div className="small mut">Loading…</div> : saved.length === 0 ? <div className="small mut">No earlier runs.</div> : saved.map((r) => (
        <div key={r.id} className="row small"><span className={`pill ${r.status === "done" ? "ok" : r.status === "error" ? "bad" : ""}`}>{r.status}</span><span className="grow">{r.task || "(no task)"}</span><span className="mut">v{r.version} · {ago(r.started)}</span><button className="small" onClick={() => { onOpenRun(r.id); setTab("run"); }}>Open</button></div>))}</div>)}
    </section>
  );
}

// ------------------------------------------------------------------ versions
type Ver = { version: number; ts: number; nodes: number; label: string; note: string };
export function Versions({ workflowId, onRestore, onClose }: { workflowId: string; onRestore: (v: number) => void; onClose: () => void }) {
  const [vs, setVs] = useState<Ver[] | null>(null);
  const [look, setLook] = useState<{ v: number; doc: Doc } | null>(null);
  useEffect(() => { api<{ versions: Ver[] }>(`/api/workflows/${workflowId}/versions`).then((r) => setVs(r.versions)).catch(() => setVs([])); }, [workflowId]);
  return (
    <Modal title="Versions" onClose={onClose}>
      <div data-testid="versions" style={{ marginTop: 8 }}>{!vs ? "Loading…" : vs.map((v) => (
        <div key={v.version} className="row small" style={{ margin: "4px 0" }}><b>v{v.version}</b><span className="grow">{v.label || "(no label)"}{v.note ? ` — ${v.note}` : ""} <span className="mut">· {v.nodes} nodes · {ago(v.ts)}</span></span>
          <button className="small" onClick={async () => setLook({ v: v.version, doc: await api<Doc>(`/api/workflows/${workflowId}/versions/${v.version}`) })}>Look</button><button className="small" onClick={() => onRestore(v.version)}>Restore</button></div>))}
        {look && <div className="card small" data-testid="version-look">v{look.v}: {look.doc.nodes.map((n) => n.label).join(" → ")}</div>}</div>
    </Modal>
  );
}
