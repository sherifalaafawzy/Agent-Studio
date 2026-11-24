"use client";
// One-click install of the local voice engines (speech recognition + voice), with the licences, sizes and live progress.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LocalStatus } from "@/lib/live";

export default function LiveInstall({ local, onChange }: { local: LocalStatus; onChange: () => void }) {
  const [ok, setOk] = useState(false);
  const [cpu, setCpu] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const installing = local.state === "installing";
  useEffect(() => {
    if (!installing) return;
    const t = setInterval(onChange, 2000);
    return () => clearInterval(t);
  }, [installing, onChange]);
  const go = async () => {
    setBusy(true); setErr("");
    try { await api("/api/live/install", { method: "POST", body: JSON.stringify({ accept_licenses: ok, device: cpu ? "cpu" : "auto" }) }); onChange(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  return (
    <div className="card" data-testid="live-install">
      <div className="card-head"><b>Local voice</b><span className={`pill ${local.state === "failed" ? "bad" : ""}`}>{local.state === "installing" ? `installing ${local.percent}%` : local.state === "failed" ? "install failed" : "not installed"}</span></div>
      <div className="card-desc">Live mode listens and speaks on <b>this PC</b>: nothing you say is sent anywhere. It needs two small engines, installed into their own private folder:
        speech recognition (Whisper {local.stt_model}) and a voice (Kokoro). About <b>{Math.round(local.download_mb / 100) / 10} GB</b> to download{local.free_gb != null ? `, ${local.free_gb} GB free` : ""}.
        {local.hardware?.nvidia ? <> Your {local.hardware.gpu} will be used ({local.device === "cuda" ? "GPU" : "CPU"} mode).</> : <> No NVIDIA GPU was found, so the CPU is used.</>}</div>
      <p className="small mut">{local.expect}</p>
      <ul className="small" style={{ margin: "8px 0", paddingLeft: 18 }}>
        {local.steps.map((s) => <li key={s.id} className={s.status === "failed" ? "err" : s.status === "done" ? "ok" : ""}>{s.status === "done" ? "✓ " : s.status === "running" ? "… " : s.status === "failed" ? "✕ " : ""}{s.label}</li>)}
      </ul>
      {installing && <><div className="bar" style={{ margin: "6px 0" }}><i style={{ width: `${local.percent}%` }} /></div><div className="small mut" data-testid="live-install-text">{local.progress_text}</div></>}
      {local.error && <div className="err small" role="alert">{local.error}</div>}
      {err && <div className="err small" role="alert">{err}</div>}
      {!installing && <>
        <details className="small" style={{ margin: "8px 0" }}><summary>Licences</summary>
          <ul>{local.licences.map((l) => <li key={l.name}>{l.name}: {l.licence} · <a href={l.url} target="_blank" rel="noreferrer">source</a></li>)}</ul></details>
        <label className="row small"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} aria-label="Accept the licences" /> I accept these licences</label>
        {local.hardware?.nvidia && <label className="row small"><input type="checkbox" checked={cpu} onChange={(e) => setCpu(e.target.checked)} aria-label="Use the CPU only" /> Use the CPU only (smaller download, slower)</label>}
        <div className="row" style={{ marginTop: 8 }}><button className="acc" disabled={!ok || busy} onClick={go}>{local.state === "failed" ? "Try again" : "Install"}</button></div>
      </>}
    </div>
  );
}
