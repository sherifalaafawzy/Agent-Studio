"use client";
// The files of an app and a text editor. You type anywhere and save; the builder agent's changes arrive as proposals in the Changes tab (they never overwrite what you are typing).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { buildTree, editorKind, fmtSize, TreeFile, TreeNode } from "@/lib/apps";
import CodeEditor from "@/components/canvas/CodeEditor";
import { ConfirmDelete, Modal } from "@/components/Modal";
import { toast } from "@/components/Nav";

type Opened = { path: string; content: string | null; size: number; binary: boolean };

function Row({ n, depth, open, sel, onPick, closed, toggle }: { n: TreeNode; depth: number; open: string; sel: string; onPick: (p: string) => void; closed: Set<string>; toggle: (p: string) => void }) {
  return (
    <>
      <li role="treeitem" aria-selected={!n.dir && n.path === sel} aria-expanded={n.dir ? !closed.has(n.path) : undefined}>
        <button className={`file-row ${!n.dir && n.path === open ? "on" : ""}`} style={{ paddingLeft: 8 + depth * 14 }} onClick={() => (n.dir ? toggle(n.path) : onPick(n.path))}>
          <span aria-hidden="true">{n.dir ? (closed.has(n.path) ? "▸" : "▾") : "·"}</span> {n.name}{!n.dir && <span className="mut small"> {fmtSize(n.size)}</span>}
        </button>
      </li>
      {n.dir && !closed.has(n.path) && n.children.map((c) => <Row key={c.path} n={c} depth={depth + 1} open={open} sel={sel} onPick={onPick} closed={closed} toggle={toggle} />)}
    </>
  );
}

export default function FilePanel({ appId, files, onChanged, refreshKey }: { appId: string; files: TreeFile[]; onChanged: () => void; refreshKey: number }) {
  const [file, setFile] = useState<Opened | null>(null);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [err, setErr] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [newName, setNewName] = useState<string | null>(null);
  const [del, setDel] = useState(false);
  const dirty = file !== null && !file.binary && text !== saved;
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const tree = useMemo(() => buildTree(files), [files]);

  const openFile = useCallback(async (path: string, keepIfDirty = false) => {
    if (keepIfDirty && dirtyRef.current) return;
    try {
      const f = await api<Opened>(`/api/apps/${appId}/file?path=${encodeURIComponent(path)}`);
      const c = (f.content ?? "").replace(/\r\n/g, "\n");          // the editor always uses LF, so a file with Windows line endings is not "unsaved" the moment it opens
      setFile(f); setText(c); setSaved(c); setErr("");
    } catch (e) { setErr((e as Error).message); setFile(null); }
  }, [appId]);
  useEffect(() => { if (file && files.some((f) => f.path === file.path)) openFile(file.path, true); else if (file) setFile(null); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [refreshKey]);
  useEffect(() => { if (!file && files.length) { const first = files.find((f) => f.path === "public/index.html") ?? files.find((f) => !f.binary); if (first) openFile(first.path); } /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [files.length]);

  const pick = (p: string) => { if (dirty && !window.confirm("You have unsaved changes in this file. Open another file anyway?")) return; openFile(p); };
  const save = async () => {
    if (!file) return;
    try { await api(`/api/apps/${appId}/file`, { method: "PUT", body: JSON.stringify({ path: file.path, content: text }) }); setSaved(text); setErr(""); toast("Saved"); onChanged(); }
    catch (e) { setErr((e as Error).message); }
  };
  const create = async () => {
    const path = (newName ?? "").trim();
    if (!path) return;
    try { await api(`/api/apps/${appId}/file`, { method: "PUT", body: JSON.stringify({ path, content: "" }) }); setNewName(null); setErr(""); onChanged(); await openFile(path); }
    catch (e) { setErr((e as Error).message); setNewName(null); }
  };
  const remove = async () => {
    if (!file) return;
    try { await api(`/api/apps/${appId}/file?path=${encodeURIComponent(file.path)}`, { method: "DELETE" }); setDel(false); setFile(null); setErr(""); onChanged(); }
    catch (e) { setDel(false); setErr((e as Error).message); }
  };
  const kind = file ? editorKind(file.path) : { type: "text" as const, language: "" };
  return (
    <div className="files-split" data-testid="file-panel">
      <div className="files-tree">
        <div className="row" style={{ marginBottom: 4 }}><b className="small">Files</b><span className="grow" /><button className="chip small" onClick={() => setNewName("")}>New file</button></div>
        <ul role="tree" aria-label="App files" style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {tree.map((n) => <Row key={n.path} n={n} depth={0} open={file?.path ?? ""} sel={file?.path ?? ""} onPick={pick} closed={closed} toggle={(p) => setClosed((c) => { const x = new Set(c); if (x.has(p)) x.delete(p); else x.add(p); return x; })} />)}
        </ul>
      </div>
      <div className="files-editor">
        {err && <div className="err small" role="alert">{err}</div>}
        {!file && !err && <div className="mut small" style={{ padding: 12 }}>Pick a file to read or edit it.</div>}
        {file && (
          <>
            <div className="row" style={{ marginBottom: 6, flexWrap: "wrap" }}>
              <b className="small" data-testid="open-file">{file.path}</b>{dirty && <span className="chip small" style={{ color: "var(--warn)" }}>unsaved</span>}<span className="grow" />
              {!file.binary && <button className="acc" disabled={!dirty} onClick={save}>Save</button>}
              {file.path !== "app.json" && <button onClick={() => setDel(true)} aria-label={`Delete ${file.path}`}>Delete file</button>}
            </div>
            {file.binary
              ? <div className="mut small" role="note">This is not a text file ({fmtSize(file.size)}), so it cannot be edited here. It stays in the app and is included when you export it.</div>
              : <CodeEditor value={text} onChange={setText} type={kind.type} language={kind.language} label={`Edit ${file.path}`} />}
          </>
        )}
      </div>
      {newName !== null && (
        <Modal title="New file" onClose={() => setNewName(null)}>
          <label>File path (for example public/about.html)<input autoFocus value={newName} aria-label="New file path" onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") create(); }} /></label>
          <div className="row" style={{ marginTop: 10 }}><button onClick={() => setNewName(null)}>Cancel</button><button className="acc" disabled={!newName.trim()} onClick={create}>Create</button></div>
        </Modal>
      )}
      {del && file && <ConfirmDelete what={file.path} onConfirm={remove} onClose={() => setDel(false)} />}
    </div>
  );
}
