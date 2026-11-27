"use client";
// "Flows": every workflow in a table (search, filter, open, run, duplicate, rename, export, turn into an agent, delete) and the "New workflow" chooser:
// build it yourself, talk to an agent that builds it, or start from a template; plus import with the three ways to settle a name clash.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, API_BASE } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete, Modal } from "@/components/Modal";
import WorkflowSettings from "@/components/workflows/WorkflowSettings";
import { ago, blankDoc, slug, type Doc } from "@/lib/workflows";

type Row = { id: string; name: string; description: string; version: number; updated: number; nodes: number; autonomy: string; last_run: { status: string; started: number; cost_usd: number } | null; has_draft: boolean; uses: string[]; trigger?: string; has_hook?: boolean; schedule?: { enabled: boolean; next_run: number | null } | null };
type Past = { id: string; status: string; task: string; started: number; cost_usd: number; version: number };
type Tpl = { id: string; name: string; description: string; nodes: number; doc: Doc };
const AUTO_LABEL: Record<string, string> = { suggest: "plan only", approve: "asks first", auto: "automatic" };
const dot = (s: string) => (s === "done" ? "ok" : s === "error" ? "bad" : s === "running" ? "acc" : "warn");

function NewWorkflow({ onClose, onImport }: { onClose: () => void; onImport: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [tpls, setTpls] = useState<Tpl[]>([]);
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { api<{ templates: Tpl[] }>("/api/workflows/templates").then((r) => setTpls(r.templates)).catch(() => setTpls([])); }, []);
  const create = async (doc: Doc, chat: boolean) => {
    setBusy(true); setErr("");
    try {
      const base = slug(doc.name) || "workflow";
      let id = base, n = 2;
      const taken = new Set((await api<{ workflows: Row[] }>("/api/workflows")).workflows.map((w) => w.id));
      while (taken.has(id)) id = `${base}_${n++}`;
      await api("/api/workflows", { method: "POST", body: JSON.stringify({ ...doc, id }) });
      router.push(`/workflows/edit/?id=${id}${chat ? "&chat=1" : ""}`);
      setBusy(false);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  const title = name.trim() || "New workflow";
  return (
    <Modal title="New workflow" onClose={onClose} wide>
      <label style={{ marginTop: 8 }}>Name<input value={name} aria-label="Workflow name" placeholder="e.g. Customer reply" onChange={(e) => setName(e.target.value)} autoFocus /></label>
      <div className="grid" style={{ marginTop: 12 }}>
        <button className="card wf-choice" disabled={busy} onClick={() => create(blankDoc(title), false)}><b>Build it myself</b><span className="small mut">Start with a blank canvas and drag boxes onto it.</span></button>
        <button className="card wf-choice" disabled={busy} onClick={() => create(blankDoc(title), true)}><b>Talk to an agent</b><span className="small mut">Describe the goal and choose how much it asks you. The agent proposes the workflow, you decide what to keep.</span></button>
      </div>
      {tpls.length > 0 && (<><div className="small" style={{ marginTop: 12 }}><b>Or start from a template</b></div>
        <div className="grid" style={{ marginTop: 6 }}>{tpls.map((t) => <button key={t.id} className="card wf-choice" disabled={busy} onClick={() => create({ ...t.doc, name: name.trim() || t.name, id: "" } as Doc, false)}><b>{t.name}</b><span className="small mut">{t.description} · {t.nodes} boxes</span></button>)}</div></>)}
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 12 }}><button className="link" onClick={onImport}>Import a workflow file instead</button><span className="grow" /><button onClick={onClose}>Cancel</button></div>
    </Modal>
  );
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: (id: string | null) => void }) {
  const [text, setText] = useState(""), [err, setErr] = useState(""), [clash, setClash] = useState<string | null>(null), [how, setHow] = useState("rename");
  let parsed: any = null;
  try { parsed = JSON.parse(text); } catch { parsed = null; }
  const many: any[] | null = parsed && Array.isArray(parsed.workflows) ? parsed.workflows : null;
  const go = async (mode: string) => {
    if (!parsed) { setErr("That is not valid JSON."); return; }
    try {
      if (many) {
        const r = await api<{ imported: string[]; skipped: string[]; errors: string[] }>("/api/workflows/import", { method: "POST", body: JSON.stringify({ ...parsed, on_conflict: how }) });
        toast(`Imported ${r.imported.length}${r.skipped.length ? `, left ${r.skipped.length} that already exist` : ""}${r.errors.length ? `, ${r.errors.length} could not be read` : ""}`);
        onDone(null);
        return;
      }
      const r = await api<Doc>("/api/workflows/import", { method: "POST", body: JSON.stringify({ workflow: parsed, on_conflict: mode }) }); toast("Workflow imported"); onDone(r.id);
    } catch (e) { const m = (e as Error).message; if (m.includes("already exists")) setClash(m); else setErr(m); }
  };
  return (
    <Modal title={many ? "Import workflows" : "Import a workflow"} onClose={onClose}>
      <label style={{ marginTop: 8 }}>Workflow file<input type="file" accept=".json,application/json" aria-label="Workflow file" onChange={async (e) => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setClash(null); } }} /></label>
      <label style={{ marginTop: 8 }}>…or paste it<textarea rows={6} value={text} aria-label="Pasted workflow" onChange={(e) => { setText(e.target.value); setClash(null); }} /></label>
      {many && <label style={{ marginTop: 8 }}>This file holds {many.length} workflow(s). If a name is taken<select value={how} aria-label="If a name is taken" onChange={(e) => setHow(e.target.value)}><option value="rename">keep both (rename the new one)</option><option value="replace">replace it (a new version)</option><option value="cancel">leave the existing one</option></select></label>}
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)" }}>{err}</div>}
      {clash ? (<div className="card" role="alert" style={{ marginTop: 8 }}><div className="small">{clash}. What should happen?</div>
        <div className="row" style={{ marginTop: 6 }}><button className="pri" onClick={() => go("replace")}>Replace it (new version)</button><button onClick={() => go("rename")}>Keep both</button><button onClick={onClose}>Cancel</button></div></div>)
        : <div className="row" style={{ marginTop: 10 }}><button className="pri" disabled={!text.trim()} onClick={() => go("cancel")}>{many ? `Import ${many.length}` : "Import"}</button><button onClick={onClose}>Cancel</button></div>}
    </Modal>
  );
}

