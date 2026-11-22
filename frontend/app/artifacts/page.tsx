"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { Artifact, artifactUrl, agoText, COPY_WORD, copyState, Facets, fmtBytes, SOURCE_LABEL, TYPE_LABEL } from "@/lib/artifacts";
import ArtifactSettings from "@/components/ArtifactSettings";
import Markdown from "@/components/Markdown";
import { CsvTable } from "@/components/RichBlocks";
import HtmlPreview from "@/components/rich/HtmlPreview";
import JsonViewer from "@/components/rich/JsonViewer";
import MediaBlock from "@/components/rich/MediaBlock";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { IDownload, IFile, ISearch, IX } from "@/components/Icons";

type List = { total: number; items: Artifact[] };
type Preview = { kind: string; text: string; truncated: boolean };
const PAGE = 60;
const SORTS: [string, string, string][] = [["created", "desc", "Newest first"], ["created", "asc", "Oldest first"], ["name", "asc", "Name A to Z"], ["size", "desc", "Largest first"]];
const WHEN: [string, string][] = [["", "Any time"], ["1", "Last 24 hours"], ["7", "Last 7 days"], ["30", "Last 30 days"]];
const SIZES: [string, string][] = [["", "Any size"], ["small", "Under 100 KB"], ["medium", "100 KB to 5 MB"], ["large", "Over 5 MB"]];

