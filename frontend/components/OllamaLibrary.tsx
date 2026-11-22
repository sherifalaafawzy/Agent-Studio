"use client";
// Browse, search, download and remove local (Ollama) models, with an estimate of whether each one suits this PC.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamSse } from "@/lib/api";
import { forgetModels } from "./ModelSelect";
import InfoTip from "./InfoTip";
import { toast } from "./Nav";

type Fit = { level: "great" | "ok" | "tight" | "too_big" | "unknown"; text: string };
type Tag = { tag: string; size_gb: number | null; fit: Fit; installed: boolean };
type Model = { name: string; label: string; best_for: string; caps: string[]; source: string; new: boolean; downloads: number | null; updated: string | null; installed: boolean; tags: Tag[] };
type Library = { models: Model[]; ram_gb: number | null; vram_gb: number | null };
type Have = { ok: boolean; message: string; models: { name: string; size: number; parameters: string | null; quantization: string | null }[] };

const FIT_LABEL: Record<Fit["level"], [string, string]> = { great: ["Fast on your PC", "ok"], ok: ["Slower (uses processor)", "ok"], tight: ["Tight fit", "warn"], too_big: ["Too big", "bad"], unknown: ["Size unknown", ""] };
const FILTERS = [["all", "All"], ["tools", "Can use tools"], ["thinking", "Thinks step by step"], ["vision", "Reads pictures"], ["embedding", "Search helpers"], ["fits", "Fits my PC"], ["installed", "Installed"]] as const;
type Filter = (typeof FILTERS)[number][0];

export const compactNumber = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));

