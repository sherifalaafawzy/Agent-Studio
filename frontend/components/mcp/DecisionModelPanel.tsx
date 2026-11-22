"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import { IBrain, ITrash } from "@/components/Icons";

type D1Step = { id: string; label: string; size_mb: number; status: "done" | "running" | "failed" | "pending" };
type D1Progress = { state: "not_installed" | "installing" | "ready" | "failed"; ready: boolean; error: string | null; step: string | null; steps: D1Step[];
  percent: number; progress_text: string; log_tail: string[] };
type D1Status = D1Progress & { repo: string; revision: string; home: string; versions: Record<string, any> | null; download_mb: number; disk_gb_needed: number;
  free_gb: number | null; hardware: { nvidia: boolean; gpu: string | null; vram_mb: number | null; driver: string | null; cuda: number[] | null; torch_build: string; variant: string };
  expect: string; license: { name: string; url: string; summary: string[] }; review_notes: string[] };
const D1_POLL_MS = 2000;
const gb = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);

export default function D1Panel({ onChange }: { onChange: () => void }) {
  const [s, setS] = useState<D1Status | null>(null);
  const [accept, setAccept] = useState(false);
  const [device, setDevice] = useState("auto");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<D1Status>("/api/d1/status").then(setS).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const installing = s?.state === "installing";
  useEffect(() => {
    if (!installing) return;
    const t = setInterval(async () => {
      try {
        const p = await api<D1Progress>("/api/d1/install/progress");
        if (p.state === "installing") setS((cur) => ({ ...(cur as D1Status), ...p }));
        else { await load(); onChange(); toast(p.state === "ready" ? "Decision model installed" : "Decision model install failed", p.state === "ready" ? "ok" : "err"); }
      } catch { /* backend briefly unreachable: keep polling */ }
    }, D1_POLL_MS);
    return () => clearInterval(t);
  }, [installing, load, onChange]);
  if (!s) return null;
  async function install() {
    setBusy(true);
    try { setS(await api<D1Status>("/api/d1/install", { method: "POST", body: JSON.stringify({ accept_license: accept, device }) })); toast("Installing the decision model…"); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
  }
  async function uninstall() {
    if (!confirm("Remove the decision model environment and its downloaded weights?")) return;
    try { await api("/api/d1/uninstall", { method: "POST" }); toast("Decision model removed"); await load(); onChange(); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  const tone = s.state === "ready" ? "ok" : s.state === "failed" ? "bad" : s.state === "installing" ? "warn" : "";
  const label = { not_installed: "not installed", installing: "installing…", ready: "ready", failed: "failed" }[s.state];
  const hw = s.hardware;
  return (
    <div className="card mcp-subcard" data-tour="d1-card">
      <div className="card-head"><IBrain size={18} /><b>Decision model</b><span className="pill">d1-2B · local</span><span className={`pill ${tone}`}>{label}</span><span className="grow" />
        <span className="small mut">Not a chat model: routing, triage, moderation, extraction and judging answered in one pass, on this machine.</span></div>
      <p className="small" style={{ margin: "8px 0" }}>{s.expect}</p>
      <div className="small mut">{hw.nvidia ? `GPU: ${hw.gpu} (CUDA ${hw.cuda ? hw.cuda.join(".") : "?"}, driver ${hw.driver})` : "No NVIDIA GPU detected"} · build: {hw.torch_build}
        {s.free_gb !== null ? ` · ${s.free_gb} GB free` : ""}</div>
      {s.state !== "ready" && (
        <details style={{ marginTop: 8 }} open={s.state === "not_installed"}>
          <summary className="small">License: {s.license.name} - read before installing</summary>
          <ul className="small" style={{ margin: "6px 0 0 18px" }}>{s.license.summary.map((t) => <li key={t}>{t}</li>)}</ul>
          <a className="small" href={s.license.url} target="_blank" rel="noreferrer">Full license text</a>
        </details>)}
      <details style={{ marginTop: 6 }}>
        <summary className="small">Security: this model ships code that runs on your machine</summary>
        <p className="small mut" style={{ margin: "6px 0" }}>It is loaded with trust_remote_code from the exact commit <code>{s.revision.slice(0, 12)}</code> that was reviewed (never &quot;main&quot;).</p>
        <ul className="small" style={{ margin: "0 0 0 18px" }}>{s.review_notes.map((t) => <li key={t}>{t}</li>)}</ul>
      </details>
      {s.state !== "ready" && (
        <ul className="small" style={{ listStyle: "none", margin: "10px 0 0", padding: 0 }}>
          {s.steps.map((st) => (
            <li key={st.id} data-step={st.id} data-status={st.status}>
              {{ done: "✓", running: "…", failed: "✗", pending: "○" }[st.status]} {st.label}{st.size_mb ? ` · ~${gb(st.size_mb)}` : ""}</li>))}
        </ul>)}
      {installing && (
        <div style={{ marginTop: 8 }}>
          <div style={{ height: 6, borderRadius: 3, background: "var(--line)" }} role="progressbar" aria-valuenow={s.percent} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ height: 6, borderRadius: 3, background: "var(--acc, #4f7fd9)", width: `${s.percent}%` }} /></div>
          <div className="small mut" style={{ marginTop: 4 }}>{s.percent}% · {s.progress_text}</div>
          {s.log_tail.length > 0 && <pre className="small err-raw" style={{ maxHeight: 120, overflow: "auto" }}>{s.log_tail.join("\n")}</pre>}
        </div>)}
      {s.state === "failed" && <div className="small err" style={{ marginTop: 8 }}>{s.error}</div>}
      {s.state === "ready" && <div className="small mut" style={{ marginTop: 8 }}>Installed in <code>{s.home}</code>
        {s.versions ? ` · torch ${s.versions.torch}, transformers ${s.versions.transformers}${s.versions.cuda ? `, CUDA GPU ${s.versions.gpu}` : ", CPU only"}` : ""}.
        Turn the Decision model server on with the switch at the top right, then give it to agents.</div>}
      <div className="row" style={{ marginTop: 10, justifyContent: "flex-end", gap: 10 }}>
        {(s.state === "not_installed" || s.state === "failed") && <>
          <span className="small mut" style={{ marginRight: "auto" }}>Download about {gb(s.download_mb)}; needs about {s.disk_gb_needed} GB of disk. Nothing downloads until you click Install.</span>
          <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />I accept the {s.license.name} terms</label>
          <select aria-label="Device" value={device} onChange={(e) => setDevice(e.target.value)}>
            <option value="auto">{hw.nvidia ? "GPU (CUDA) build" : "Automatic"}</option><option value="cpu">CPU only (smaller)</option></select>
          <button className="acc small" disabled={!accept || busy} onClick={install}>{s.state === "failed" ? "Retry install" : "Install"}</button></>}
        {installing && <span className="small mut">Installing… you can leave this page; it keeps running.</span>}
        {(s.state === "ready" || s.state === "failed") && <button className="ghost small" onClick={uninstall}><ITrash size={13} />Uninstall</button>}
      </div>
    </div>
  );
}
