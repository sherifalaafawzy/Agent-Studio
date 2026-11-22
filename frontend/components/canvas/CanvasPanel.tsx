"use client";
// The canvas beside the chat: a document you and an agent edit together. You type anywhere (it saves by itself, with the version you were looking at); the agent's
// changes arrive as highlighted proposals you accept or reject; every change is a version you can compare and bring back. Nothing in it is ever run.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, API_BASE, api } from "@/lib/api";
import { artifactUrl } from "@/lib/artifacts";
import { askMessage, authorText, type Canvas, type Edit, findRange, LANGUAGES, lineDiff, type Proposal, TYPE_LABEL, type VersionRow } from "@/lib/canvas";
import { toast } from "../Nav";
import Markdown from "../Markdown";
import { IX } from "../Icons";
import { CsvTable } from "../RichBlocks";
import HtmlPreview from "../rich/HtmlPreview";
import CodeEditor from "./CodeEditor";

const SAVE_DELAY = 800;
type Conflict = { id: string; message: string };
const clip = (s: string, n = 140) => (s.length > n ? s.slice(0, n) + "…" : s);

function EditView({ e }: { e: Edit }) {
  return (
    <div className="cv-edit">
      {e.at ? <div className="d-add">+ add at the {e.at}: {clip(e.new)}</div> : <><div className="d-del">− {clip(e.old ?? "")}</div><div className="d-add">+ {clip(e.new)}</div></>}
      {!e.fits && <div className="small warn">This part no longer fits the text.</div>}
    </div>
  );
}