export default function OllamaLibrary({ onInstalledChange, providerReady = true }: { onInstalledChange?: (names: string[]) => void; providerReady?: boolean }) {
  const [lib, setLib] = useState<Library | null>(null);
  const [have, setHave] = useState<Have | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [job, setJob] = useState<{ name: string; status?: string; percent?: number | null } | null>(null);       // the download in progress
  const [msg, setMsg] = useState("");
  const [finding, setFinding] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      const [l, h] = await Promise.all([api<Library>("/api/easy/ollama/library"), api<Have>("/api/easy/ollama/models")]);
      setLib(l); setHave(h); onInstalledChange?.(h.models.map((m) => m.name));
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }, [onInstalledChange]);
  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (lib?.models ?? []).filter((m) => {
      if (t && !`${m.name} ${m.label} ${m.best_for}`.toLowerCase().includes(t)) return false;
      if (filter === "installed") return m.installed;
      if (filter === "fits") return m.tags.some((x) => x.fit.level === "great" || x.fit.level === "ok");
      if (filter !== "all") return m.caps.includes(filter);
      return true;
    });
  }, [lib, q, filter]);

  async function pull(name: string) {
    setMsg(""); setJob({ name, status: "starting" });
    abort.current = new AbortController();
    let ok = false;
    try {
      await streamSse("/api/easy/ollama/pull", { model: name }, (e) => {
        if (e.type === "progress") setJob({ name, status: e.status, percent: e.percent });
        else if (e.type === "error") setMsg("⚠ " + e.message);
        else if (e.type === "done") ok = true;
      }, abort.current.signal);
    } catch (e) { if ((e as Error).name !== "AbortError") setMsg("⚠ " + (e as Error).message); else setMsg("Download stopped. Start it again to continue where it left off."); }
    setJob(null);
    if (ok) { forgetModels("ollama"); toast(`${name} is ready`); await use(name, true); }
    await load();
  }
  async function use(name: string, quiet = false) {
    try { await api("/api/easy/ollama/use", { method: "POST", body: JSON.stringify({ model: name }) }); if (!quiet) toast(`Using ${name}`); forgetModels("ollama"); setMsg(`${name} is now available in the model lists.`); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function remove(name: string) {
    if (!confirm(`Remove ${name} from this PC? You can download it again later.`)) return;
    try { await api(`/api/easy/ollama/models?name=${encodeURIComponent(name)}`, { method: "DELETE" }); forgetModels("ollama"); toast(`Removed ${name}`); await load(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function find() {
    setFinding(true); setMsg("");
    try { const r = await api<{ ok: boolean; message: string }>("/api/easy/ollama/library/refresh", { method: "POST", body: JSON.stringify({ query: q.trim(), force: true }) }); setMsg(r.message); await load(); }
    catch (e) { setMsg("⚠ " + (e as Error).message); }
    setFinding(false);
  }
  async function sizes(name: string) {
    const r = await api<{ ok: boolean; message: string }>(`/api/easy/ollama/library/tags?model=${encodeURIComponent(name)}`).catch((e: Error) => ({ ok: false, message: e.message }));
    if (!r.ok) setMsg(r.message);
    await load();
  }

  if (!lib) return <div className="small mut">{msg || "Loading the model library…"}</div>;
  return (
    <div className="olib">
      {have && <div className="olib-have">
        <div className="row"><b>On this PC</b>{!have.ok && <span className="small err">{have.message}</span>}</div>
        {have.ok && have.models.length === 0 && <div className="small mut">No models yet. Pick one below.</div>}
        {have.models.map((m) => (
          <div key={m.name} className="olib-row"><span className="t"><b>{m.name}</b> <span className="small mut">{(m.size / 1e9).toFixed(1)} GB{m.parameters ? ` · ${m.parameters}` : ""}</span></span>
            {providerReady && <button className="ghost small" onClick={() => use(m.name)}>Use</button>}
            <button className="ghost small" aria-label={`Remove ${m.name}`} onClick={() => remove(m.name)}>Remove</button></div>))}
      </div>}

      <div className="row" style={{ margin: "12px 0 8px" }}>
        <input value={q} placeholder="Search models, e.g. coding, vision, llama" aria-label="Search models" onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
        <button onClick={find} disabled={finding}>{finding ? "Looking…" : "Find new models"}</button>
      </div>
      <div className="row small" style={{ gap: 6 }}>
        {FILTERS.map(([k, label]) => <button key={k} className={`chip ${filter === k ? "on" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>)}
        <InfoTip id="capabilities" />
      </div>
      <div className="small mut" style={{ margin: "6px 0" }}>
        Your PC: {lib.ram_gb ? `${lib.ram_gb} GB memory` : "memory unknown"}{lib.vram_gb ? `, ${lib.vram_gb} GB graphics memory` : ", no graphics card found"}. <InfoTip id="model-fit" />
      </div>
      {job && <div className="olib-prog">
        <div className="small">Downloading <b>{job.name}</b>: {job.status}{job.percent != null ? ` · ${job.percent}%` : ""}</div>
        {job.percent != null && <div className="bar" style={{ marginTop: 4 }}><i style={{ width: `${job.percent}%` }} /></div>}
        <button className="ghost small" onClick={() => abort.current?.abort()} style={{ marginTop: 4 }}>Stop</button></div>}
      {msg && <div className="small" style={{ margin: "6px 0" }}>{msg}</div>}

      {shown.length === 0 && <div className="small mut">Nothing matches. Try “Find new models” to look on ollama.com.</div>}
      <div className="olib-list">
        {shown.map((m) => (
          <div key={m.name} className="olib-card">
            <div className="row" style={{ gap: 6 }}><b>{m.label}</b>{m.new && <span className="pill acc">new</span>}
              {m.installed && <span className="pill ok">installed</span>}
              {m.caps.map((c) => <span key={c} className="pill">{c}</span>)}
              {m.downloads ? <span className="small mut" title="downloads from ollama.com">{compactNumber(m.downloads)} downloads</span> : null}</div>
            <div className="small mut">{m.best_for || "No description."}{m.source === "ollama.com" ? " (from ollama.com)" : ""}</div>
            {m.tags.map((t) => {
              const [label, tone] = FIT_LABEL[t.fit.level];
              const id = `${m.name}:${t.tag}`;
              return (
                <div key={t.tag} className="olib-tag">
                  <span className="t"><code>{t.tag}</code>{t.size_gb ? <span className="small mut"> · {t.size_gb} GB</span> : null}</span>
                  <span className={`pill ${tone}`} title={t.fit.text}>{label}</span>
                  {t.installed ? <span className="small mut">Installed ✓</span>
                    : <button className="small" disabled={!!job} onClick={() => pull(id)} aria-label={`Download ${id}`}>Download</button>}
                </div>);
            })}
            {m.source === "ollama.com" && m.tags.every((t) => t.size_gb === null) && <button className="ghost small" onClick={() => sizes(m.name)}>Show download sizes</button>}
          </div>))}
      </div>
    </div>
  );
}