export default function FlowsTab() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [q, setQ] = useState(""), [status, setStatus] = useState("");
  const [adding, setAdding] = useState(false), [importing, setImporting] = useState(false);
  const [del, setDel] = useState<{ row: Row; usedBy: string[] } | null>(null), [ren, setRen] = useState<Row | null>(null), [newName, setNewName] = useState("");
  const [err, setErr] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set()), [open, setOpen] = useState<Record<string, Past[] | null>>({}), [settings, setSettings] = useState(false);
  const load = useCallback(() => api<{ workflows: Row[] }>("/api/workflows").then((r) => setRows(r.workflows)).catch((e) => { setErr(e.message); setRows([]); }), []);
  useEffect(() => { load(); }, [load]);
  const shown = useMemo(() => (rows ?? []).filter((r) => (!q || `${r.name} ${r.description} ${r.id}`.toLowerCase().includes(q.toLowerCase())) && (!status || (status === "never" ? !r.last_run : r.last_run?.status === status))), [rows, q, status]);
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); toast(msg); setErr(""); load(); } catch (e) { setErr((e as Error).message); } };
  const askDelete = async (row: Row) => { const r = await api<{ used_by: string[] }>(`/api/workflows/${row.id}/used-by`).catch(() => ({ used_by: [] })); setDel({ row, usedBy: r.used_by }); };
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const showRuns = async (id: string) => {
    if (id in open) { setOpen(({ [id]: _gone, ...rest }) => rest); return; }
    setOpen((o) => ({ ...o, [id]: null }));
    const r = await api<{ runs: Past[] }>(`/api/workflows/${id}/runs?limit=5`).catch(() => ({ runs: [] as Past[] }));
    setOpen((o) => (id in o ? { ...o, [id]: r.runs } : o));
  };
  const exportPicked = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/workflows/export`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [...picked] }) });
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url; a.download = "workflows.bundle.json"; a.click();
      URL.revokeObjectURL(url);
      toast(`Exported ${picked.size} workflow(s)`);
    } catch (e) { setErr((e as Error).message); }
  };
  const rename = async (ren: Row) => {
    try { const d = await api<Doc>(`/api/workflows/${ren.id}`); await api(`/api/workflows/${ren.id}`, { method: "PUT", body: JSON.stringify({ ...d, name: newName, label: "Renamed" }) }); toast("Renamed"); setRen(null); load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div data-testid="flows-tab">
      <div className="row" style={{ marginBottom: 12 }}>
        <input value={q} placeholder="Search workflows" aria-label="Search workflows" onChange={(e) => setQ(e.target.value)} />
        <select value={status} aria-label="Last run" onChange={(e) => setStatus(e.target.value)}><option value="">Any last run</option><option value="done">Last run worked</option><option value="error">Last run failed</option><option value="stopped">Last run stopped</option><option value="never">Never run</option></select>
        <span className="grow" /><InfoTip id="workflows" />{picked.size > 0 && <button onClick={exportPicked}>Export {picked.size} selected</button>}<button onClick={() => setSettings(true)}>Settings</button><button onClick={() => setImporting(true)}>Import</button><button className="acc" onClick={() => setAdding(true)}>New workflow</button>
      </div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginBottom: 8 }}>{err}</div>}
      {rows && rows.length === 0 && <div className="card" data-testid="flows-empty">No workflows yet. <button className="link" onClick={() => setAdding(true)}>Create the first one</button>: build it yourself, or describe it and let an agent propose it.</div>}
      {rows && rows.length > 0 && shown.length === 0 && <div className="small mut">No workflow matches.</div>}
      {shown.length > 0 && (
        <table className="tbl wf-table" data-testid="flows-table"><thead><tr><th><input type="checkbox" aria-label="Select all" checked={shown.length > 0 && shown.every((r) => picked.has(r.id))} onChange={(e) => setPicked(e.target.checked ? new Set(shown.map((r) => r.id)) : new Set())} /></th><th>Name</th><th>Boxes</th><th>Asks</th><th>Last run</th><th>Version</th><th>Updated</th><th /></tr></thead><tbody>
          {shown.flatMap((r) => [(<tr key={r.id}>
            <td><input type="checkbox" aria-label={`Select ${r.id}`} checked={picked.has(r.id)} onChange={() => toggle(r.id)} /></td>
            <td><div className="wf-namecell"><button className="icon-btn" aria-label={`Show earlier runs of ${r.id}`} aria-expanded={r.id in open} onClick={() => showRuns(r.id)}>{r.id in open ? "▾" : "▸"}</button> <button className="link" onClick={() => router.push(`/workflows/edit/?id=${r.id}`)}><b>{r.name}</b></button>
              {r.trigger === "webhook" && <span className="pill" style={{ marginLeft: 6 }}>{r.has_hook ? "webhook" : "webhook (no address yet)"}</span>}{r.trigger === "schedule" && <span className={`pill ${r.schedule?.enabled ? "ok" : ""}`} style={{ marginLeft: 6 }}>{r.schedule?.enabled ? `scheduled · next ${new Date((r.schedule.next_run ?? 0) * 1000).toLocaleString()}` : "schedule off"}</span>}{r.has_draft && <span className="pill warn" style={{ marginLeft: 6 }}>unsaved draft</span>}</div><div className="small mut wf-namesub">{r.description || r.id}</div></td>
            <td data-label="Boxes">{r.nodes}</td><td data-label="Asks"><span className="pill">{AUTO_LABEL[r.autonomy] ?? r.autonomy}</span></td>
            <td data-label="Last run" className="nowrap">{r.last_run ? <><span className={`pill ${dot(r.last_run.status)}`}>{r.last_run.status}</span> <span className="small mut">{ago(r.last_run.started)}</span></> : <span className="small mut">never</span>}</td>
            <td data-label="Version">v{r.version}</td><td data-label="Updated" className="small nowrap">{ago(r.updated)}</td>
            <td style={{ whiteSpace: "nowrap" }}><button className="small" onClick={() => router.push(`/workflows/edit/?id=${r.id}`)}>Open</button> <button className="small" onClick={() => router.push(`/workflows/edit/?id=${r.id}&run=1`)} aria-label={`Run ${r.id}`}>Run</button>{" "}
              <details className="wf-menu"><summary aria-label={`More for ${r.id}`}>⋯</summary><div className="wf-menu-list">
                <button onClick={() => act(() => api(`/api/workflows/${r.id}/duplicate`, { method: "POST", body: "{}" }), "Duplicated")}>Duplicate</button>
                <button onClick={() => { setRen(r); setNewName(r.name); }}>Rename</button>
                <a className="btn" href={`${API_BASE}/api/workflows/${r.id}/export`} download>Export JSON</a>
                <button onClick={() => act(async () => { const a = await api<{ agent: string }>(`/api/workflows/${r.id}/to-agent`, { method: "POST" }); toast(`Agent created: ${a.agent}`); }, "Turned into an agent")}>Turn into an agent</button>
                <button onClick={() => askDelete(r)}>Delete…</button></div></details></td></tr>),
            ...(r.id in open ? [(<tr key={`${r.id}-runs`} data-testid="runs-row"><td /><td colSpan={7}>{open[r.id] === null ? <span className="small mut">Loading…</span> : open[r.id]!.length === 0 ? <span className="small mut">No earlier runs.</span>
              : open[r.id]!.map((p) => <div key={p.id} className="row small"><span className={`pill ${dot(p.status)}`}>{p.status}</span><span className="grow">{p.task || "(no task)"}</span><span className="mut">v{p.version} · {ago(p.started)}{p.cost_usd > 0 ? ` · $${p.cost_usd.toFixed(4)}` : ""}</span></div>)}
              <button className="link small" onClick={() => router.push(`/workflows/?tab=runs&workflow=${r.id}`)}>See all runs of {r.name}</button></td></tr>)] : [])])}
        </tbody></table>)}
      {adding && <NewWorkflow onClose={() => setAdding(false)} onImport={() => { setAdding(false); setImporting(true); }} />}
      {importing && <ImportDialog onClose={() => setImporting(false)} onDone={(id) => { setImporting(false); if (id) router.push(`/workflows/edit/?id=${id}`); else load(); }} />}
      {settings && <WorkflowSettings onClose={() => setSettings(false)} />}
      {del && <ConfirmDelete what={`the workflow ${del.row.name}`} usedBy={del.usedBy} onClose={() => setDel(null)} onConfirm={() => { const r = del.row; setDel(null); act(() => api(`/api/workflows/${r.id}?force=true`, { method: "DELETE" }), "Workflow deleted"); }} />}
      {ren && <Modal title={`Rename ${ren.name}`} onClose={() => setRen(null)}><label style={{ marginTop: 8 }}>New name<input value={newName} aria-label="New name" onChange={(e) => setNewName(e.target.value)} /></label>
        <div className="row" style={{ marginTop: 10 }}><button className="pri" disabled={!newName.trim()} onClick={() => rename(ren)}>Rename</button><button onClick={() => setRen(null)}>Cancel</button></div></Modal>}
    </div>
  );
}
