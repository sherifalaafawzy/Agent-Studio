"use client";
// The smaller panels of an app: the changes waiting for review, the versions, and exporting / hosting it elsewhere.
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import { lineDiff } from "@/lib/canvas";
import { ago, authorText, FileDiff, Plan, Proposal, VersionRow } from "@/lib/apps";
import { Modal } from "@/components/Modal";
import { toast } from "@/components/Nav";

const MAX_LINES = 60;

export function Diff({ before, after }: { before: string | null; after: string | null }) {
  const lines = lineDiff(before ?? "", after ?? "").filter((l, i, a) => l.kind !== "same" || a.slice(Math.max(0, i - 2), i + 3).some((x) => x.kind !== "same"));
  const shown = lines.slice(0, MAX_LINES);
  return (
    <pre className="apps-diff" aria-label="Changes in this file">
      {shown.map((l, i) => <div key={i} className={`diff-${l.kind}`}>{l.kind === "add" ? "+ " : l.kind === "del" ? "- " : "  "}{l.text}</div>)}
      {lines.length > MAX_LINES && <div className="mut">… {lines.length - MAX_LINES} more lines</div>}
    </pre>
  );
}

// ---------------------------------------------------------------- changes waiting for review
export function ChangesPanel({ appId, onChanged, refreshKey }: { appId: string; onChanged: () => void; refreshKey: number }) {
  const [list, setList] = useState<Proposal[] | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Proposal[]>(`/api/apps/${appId}/proposals`).then((p) => { setList(p); setErr(""); }).catch((e) => setErr(e.message)), [appId]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const act = async (path: string, ok: string) => {
    let message = "";
    try {
      const r = await api<any>(`/api/apps/${appId}/${path}`, { method: "POST", body: "{}" });
      if (r?.stale?.length) message = `${r.stale.length} change(s) no longer fit: ${r.stale.map((s: any) => s.reason).join("; ")}`;
      else toast(ok);
      onChanged();
    } catch (e) { message = (e as Error).message; }
    await load();
    setErr(message);                                                // after the reload, which would otherwise clear it
  };
  if (list === null) return <div className="mut small">{err ? <span role="alert" className="err">{err}</span> : "Loading changes…"}</div>;
  return (
    <div data-testid="changes-panel">
      {err && <div className="err small" role="alert">{err}</div>}
      {list.length === 0 && <div className="mut small">No changes are waiting. When the builder suggests changes to a file, they show here as a diff for you to accept or reject.</div>}
      {list.length > 1 && <div className="row" style={{ gap: 6, marginBottom: 6 }}><button className="acc" onClick={() => act("accept-all", "Changes accepted")}>Accept all</button><button onClick={() => act("reject-all", "Changes rejected")}>Reject all</button></div>}
      {list.map((p) => (
        <div key={p.id} className="card" style={{ marginBottom: 8 }}>
          <div className="row"><b className="small">{authorText(p.author)}</b><span className="grow" /><span className="small mut">{ago(p.created)}</span></div>
          {p.note && <div className="small">{p.note}</div>}
          {p.changes.map((c, i) => (
            <div key={i} style={{ marginTop: 6 }}>
              <div className="small"><span className="chip small">{c.op === "write" ? (c.old === null ? "new file" : "replace") : c.op === "patch" ? "edit" : "delete"}</span> {c.path}</div>
              {!c.fits && <div className="small" role="note" style={{ color: "var(--warn-text)" }}>This no longer fits the file as it is now.</div>}
              {c.op !== "delete" ? <Diff before={c.old} after={c.new} /> : <div className="small mut">The file will be removed.</div>}
            </div>
          ))}
          <div className="row" style={{ marginTop: 8, gap: 6 }}>
            <button className="acc" aria-label={`Accept: ${p.note || p.changes[0]?.path}`} onClick={() => act(`proposals/${p.id}/accept`, "Change accepted")}>Accept</button>
            <button aria-label={`Reject: ${p.note || p.changes[0]?.path}`} onClick={() => act(`proposals/${p.id}/reject`, "Change rejected")}>Reject</button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- versions
export function VersionsPanel({ appId, onChanged, refreshKey }: { appId: string; onChanged: () => void; refreshKey: number }) {
  const [list, setList] = useState<VersionRow[] | null>(null);
  const [diff, setDiff] = useState<{ n: number; to?: number; files: FileDiff[] } | null>(null);
  const [pair, setPair] = useState<number[]>([]);
  const [restore, setRestore] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api<VersionRow[]>(`/api/apps/${appId}/versions`).then((v) => { setList(v); setErr(""); }).catch((e) => setErr(e.message)), [appId]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const show = async (n: number) => { try { setDiff({ n, files: await api<FileDiff[]>(`/api/apps/${appId}/versions/${n}/diff`) }); } catch (e) { setErr((e as Error).message); } };
  const undo = async () => { try { await api(`/api/apps/${appId}/undo`, { method: "POST" }); toast("Went back one change"); setDiff(null); setPair([]); onChanged(); await load(); } catch (e) { setErr((e as Error).message); } };
  const togglePair = (n: number, on: boolean) => setPair(on ? [...pair.filter((x) => x !== n), n].slice(-2) : pair.filter((x) => x !== n));
  const comparePair = async () => {
    const [a, b] = [...pair].sort((x, y) => x - y);
    try { setDiff({ n: a, to: b, files: await api<FileDiff[]>(`/api/apps/${appId}/versions/compare?a=${a}&b=${b}`) }); } catch (e) { setErr((e as Error).message); }
  };
  const doRestore = async () => {
    const n = restore!;
    try { await api(`/api/apps/${appId}/restore`, { method: "POST", body: JSON.stringify({ version: n }) }); setRestore(null); setDiff(null); toast(`Version ${n} brought back`); onChanged(); await load(); }
    catch (e) { setRestore(null); setErr((e as Error).message); }
  };
  const keep = async () => { try { await api(`/api/apps/${appId}/versions`, { method: "POST", body: JSON.stringify({ note: note.trim() || "saved on purpose" }) }); setNote(""); toast("Version saved"); await load(); } catch (e) { setErr((e as Error).message); } };
  return (
    <div data-testid="versions-panel">
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ gap: 6 }}>
        <input className="grow" value={note} aria-label="Version note" placeholder="Note for this version (optional)" onChange={(e) => setNote(e.target.value)} />
        <button onClick={keep}>Save a version</button>
        <button onClick={undo} disabled={(list?.length ?? 0) < 2} title="Go back one change. Press it again to go forward again.">Undo the last change</button>
      </div>
      {list === null && !err && <div className="mut small">Loading versions…</div>}
      <ul style={{ listStyle: "none", padding: 0, margin: "8px 0" }}>
        {(list ?? []).map((v, i) => (
          <li key={v.version} className="row small" style={{ gap: 8, padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
            <input type="checkbox" checked={pair.includes(v.version)} onChange={(e) => togglePair(v.version, e.target.checked)} aria-label={`Pick version ${v.version} to compare`} />
            <b>v{v.version}</b><span className="grow">{authorText(v.author)}{v.note ? `: ${v.note}` : ""} <span className="mut">· {ago(v.created)}</span></span>
            {i > 0 && <><button onClick={() => show(v.version)} aria-label={`Compare version ${v.version}`}>Compare</button><button onClick={() => setRestore(v.version)} aria-label={`Restore version ${v.version}`}>Restore</button></>}
            {i === 0 && <span className="chip small">now</span>}
          </li>
        ))}
      </ul>
      {pair.length === 2 && <div style={{ marginBottom: 6 }}><button onClick={comparePair}>Compare the two picked versions</button></div>}
      {diff && (
        <div data-testid="version-diff">
          <b className="small">Version {diff.n} compared with {diff.to ? `version ${diff.to}` : "now"}</b>
          {diff.files.length === 0 && <div className="mut small">No differences.</div>}
          {diff.files.map((f) => <div key={f.path} style={{ marginTop: 6 }}><div className="small"><span className="chip small">{f.status === "added" ? "added since" : f.status === "removed" ? "removed since" : "changed"}</span> {f.path}</div>{(f.old !== null || f.new !== null) && <Diff before={f.old} after={f.new} />}</div>)}
        </div>
      )}
      {restore !== null && (
        <Modal title={`Bring back version ${restore}?`} onClose={() => setRestore(null)}>
          <p className="small">The app&apos;s files go back to how they were in version {restore}. What you have now is saved as a version first, so you can undo this.</p>
          <div className="row"><button onClick={() => setRestore(null)}>Cancel</button><button className="acc" onClick={doRestore}>Restore</button></div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- export and hosting
export function ExportPanel({ appId, slug }: { appId: string; slug: string }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Plan>(`/api/apps/${appId}/deploy-plan`).then(setPlan).catch((e) => setErr(e.message)); }, [appId]);
  const download = async () => {
    setBusy(true); setErr("");
    try {
      const res = await fetch(`${API_BASE}/api/apps/${appId}/export.zip`);
      if (!res.ok) { const b = await res.json().catch(() => null); throw new Error(b?.detail ?? `HTTP ${res.status}`); }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url; a.download = `${slug}.zip`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  return (
    <div data-testid="export-panel">
      <div className="row"><button className="acc" disabled={busy} onClick={download}>Download as a zip</button><span className="small mut">Without secrets, .env files or anything .gitignore excludes. Refused if a file looks like it holds a secret.</span></div>
      {err && <div className="err small" role="alert">{err}</div>}
      {plan && (
        <div style={{ marginTop: 10 }}>
          <b className="small">To host it somewhere else</b>
          <ol className="small" style={{ paddingLeft: 18 }}>
            {plan.steps.map((s) => <li key={s.title}>{s.title}: <code>{s.command}</code> <span className="mut">({s.where})</span></li>)}
          </ol>
          {plan.notes.map((n, i) => <div key={i} className="small mut" role="note" style={{ marginTop: 4 }}>{n}</div>)}
        </div>
      )}
    </div>
  );
}
