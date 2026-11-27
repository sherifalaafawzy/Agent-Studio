"use client";
// The workflow editor: a full-screen canvas with a box palette, a right-hand settings panel, a builder chat, undo/redo and a run drawer.
// Edits are autosaved as a draft; "Save version" publishes them. Runs use the saved version.
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { Modal } from "@/components/Modal";
import InfoTip from "@/components/InfoTip";
import FlowCanvas from "@/components/workflows/FlowCanvas";
import { BuilderChat, Drawer, NodePanel, Palette, Versions, type DrawerTab } from "@/components/workflows/EditorParts";
import type { Ctx } from "@/components/workflows/Fields";
import { AUTONOMY, defaultConfig, ENTRY, NO_REFS, portsOf, tidy, uniqueId, type Catalog, type Doc, type NodeSpec, type Problem, type Refs, type Run, type WEdge, type WNode } from "@/lib/workflows";
import { usePageContext } from "@/lib/pagectx";

const EMPTY: Catalog = { groups: [], nodes: [] };

function Editor() {
  const id = useSearchParams().get("id") ?? "";
  const wantChat = useSearchParams().get("chat") === "1";
  const wantRun = useSearchParams().get("run") === "1";
  const wantVersions = useSearchParams().get("versions") === "1";
  const [doc, setDoc] = useState<Doc | null>(null);
  const [past, setPast] = useState<Doc[]>([]), [future, setFuture] = useState<Doc[]>([]);
  const [dirty, setDirty] = useState(false), [restored, setRestored] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  usePageContext("workflow", () => ({ name: doc?.name, boxes: doc?.nodes.length, selectedBox: doc?.nodes.find((n) => n.id === sel)?.label ?? null }));
  const [catalog, setCatalog] = useState<Catalog>(EMPTY);
  const [ctx, setCtx] = useState<Ctx>({ agents: [], tools: [], srcs: [], workflows: [], refs: NO_REFS });
  const [selIds, setSelIds] = useState<string[]>([]);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [run, setRun] = useState<Run | null>(null), [task, setTask] = useState(""), [runOpen, setRunOpen] = useState(false);
  const [palette, setPalette] = useState(false), [chat, setChat] = useState(wantChat), [drawer, setDrawer] = useState(false), [dTab, setDTab] = useState<DrawerTab>("run"), [versions, setVersions] = useState(wantVersions), [saving, setSaving] = useState(false);
  const [label, setLabel] = useState(""), [err, setErr] = useState(""), [clip, setClip] = useState<{ nodes: WNode[]; edges: WEdge[] } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const specs = useMemo(() => Object.fromEntries(catalog.nodes.map((n) => [n.type, n])) as Record<string, NodeSpec>, [catalog]);

  // -- loading
  useEffect(() => {
    if (!id) return;
    api<Catalog>("/api/workflows/nodes").then(setCatalog).catch(() => setCatalog(EMPTY));
    Promise.all([api<any[]>("/api/agents").catch(() => []), api<any[]>("/api/tools").catch(() => []), api<{ apis: Ctx["srcs"] }>("/api/workflows/apis").catch(() => ({ apis: [] })), api<{ workflows: Ctx["workflows"] }>("/api/workflows").catch(() => ({ workflows: [] })), api<Refs>("/api/workflows/refs").catch(() => NO_REFS)])
      .then(([agents, tools, apis, wfs, refs]) => setCtx({ agents, tools, srcs: apis.apis, workflows: wfs.workflows.filter((w) => w.id !== id), refs }));
    (async () => {
      try {
        const d = await api<Doc>(`/api/workflows/${id}`);
        setProblems(d.problems ?? []);
        const draft = await api<{ doc: Doc }>(`/api/workflows/${id}/draft`).catch(() => null);
        if (draft) { setDoc({ ...draft.doc, version: d.version }); setDirty(true); setRestored(true); } else { setDoc(d); if (wantRun) setRunOpen(true); }
      } catch (e) { setErr((e as Error).message); }
    })();
  }, [id, wantRun]);
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  // -- autosave the draft
  useEffect(() => {
    if (!doc || !dirty) return;
    const t = setTimeout(() => { api(`/api/workflows/${id}/draft`, { method: "PUT", body: JSON.stringify(doc) }).catch(() => undefined); }, 1500);
    return () => clearTimeout(t);
  }, [doc, dirty, id]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // -- editing with history
  const apply = useCallback((next: Doc, commit = true) => {
    setDoc((cur) => { if (commit && cur) { setPast((p) => [...p.slice(-49), cur]); setFuture([]); } return next; });
    setDirty(true);
  }, []);
  const undo = () => setPast((p) => { if (!p.length || !doc) return p; setFuture((f) => [doc, ...f]); setDoc(p[p.length - 1]); setDirty(true); return p.slice(0, -1); });
  const redo = () => setFuture((f) => { if (!f.length || !doc) return f; setPast((p) => [...p, doc]); setDoc(f[0]); setDirty(true); return f.slice(1); });
  const mutate = (fn: (d: Doc) => Doc) => doc && apply(fn(doc));
  const addNode = (type: string, preset?: Record<string, unknown>, lbl?: string, pos?: { x: number; y: number }) => {
    const cur = doc as Doc;                                              // the palette exists only once the workflow has loaded
    const spec = specs[type];
    const from = cur.nodes.find((n) => n.id === sel) ?? cur.nodes[cur.nodes.length - 1];
    const nid = uniqueId(cur.nodes, type);
    const position = pos ?? { x: (from?.position.x ?? 0) + 290, y: (from?.position.y ?? 120) + (cur.edges.filter((e) => e.source === from?.id).length * 140) };
    const node: WNode = { id: nid, type, label: lbl ?? spec?.label ?? type, position, config: { ...(spec ? defaultConfig(spec) : {}), ...(preset ?? {}) } };
    const canLink = from && from.type !== "output" && from.type !== "note" && type !== "note" && !pos;
    const port = from ? portsOf(from, specs[from.type])[0] : undefined;
    apply({ ...cur, nodes: [...cur.nodes, node], edges: canLink ? [...cur.edges, { source: from.id, target: nid, ...(port && port !== "out" ? { source_port: port } : {}) }] : cur.edges });
    setSel(nid); setPalette(false);
  };
  const delNode = (nid: string) => { mutate((d) => ({ ...d, nodes: d.nodes.filter((n) => n.id !== nid), edges: d.edges.filter((e) => e.source !== nid && e.target !== nid) })); setSel(null); };
  const dupNode = (nid: string) => {
    const cur = doc as Doc;                                              // reached from the panel or Ctrl+D, which need a box to be selected
    const n = cur.nodes.find((x) => x.id === nid);
    if (!n || ENTRY.includes(n.type)) return;
    const copy: WNode = { ...JSON.parse(JSON.stringify(n)), id: uniqueId(cur.nodes, n.type), label: `${n.label} copy`, position: { x: n.position.x + 40, y: n.position.y + 60 } };
    apply({ ...cur, nodes: [...cur.nodes, copy] }); setSel(copy.id);
  };
  const copy = () => {
    if (!doc) return;
    const ids = new Set(selIds.length > 1 ? selIds : sel ? [sel] : []);
    const nodes = doc.nodes.filter((n) => ids.has(n.id) && !ENTRY.includes(n.type));
    if (!nodes.length) return;
    const keep = new Set(nodes.map((n) => n.id));
    setClip({ nodes, edges: doc.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) });
    toast(nodes.length > 1 ? `Copied ${nodes.length} boxes` : "Copied");
  };
  const paste = () => {
    if (!doc || !clip) return;
    const added: WNode[] = [], renamed: Record<string, string> = {};
    for (const n of clip.nodes) {
      const nid = uniqueId([...doc.nodes, ...added], n.type);
      renamed[n.id] = nid;
      added.push({ ...JSON.parse(JSON.stringify(n)), id: nid, position: { x: n.position.x + 40, y: n.position.y + 60 } });
    }
    const edges = clip.edges.map((e) => ({ source: renamed[e.source], target: renamed[e.target], ...(e.source_port ? { source_port: e.source_port } : {}) }));
    apply({ ...doc, nodes: [...doc.nodes, ...added], edges: [...doc.edges, ...edges] });
    setSel(added[added.length - 1].id); setSelIds(added.map((x) => x.id));
  };
  const switchEntry = (type: string) => {
    const entry = doc?.nodes.find((n) => ENTRY.includes(n.type));
    const spec = specs[type];
    if (!doc || !entry || !spec) return;
    apply({ ...doc, nodes: doc.nodes.map((n) => (n.id === entry.id ? { ...n, type, label: type === "webhook" ? "Webhook" : type === "schedule" ? "Schedule" : "Task", config: defaultConfig(spec) } : n)) });
  };

  // -- saving, checking, running
  const check = async () => {
    try { const r = await api<{ ok: boolean; problems: Problem[] }>("/api/workflows/validate", { method: "POST", body: JSON.stringify(doc) }); setProblems(r.problems); setDTab("problems"); setDrawer(true); toast(r.ok ? "No problems found" : "Some things need fixing", r.ok ? "ok" : "warn"); } catch (e) { setErr((e as Error).message); }
  };
  const saveVersion = async () => {
    if (!doc) return;
    setSaving(true);
    try {
      const r = await api<Doc>(`/api/workflows/${id}`, { method: "PUT", body: JSON.stringify({ ...doc, label }) });
      setDoc(r); setProblems(r.problems ?? []); setDirty(false); setRestored(false); setErr(""); setLabel(""); setPast([]); setFuture([]); toast(`Saved version ${r.version}`);
    } catch (e) { setErr((e as Error).message); } finally { setSaving(false); }
  };
  const poll = (rid: string) => {
    if (timer.current) clearInterval(timer.current);
    const tick = async () => {
      try { const r = await api<Run>(`/api/workflows/runs/${rid}`); setRun(r); if (r.status !== "running" && !Object.values(r.nodes).some((n) => n.status === "waiting_approval") && timer.current) clearInterval(timer.current); }
      catch { if (timer.current) clearInterval(timer.current); }
    };
    tick(); timer.current = setInterval(tick, 1000);
  };
  const start = async () => {
    try { const r = await api<Run>(`/api/workflows/${id}/run`, { method: "POST", body: JSON.stringify({ task }) }); setRun(r); setDTab("run"); setDrawer(true); setRunOpen(false); setErr(""); poll(r.id); } catch (e) { setErr((e as Error).message); }
  };
  const decide = (node: string, d: string, values?: Record<string, unknown>) => run && api(`/api/workflows/runs/${run.id}/decide`, { method: "POST", body: JSON.stringify({ node, decision: d, values }) }).then(() => poll(run.id)).catch((e) => setErr(e.message));
  const runFromWhy = dirty ? "Save a version first: runs use the saved version." : !run || run.status === "plan" ? "Run the whole workflow once first, then you can run from a box and reuse that run." : run.status === "running" ? "Wait for the run to finish." : "";
  const startFrom = async (nid: string, from: Run) => {
    try { const r = await api<Run>(`/api/workflows/${id}/run`, { method: "POST", body: JSON.stringify({ task: from.task || task, from_node: nid, base: from.id }) }); setRun(r); setDTab("run"); setDrawer(true); setErr(""); poll(r.id); }
    catch (e) { setErr((e as Error).message); }
  };
  const stop = () => run && api<Run>(`/api/workflows/runs/${run.id}/stop`, { method: "POST" }).then(setRun).catch((e) => setErr(e.message));
  const restore = async (v: number) => { try { const r = await api<Doc>(`/api/workflows/${id}/restore`, { method: "POST", body: JSON.stringify({ version: v }) }); setDoc(r); setProblems(r.problems ?? []); setDirty(false); setVersions(false); toast(`Restored as version ${r.version}`); } catch (e) { setErr((e as Error).message); } };
  const discardDraft = async () => { await api(`/api/workflows/${id}/draft`, { method: "DELETE" }).catch(() => undefined); const d = await api<Doc>(`/api/workflows/${id}`); setDoc(d); setDirty(false); setRestored(false); setPast([]); setFuture([]); };

  // -- keyboard
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName);
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
      else if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); saveVersion(); }
      else if (!typing && mod && e.key.toLowerCase() === "c") copy();
      else if (!typing && mod && e.key.toLowerCase() === "v") paste();
      else if (!typing && mod && e.key.toLowerCase() === "d") { e.preventDefault(); if (sel) dupNode(sel); }
      else if (!typing && e.key === "/") { e.preventDefault(); setPalette(true); }
      else if (e.key === "Escape") { setPalette(false); setSel(null); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  if (!id) return <div className="card" style={{ margin: 24 }}>No workflow chosen. <Link href="/workflows/">Back to workflows</Link></div>;
  if (!doc) return <div className="card" style={{ margin: 24 }}>{err || "Loading…"} {err && <Link href="/workflows/">Back to workflows</Link>}</div>;
  const byNode: Record<string, string> = {};
  for (const p of problems) if (p.node && p.severity === "error" && !byNode[p.node]) byNode[p.node] = p.message;
  const statuses = run && run.status !== "plan" ? Object.fromEntries(Object.entries(run.nodes).map(([k, v]) => [k, v.status])) : undefined;
  const node = doc.nodes.find((n) => n.id === sel) ?? null;
  const waiting = run ? Object.values(run.nodes).filter((n) => n.status === "waiting_approval").length : 0;
  return (
    <div className="wf-editor" data-testid="workflow-editor">
      <header className="wf-top">
        <Link className="btn" href="/workflows/" aria-label="Back to workflows">← Flows</Link>
        <input className="wf-name" value={doc.name} aria-label="Workflow name" onChange={(e) => apply({ ...doc, name: e.target.value }, false)} />
        <select value={doc.autonomy} aria-label="Autonomy" onChange={(e) => apply({ ...doc, autonomy: e.target.value })}>{AUTONOMY.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
        <span className={`pill ${dirty ? "warn" : "ok"}`}>{dirty ? "unsaved changes" : `saved · v${doc.version ?? 1}`}</span>
        {restored && <span className="small mut">your unsaved draft was restored · <button className="link" onClick={discardDraft}>discard it</button></span>}
        {problems.some((p) => p.severity === "error") && <button className="pill bad" onClick={() => { setDTab("problems"); setDrawer(true); }}>{problems.filter((p) => p.severity === "error").length} problem(s)</button>}
        <span className="grow" />
        <button onClick={undo} disabled={!past.length} aria-label="Undo">↶</button><button onClick={redo} disabled={!future.length} aria-label="Redo">↷</button>
        <button onClick={() => mutate((d) => ({ ...d, nodes: tidy(d.nodes, d.edges) }))}>Tidy up</button>
        <button onClick={check}>Check</button><button onClick={() => setVersions(true)}>Versions</button>
        <button className="pri" onClick={() => setRunOpen(true)} disabled={dirty} title={dirty ? "Save a version first: runs use the saved version" : ""}>{doc.autonomy === "suggest" ? "Show the plan" : "Run"}</button>
        <input className="wf-label" value={label} placeholder="Version label (optional)" aria-label="Version label" onChange={(e) => setLabel(e.target.value)} />
        <button className="pri" onClick={saveVersion} disabled={saving || !dirty}>Save version</button>
        <InfoTip id="wf-editor" />
      </header>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", padding: "4px 14px" }}>{err}</div>}
      {waiting > 0 && <div className="wf-banner" role="status">{waiting} step(s) waiting for you <button className="small" onClick={() => setDrawer(true)}>Open</button></div>}
      <div className="wf-body">
        <nav className="wf-rail" aria-label="Editor tools">
          <button title="Add a box (/)" aria-label="Add a box" onClick={() => setPalette(true)}>＋</button>
          <button title="Build with an agent" aria-label="Build with an agent" className={chat ? "on" : ""} onClick={() => setChat(!chat)}>✦</button>
          <button title="Run log" aria-label="Run log" className={drawer ? "on" : ""} onClick={() => setDrawer(!drawer)}>▤</button>
        </nav>
        {chat && <BuilderChat doc={doc} ctx={ctx} autonomy={doc.autonomy} onClose={() => setChat(false)} onApply={(d) => { apply({ ...doc, name: d.name, nodes: d.nodes, edges: d.edges, autonomy: d.autonomy }); toast("Changes applied: check them, then save a version"); }}
          initial={wantChat && doc.nodes.length <= 1 ? "Tell me what this workflow should do, which of your agents or APIs it may use, and how much it should ask you before acting." : undefined} />}
        {palette && <Palette catalog={catalog} ops={ctx.srcs} onAdd={addNode} onClose={() => setPalette(false)} />}
        <main className="wf-center">
          <FlowCanvas nodes={doc.nodes} edges={doc.edges} specs={specs} selected={sel} multi={selIds} statuses={statuses} problems={byNode} onSelect={setSel} onSelectMany={setSelIds}
            onChange={(nodes, edges, commit) => apply({ ...doc, nodes, edges }, commit)} onDropNode={(t, pos) => addNode(t, undefined, undefined, pos)} />
          {drawer && <Drawer run={run} problems={problems} workflowId={id} tab={dTab} onTab={setDTab} onDecide={decide} onStop={stop} onSelect={setSel} onClose={() => setDrawer(false)} onOpenRun={async (rid) => { const r = await api<Run>(`/api/workflows/runs/${rid}`); setRun(r); }} />}
        </main>
        {node && <NodePanel node={node} spec={specs[node.type]} ctx={ctx} run={run} doc={doc} saved={!dirty} runFromWhy={runFromWhy} onRunFrom={() => run && startFrom(node.id, run)} onSwitchEntry={switchEntry} onClose={() => setSel(null)} onChange={(n) => apply({ ...doc, nodes: doc.nodes.map((x) => (x.id === n.id ? n : x)) }, false)} onDelete={() => delNode(node.id)} onDuplicate={() => dupNode(node.id)} />}
      </div>
      {runOpen && (<Modal title={doc.autonomy === "suggest" ? "Show the plan" : "Run this workflow"} onClose={() => setRunOpen(false)}>
        <label style={{ marginTop: 8 }}>Task<textarea rows={4} value={task} aria-label="Task" placeholder="What should this workflow work on?" onChange={(e) => setTask(e.target.value)} autoFocus /></label>
        <div className="row" style={{ marginTop: 10 }}><button className="pri" disabled={!task.trim() || dirty} onClick={start}>{doc.autonomy === "suggest" ? "Show the plan" : "Run"}</button><button onClick={() => setRunOpen(false)}>Cancel</button></div></Modal>)}
      {versions && <Versions workflowId={id} onRestore={restore} onClose={() => setVersions(false)} />}
    </div>
  );
}

export default function EditPage() {
  return <Suspense fallback={<div className="card" style={{ margin: 24 }}>Loading…</div>}><Editor /></Suspense>;
}