export default function CanvasPanel({ canvasId, refreshKey = 0, onClose, onAsk, onGone }: { canvasId: string; refreshKey?: number; onClose: () => void; onAsk: (text: string) => void; onGone?: () => void }) {
  const [c, setC] = useState<Canvas | null>(null);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [status, setStatus] = useState<"saved" | "saving" | "error">("saved");
  const [err, setErr] = useState("");
  const [behind, setBehind] = useState<Canvas | null>(null);
  const [props, setProps] = useState<Proposal[]>([]);
  const [view, setView] = useState<"edit" | "preview">("edit");
  const [vers, setVers] = useState<VersionRow[] | null>(null);
  const [shown, setShown] = useState<{ version: number; content: string; label: string } | null>(null);
  const [sel, setSel] = useState<{ start: number; end: number } | null>(null);
  const [ask, setAsk] = useState("");
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const [title, setTitle] = useState("");
  const textRef = useRef(""), savedRef = useRef(""), cRef = useRef<Canvas | null>(null);
  const goneRef = useRef(onGone);
  textRef.current = text; savedRef.current = saved; cRef.current = c; goneRef.current = onGone;

  const adopt = useCallback((nc: Canvas) => { setC(nc); setTitle(nc.title); setText(nc.content ?? ""); setSaved(nc.content ?? ""); setBehind(null); setErr(""); setStatus("saved"); }, []);

  const load = useCallback(async () => {
    try {
      const [nc, np] = await Promise.all([api<Canvas>(`/api/canvas/${canvasId}`), api<Proposal[]>(`/api/canvas/${canvasId}/proposals`)]);
      setProps(np);
      const cur = cRef.current;
      if (!cur || cur.id !== nc.id || textRef.current === savedRef.current) { if (!cur || cur.id !== nc.id || nc.version !== cur.version) adopt(nc); else setC(nc); }
      else if (nc.version !== cur.version) setBehind(nc);
    } catch (e) { if (e instanceof ApiError && e.status === 404) goneRef.current?.(); else setErr((e as Error).message); }
  }, [canvasId, adopt]);

  useEffect(() => { setC(null); setProps([]); setVers(null); setShown(null); setSel(null); setConflict(null); setView("edit"); void load(); }, [canvasId, load]);
  useEffect(() => { if (refreshKey) void load(); }, [refreshKey, load]);                 // the agent changed it: refresh without closing the editor

  useEffect(() => { // live updates: the agent's changes and edits made in another tab
    if (typeof EventSource === "undefined") return;
    const es = new EventSource(`${API_BASE}/api/canvas/${canvasId}/events`);
    es.onmessage = () => { void load(); };
    return () => es.close();
  }, [canvasId, load]);

  const save = useCallback(async (): Promise<boolean> => {
    const cur = cRef.current, content = textRef.current;
    if (!cur || content === savedRef.current) return true;
    setStatus("saving");
    try {
      const nc = await api<Canvas>(`/api/canvas/${cur.id}`, { method: "PUT", body: JSON.stringify({ content, base_version: cur.version }) });
      setC(nc); setSaved(content); setStatus("saved"); setErr("");
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) { const latest = await api<Canvas>(`/api/canvas/${cur.id}`); if (latest.content === content) adopt(latest); else setBehind(latest); setStatus("saved"); }
      else { setStatus("error"); setErr((e as Error).message); }
      return false;
    }
  }, [adopt]);

  useEffect(() => { // save a moment after the typing stops
    if (!c || behind || text === saved) return;
    const t = setTimeout(() => { void save(); }, SAVE_DELAY);
    return () => clearTimeout(t);
  }, [text, saved, c, behind, save]);

  const selStart = sel?.start ?? 0, selEnd = sel?.end ?? 0;
  useEffect(() => { // tell the server what is selected, so "make this shorter" can target it
    if (!c || selEnd <= selStart) return;
    const t = setTimeout(() => { void save().then((ok) => { if (ok) api(`/api/canvas/${c.id}/selection`, { method: "POST", body: JSON.stringify({ start: selStart, end: selEnd }) }).catch(() => undefined); }); }, 300);
    return () => clearTimeout(t);
  }, [selStart, selEnd, c, save]);

  const marks = useMemo(() => props.flatMap((p) => p.edits.map((e) => findRange(text, e)).filter((r): r is { from: number; to: number } => r !== null)), [props, text]);

  async function resolve(path: string, body: object = {}): Promise<Canvas | null> {
    if (!(await save())) return null;
    return api<Canvas>(`/api/canvas/${canvasId}${path}`, { method: "POST", body: JSON.stringify(body) });
  }
  async function accept(p: Proposal, skip = false) {
    try { const out = await resolve(`/proposals/${p.id}/accept`, { skip_conflicts: skip }); if (out) { adopt(out); setConflict(null); await load(); } }
    catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict({ id: p.id, message: (e.detail as any)?.message || "This change does not fit any more." });
      else toast((e as Error).message, "err");
    }
  }
  async function reject(p: Proposal) { try { await api(`/api/canvas/${canvasId}/proposals/${p.id}/reject`, { method: "POST" }); setConflict(null); await load(); } catch (e) { toast((e as Error).message, "err"); } }
  async function acceptAll(skip = false) {
    try { const out = await resolve("/accept-all", { skip_conflicts: skip }); if (out) { adopt(out); setConflict(null); await load(); } }
    catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict({ id: "all", message: (e.detail as any)?.message || "A change does not fit any more." });
      else toast((e as Error).message, "err");
    }
  }
  async function rejectAll() { try { await api(`/api/canvas/${canvasId}/reject-all`, { method: "POST" }); setConflict(null); await load(); } catch (e) { toast((e as Error).message, "err"); } }

  async function openVersions() {
    if (vers) { setVers(null); setShown(null); return; }
    try { setVers(await api<VersionRow[]>(`/api/canvas/${canvasId}/versions`)); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function showVersion(v: VersionRow) {
    try { const r = await api<{ version: number; content: string }>(`/api/canvas/${canvasId}/versions/${v.version}`); setShown({ ...r, label: `Version ${r.version} · ${authorText(v.author)}` }); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function restore(version: number) {
    try { const out = await resolve("/restore", { version }); if (out) { adopt(out); setShown(null); setVers(await api<VersionRow[]>(`/api/canvas/${canvasId}/versions`)); toast(`Version ${version} is back`); } }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function keepAsArtifact() {
    try { const out = await resolve("/snapshot"); if (out) { setC(out); toast("Kept on the Artifacts page"); } } catch (e) { toast((e as Error).message, "err"); }
  }
  async function saveTitle() {
    if (!c || title.trim() === c.title) return;
    try { setC(await api<Canvas>(`/api/canvas/${c.id}`, { method: "PUT", body: JSON.stringify({ title }) })); } catch (e) { toast((e as Error).message, "err"); setTitle(c.title); }
  }
  async function setLanguage(language: string) {
    if (!c) return;
    try { setC(await api<Canvas>(`/api/canvas/${c.id}`, { method: "PUT", body: JSON.stringify({ language }) })); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function remove() {
    try { await api(`/api/canvas/${canvasId}`, { method: "DELETE" }); onGone?.(); } catch (e) { toast((e as Error).message, "err"); }
    setConfirmDel(false);
  }
  async function keepMine() {
    if (!behind || !c) return;
    try { const nc = await api<Canvas>(`/api/canvas/${c.id}`, { method: "PUT", body: JSON.stringify({ content: text, base_version: behind.version }) }); setC(nc); setSaved(text); setBehind(null); setStatus("saved"); }
    catch (e) { toast((e as Error).message, "err"); }
  }

  if (!c) return <aside className="canvas-panel" aria-label="Canvas"><div className="small mut" role="status" style={{ padding: 16 }}>{err || "Opening the canvas…"}</div></aside>;
  const hasSel = !!sel && sel.end > sel.start;
  const canPreview = c.type === "markdown" || c.type === "html" || c.type === "table";
  const diff = shown ? lineDiff(shown.content, text) : [];
  return (
    <aside className="canvas-panel" aria-label="Canvas">
      <div className="canvas-head">
        <input className="canvas-title" aria-label="Canvas title" value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} onBlur={() => { void saveTitle(); }} />
        <span className="pill">{TYPE_LABEL[c.type]}</span>
        {c.type === "code" && <select aria-label="Language" value={c.language || "text"} onChange={(e) => { void setLanguage(e.target.value); }}>{LANGUAGES.map((l) => <option key={l}>{l}</option>)}</select>}
        <span className="grow" />
        <span className="small mut" role="status">{status === "saving" ? "Saving…" : status === "error" ? "Not saved" : text === saved ? `Saved · version ${c.version}` : "Unsaved changes"}</span>
        <button className="icon-btn" aria-label="Close canvas" onClick={onClose}><IX size={16} /></button>
      </div>
      <div className="canvas-bar">
        {canPreview && <span className="seg" role="group" aria-label="Edit or preview">
          <button className={view === "edit" ? "on" : ""} aria-pressed={view === "edit"} onClick={() => setView("edit")}>Edit</button>
          <button className={view === "preview" ? "on" : ""} aria-pressed={view === "preview"} onClick={() => setView("preview")}>Preview</button></span>}
        <button className="ghost small" aria-pressed={!!vers} onClick={() => { void openVersions(); }}>Versions</button>
        <button className="ghost small" onClick={() => { void keepAsArtifact(); }}>Keep in Artifacts</button>
        {c.artifact_id && <a className="ghost small btn" href={`/artifacts/?id=${c.artifact_id}`} target="_blank" rel="noopener noreferrer">Open in Artifacts</a>}
        {c.artifact_id && <a className="ghost small btn" href={artifactUrl(c.artifact_id, true)} download>Download</a>}
        {confirmDel ? <span className="row"><span className="small">Delete this canvas and its versions?</span><button className="danger small" onClick={() => { void remove(); }}>Delete</button><button className="ghost small" onClick={() => setConfirmDel(false)}>Cancel</button></span>
          : <button className="ghost small" onClick={() => setConfirmDel(true)}>Delete…</button>}
      </div>
      {err && <div className="err small" role="alert" style={{ padding: "0 12px" }}>{err}</div>}
      {behind && (
        <div className="canvas-merge" role="alert">
          <b>This canvas changed while you were typing</b> (now at version {behind.version}). Choose what to keep:
          <div className="row" style={{ flexWrap: "wrap" }}>
            <button onClick={() => { void keepMine(); }}>Keep my text</button>
            <button onClick={() => adopt(behind)}>Take the new version</button>
            <button className="ghost" onClick={() => setShown({ version: behind.version, content: behind.content ?? "", label: `The new version ${behind.version}` })}>Compare</button>
          </div>
        </div>)}
      {props.length > 0 && (
        <div className="canvas-pending" data-testid="pending">
          <div className="row" style={{ flexWrap: "wrap" }}><b>{props.length} change{props.length === 1 ? "" : "s"} from the agent to review</b><span className="grow" />
            <button className="acc small" onClick={() => { void acceptAll(); }}>Accept all</button><button className="ghost small" onClick={() => { void rejectAll(); }}>Reject all</button></div>
          {conflict && (
            <div className="canvas-merge" role="alert">{conflict.message}
              <div className="row" style={{ flexWrap: "wrap" }}>
                <button onClick={() => { if (conflict.id === "all") void acceptAll(true); else void accept(props.find((p) => p.id === conflict.id)!, true); }}>Apply the parts that fit</button>
                <button className="ghost" onClick={() => setConflict(null)}>Leave it for now</button></div></div>)}
          {props.map((p) => (
            <div key={p.id} className="cv-card">
              <div className="small"><b>{authorText(p.author)}</b>{p.note ? `: ${p.note}` : ""}</div>
              {p.edits.map((e) => <EditView key={e.n} e={e} />)}
              <div className="row"><button className="acc small" onClick={() => { void accept(p); }}>Accept</button><button className="ghost small" onClick={() => { void reject(p); }}>Reject</button></div>
            </div>))}
        </div>)}
      {vers && (
        <div className="canvas-versions" data-testid="versions">
          <ul>{vers.map((v) => <li key={v.version}><button className="link" onClick={() => { void showVersion(v); }}>Version {v.version}</button> · {authorText(v.author)}{v.note ? ` · ${v.note}` : ""} · {new Date(v.created * 1000).toLocaleString()}</li>)}</ul>
        </div>)}
      {shown && (
        <div className="canvas-diff" data-testid="diff-view">
          <div className="row" style={{ flexWrap: "wrap" }}><b>{shown.label} compared with what is in the canvas now</b><span className="grow" />
            {vers && <button className="acc small" onClick={() => { void restore(shown.version); }}>Bring this version back</button>}<button className="ghost small" onClick={() => setShown(null)}>Close</button></div>
          <pre className="diff-lines">{diff.map((l, i) => <span key={i} className={l.kind === "add" ? "d-add" : l.kind === "del" ? "d-del" : ""}>{(l.kind === "add" ? "+ " : l.kind === "del" ? "− " : "  ") + l.text + "\n"}</span>)}</pre>
        </div>)}
      {hasSel && view === "edit" && (
        <form className="canvas-ask" onSubmit={(e) => { e.preventDefault(); if (ask.trim()) { onAsk(askMessage(title, c.id, ask, text.slice(sel!.start, sel!.end))); setAsk(""); } }}>
          <span className="small mut">{sel!.end - sel!.start} characters selected</span>
          <input aria-label="Ask the agent about the selection" placeholder="e.g. make this shorter" value={ask} onChange={(e) => setAsk(e.target.value)} />
          <button className="acc small" type="submit" disabled={!ask.trim()}>Ask the agent</button>
        </form>)}
      <div className="canvas-body">
        {canPreview && view === "preview"
          ? <div className="canvas-preview" data-testid="preview">{c.type === "markdown" ? <Markdown text={text} /> : c.type === "html" ? <HtmlPreview plain html={text} title={title} /> : <CsvTable plain text={text} />}</div>
          : <CodeEditor value={text} onChange={setText} type={c.type} language={c.language} marks={marks} onSelect={setSel} label={`Text of ${title}`} placeholder="Start typing, or ask the agent to write something." />}
      </div>
    </aside>
  );
}