function Detail({ a, onClose, onChanged }: { a: Artifact; onClose: () => void; onChanged: (a: Artifact | null) => void }) {
  const [pv, setPv] = useState<Preview | null>(null);
  const [pvErr, setPvErr] = useState("");
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    setPv(null); setPvErr(""); setConfirming(false);
    if (["markdown", "text", "csv", "json"].includes(a.preview)) api<Preview>(`/api/artifacts/${a.id}/preview`).then(setPv).catch((e) => setPvErr(e.message));
  }, [a.id, a.preview]);
  const copyLink = () => { navigator.clipboard?.writeText(artifactUrl(a.id)).then(() => toast("Link copied")); };
  const resync = async () => { try { onChanged(await api<Artifact>(`/api/artifacts/${a.id}/resync`, { method: "POST" })); toast("Copies updated"); } catch (e) { toast((e as Error).message, "err"); } };
  const del = async () => {
    try { const r = await api<{ storage_errors: { storage: string; error: string }[] }>(`/api/artifacts/${a.id}`, { method: "DELETE" });
      toast(r.storage_errors.length ? `Deleted, but ${r.storage_errors.length} storage could not remove its copy` : "Deleted", r.storage_errors.length ? "warn" : "ok"); onChanged(null); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  return (
    <aside className="art-detail" aria-label={`Details of ${a.name}`}>
      <div className="art-detail-head"><IFile size={18} /><b className="grow" style={{ wordBreak: "break-word" }}>{a.name}</b><button className="icon-btn" aria-label="Close details" onClick={onClose}><IX size={16} /></button></div>
      <div className="art-detail-body">
        <div className="art-preview" data-testid="preview">
          {a.preview === "image" && <img src={artifactUrl(a.id)} alt={a.name} />}
          {a.preview === "pdf" && <iframe src={artifactUrl(a.id)} title={`Preview of ${a.name}`} />}
          {pvErr && <div className="err" role="alert">{pvErr}</div>}
          {pv?.kind === "markdown" && <Markdown text={pv.text} />}
          {pv?.kind === "csv" && <CsvTable plain text={pv.text} sep={a.name.toLowerCase().endsWith(".tsv") ? "\t" : ","} />}
          {pv?.kind === "json" && (() => { if (!pv.truncated) { try { return <JsonViewer plain value={JSON.parse(pv.text)} title={a.name} />; } catch { /* not valid JSON: shown as text */ } } return <pre>{pv.text}</pre>; })()}
          {pv?.kind === "text" && (a.mime === "text/html" && !pv.truncated ? <HtmlPreview plain html={pv.text} title={a.name} /> : <pre>{pv.text}</pre>)}
          {(a.type === "audio" || a.type === "video") && <MediaBlock plain kind={a.type} artifact={a.id} title={a.name} />}
          {pv?.truncated && <div className="small mut">Showing the start of the file. Download it to see everything.</div>}
          {a.preview === "none" && a.type !== "audio" && a.type !== "video" && <div className="small mut">No preview for this kind of file. Download it to open it.</div>}
        </div>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <a className="pri btn" href={artifactUrl(a.id, true)} download={a.name}><IDownload size={14} /> Download</a>
          <button onClick={copyLink}>Copy link</button>
          {a.session_id && <Link className="btn" href={`/?s=${a.session_id}`}>Open the chat</Link>}
          {a.session_id && <Link className="btn" href={`/runs/?s=${a.session_id}`}>Open the run</Link>}
        </div>
        <div className="kv small">
          <span className="mut">Kind</span><span>{TYPE_LABEL[a.type] ?? a.type} · {a.mime}</span>
          <span className="mut">Size</span><span>{fmtBytes(a.size)}</span>
          <span className="mut">Made</span><span>{new Date(a.created * 1000).toLocaleString()} ({agoText(a.created)}) · version {a.version}</span>
          <span className="mut">By</span><span>{a.agent || "unknown"} · {SOURCE_LABEL[a.source] ?? a.source}</span>
          {a.path && <><span className="mut">In the workspace</span><span style={{ wordBreak: "break-all" }}>{a.path}</span></>}
          <span className="mut">Fingerprint</span><span title={a.sha256}>{a.sha256.slice(0, 16)}…</span>
          {a.tags.length > 0 && <><span className="mut">Tags</span><span>{a.tags.join(", ")}</span></>}
        </div>
        {a.secrets && <div className="warn-box small" role="note">This file contains something that looks like a secret. Copies sent to cloud storage are {`checked first (see Storage and rules)`}.</div>}
        <div><div className="lbl">Where it is kept</div>
          <div className="copies">{a.copies.map((c) => (
            <div key={c.storage} className="copy small" data-testid="copy"><b>{c.storage}</b><span className={`pill ${c.status === "synced" ? "ok" : c.status === "pending" ? "warn" : "bad"}`}>{COPY_WORD[c.status] ?? c.status}</span>
              {c.detail && <span className="mut">{c.detail}</span>}</div>))}</div>
          <button className="small" style={{ marginTop: 6 }} onClick={resync}>Retry or update copies</button></div>
        {confirming ? (
          <div className="confirm-box" role="alertdialog" aria-label="Confirm delete">
            <b>Delete “{a.name}”?</b><div className="small">It is removed from every storage that holds a copy. This cannot be undone.</div>
            <div className="row" style={{ marginTop: 8 }}><button className="danger" onClick={del}>Delete for good</button><button onClick={() => setConfirming(false)}>Keep it</button></div>
          </div>
        ) : <button className="ghost" onClick={() => setConfirming(true)}>Delete…</button>}
      </div>
    </aside>
  );
}

function ArtifactsPage() {
  const sp = useSearchParams(), router = useRouter();
  const [tab, setTab] = useState<"files" | "storage">("files");
  const [facets, setFacets] = useState<Facets | null>(null);
  const [list, setList] = useState<List | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState(""), [dq, setDq] = useState("");
  const [type, setType] = useState(""), [agent, setAgent] = useState(""), [storage, setStorage] = useState(""), [source, setSource] = useState("");
  const [when, setWhen] = useState(""), [size, setSize] = useState(""), [sortI, setSortI] = useState(0);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [limit, setLimit] = useState(PAGE);
  const [open, setOpen] = useState<Artifact | null>(null);
  const idParam = sp.get("id");
  const opened = useRef<string | null>(null);

  useEffect(() => { const t = setTimeout(() => { setDq(q); setLimit(PAGE); }, 200); return () => clearTimeout(t); }, [q]);
  const load = useCallback(() => {
    const [sort, order] = SORTS[sortI];
    const p = new URLSearchParams({ sort, order, limit: String(limit) });
    if (dq) p.set("q", dq);
    if (type) p.set("type", type);
    if (agent) p.set("agent", agent);
    if (storage) p.set("storage", storage);
    if (source) p.set("source", source);
    if (when) p.set("since", String(Date.now() / 1000 - Number(when) * 86400));
    if (size === "small") p.set("max_size", String(100 * 1024));
    if (size === "medium") { p.set("min_size", String(100 * 1024)); p.set("max_size", String(5 * 1024 * 1024)); }
    if (size === "large") p.set("min_size", String(5 * 1024 * 1024));
    return Promise.all([api<List>(`/api/artifacts?${p}`), api<Facets>("/api/artifacts/facets")]).then(([l, f]) => { setList(l); setFacets(f); setErr(""); }).catch((e) => setErr(e.message));
  }, [dq, type, agent, storage, source, when, size, sortI, limit]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {                                                   // /artifacts/?id=... opens that file (the chat's file cards link here)
    if (idParam && idParam !== opened.current) { opened.current = idParam; api<Artifact>(`/api/artifacts/${idParam}`).then(setOpen).catch(() => toast("That artifact no longer exists", "warn")); }
  }, [idParam]);

  const pick = (a: Artifact | null) => { setOpen(a); opened.current = a?.id ?? null; router.replace(a ? `/artifacts/?id=${a.id}` : "/artifacts/", { scroll: false }); };
  const changed = (a: Artifact | null) => { if (a) setOpen(a); else pick(null); void load(); };
  const items = list?.items ?? [];

  return (
    <div>
      <div className="page-head"><h2>Artifacts</h2><InfoTip id="artifacts" /></div>
      <p className="page-sub">Files and results your agents produced. Search them, preview them, download them, and choose where copies are kept.</p>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "files"} className={`tab ${tab === "files" ? "on" : ""}`} onClick={() => setTab("files")}>Files{facets ? ` (${facets.total})` : ""}</button>
        <button role="tab" aria-selected={tab === "storage"} className={`tab ${tab === "storage" ? "on" : ""}`} onClick={() => setTab("storage")}>Storage and rules</button>
      </div>
      {tab === "storage" ? <ArtifactSettings /> : (<>
        {err && <p className="err" role="alert">{err}</p>}
        <div className="art-bar">
          <label className="grow" style={{ position: "relative", minWidth: 200 }}><span className="sr-only">Search artifacts</span>
            <input type="search" value={q} placeholder="Search names, tags and what is inside…" aria-label="Search artifacts" onChange={(e) => setQ(e.target.value)} /></label>
          <select aria-label="Sort" value={sortI} onChange={(e) => { setSortI(Number(e.target.value)); setLimit(PAGE); }}>{SORTS.map((s, i) => <option key={i} value={i}>{s[2]}</option>)}</select>
          <div className="seg" role="group" aria-label="View"><button className={view === "grid" ? "on" : ""} aria-pressed={view === "grid"} onClick={() => setView("grid")}>Grid</button>
            <button className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => setView("list")}>List</button></div>
        </div>
        <div className="cat-chips" aria-label="Kind">
          <button className={`chip ${type === "" ? "on" : ""}`} onClick={() => { setType(""); setLimit(PAGE); }}>All kinds</button>
          {facets?.types.map((t) => <button key={t.value} className={`chip ${type === t.value ? "on" : ""}`} aria-pressed={type === t.value} onClick={() => { setType(type === t.value ? "" : t.value); setLimit(PAGE); }}>{TYPE_LABEL[t.value] ?? t.value} · {t.count}</button>)}
        </div>
        <div className="art-filters">
          <select aria-label="Agent" value={agent} onChange={(e) => { setAgent(e.target.value); setLimit(PAGE); }}><option value="">Any agent</option>{facets?.agents.map((a) => <option key={a.value} value={a.value}>{a.value} ({a.count})</option>)}</select>
          <select aria-label="Storage" value={storage} onChange={(e) => { setStorage(e.target.value); setLimit(PAGE); }}><option value="">Any storage</option>{facets?.storages.map((a) => <option key={a.value} value={a.value}>{a.value} ({a.count})</option>)}</select>
          <select aria-label="Made by" value={source} onChange={(e) => { setSource(e.target.value); setLimit(PAGE); }}><option value="">Any origin</option>{facets?.sources.map((a) => <option key={a.value} value={a.value}>{SOURCE_LABEL[a.value] ?? a.value} ({a.count})</option>)}</select>
          <select aria-label="When" value={when} onChange={(e) => { setWhen(e.target.value); setLimit(PAGE); }}>{WHEN.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <select aria-label="Size" value={size} onChange={(e) => { setSize(e.target.value); setLimit(PAGE); }}>{SIZES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          {list && <span className="small mut">{list.total} file{list.total === 1 ? "" : "s"}{facets ? ` · ${fmtBytes(facets.bytes)} in all` : ""}</span>}
        </div>
        {list && items.length === 0 && (
          <div className="card" data-testid="empty"><b>{facets?.total ? "Nothing matches these filters" : "No artifacts yet"}</b>
            <p className="mut small">{facets?.total ? "Try fewer filters or a different search." : "When an agent writes a report, a chart, a data file or a model, it shows up here. Ask an agent to “write a short report and keep it as an artifact”, or open Storage and rules to see what is collected."}</p></div>)}
        {view === "grid" ? (
          <div className="art-grid">{items.map((a) => { const st = copyState(a); return (
            <button key={a.id} className="card art-tile" aria-pressed={open?.id === a.id} onClick={() => pick(a)} data-testid="tile">
              {a.preview === "image" && <img className="art-thumb" src={artifactUrl(a.id)} alt="" loading="lazy" />}
              <span className="row"><IFile size={16} /><b className="art-name">{a.name}</b></span>
              <span className="small mut">{TYPE_LABEL[a.type] ?? a.type} · {fmtBytes(a.size)} · {agoText(a.created)}</span>
              {a.summary && a.preview !== "image" && <span className="small mut" style={{ overflow: "hidden", maxHeight: 40 }}>{a.summary}</span>}
              <span className="row small"><span className={`pill ${st.tone}`}>{st.text}</span>{a.agent && <span className="pill">{a.agent}</span>}</span>
            </button>); })}</div>
        ) : (
          <div className="art-rows card" style={{ padding: 0 }}>{items.map((a) => { const st = copyState(a); return (
            <div key={a.id} role="button" tabIndex={0} className="art-row" aria-pressed={open?.id === a.id} data-testid="row" onClick={() => pick(a)} onKeyDown={(e) => { if (e.key === "Enter") pick(a); }}>
              <span className="row"><IFile size={16} /><b className="art-name">{a.name}</b></span><span className="small mut">{fmtBytes(a.size)}</span>
              <span className="small mut hide-sm">{TYPE_LABEL[a.type] ?? a.type}</span><span className="small mut hide-sm">{agoText(a.created)}</span><span className={`pill ${st.tone} hide-sm`}>{st.text}</span>
            </div>); })}</div>
        )}
        {list && list.total > items.length && <div style={{ textAlign: "center", margin: "14px 0" }}><button onClick={() => setLimit(limit + PAGE)}>Show more ({list.total - items.length} left)</button></div>}
      </>)}
      {open && <Detail a={open} onClose={() => pick(null)} onChanged={changed} />}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={null}><ArtifactsPage /></Suspense>;
}
