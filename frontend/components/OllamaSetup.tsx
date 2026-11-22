"use client";
// Check Ollama, install it with one click (Windows), or start it if it is installed but stopped.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "./InfoTip";
import { toast } from "./Nav";

type Install = { state: string; step: string; done: number; total: number; log: string[]; error: string; version: string; running: boolean; needs_manual: boolean };
type Status = { running: boolean; version: string | null; installed: boolean; path: string | null; install: Install };
type Plan = {
  supported: boolean; installed: boolean; manual: { title: string; steps: string[]; url: string };
  release?: { version: string; size: number; source: string; verified: boolean; note: string }; size_gb?: number | null; needs_confirm?: boolean; steps?: string[];
};

const gb = (n: number) => (n / 1e9).toFixed(2);

export default function OllamaSetup({ onChange }: { onChange?: (s: Status) => void }) {
  const [st, setSt] = useState<Status | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [accept, setAccept] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api<Status>("/api/easy/ollama/status");
      setSt(s); onChange?.(s);
      if (!s.installed && !s.install.running) setPlan(await api<Plan>("/api/easy/ollama/plan"));
      return s;
    } catch (e) { setErr((e as Error).message); return null; }
  }, [onChange]);
  useEffect(() => { void load(); }, [load]);

  const running = st?.install.running ?? false;
  useEffect(() => {          // while an install runs, ask how far it is once a second
    if (!running) return;
    timer.current = setInterval(async () => {
      try {
        const i = await api<Install>("/api/easy/ollama/install/status");
        setSt((s) => ({ ...(s as Status), install: i }));
        if (!i.running) { clearInterval(timer.current!); void load(); }
      } catch { /* keep polling */ }
    }, 1000);
    return () => clearInterval(timer.current!);
  }, [running, load]);

  async function install() {
    setErr(""); setBusy(true);
    try { const i = await api<Install>("/api/easy/ollama/install", { method: "POST", body: JSON.stringify({ accept_unverified: accept }) }); setSt((s) => ({ ...(s as Status), install: i })); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }
  async function start() {
    setErr(""); setBusy(true);
    try { const r = await api<{ message: string }>("/api/easy/ollama/start", { method: "POST" }); toast(r.message); await load(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }
  async function cancel() { await api("/api/easy/ollama/install/cancel", { method: "POST" }).catch(() => {}); }

  if (!st) return <div className="small mut">{err || "Checking Ollama…"}</div>;
  const i = st.install;
  const pct = i.total ? Math.round((i.done / i.total) * 100) : null;
  return (
    <div className="ollama-setup">
      <div className="row"><b>Ollama</b><InfoTip id="ollama" />
        <span className={`pill ${st.running ? "ok" : st.installed ? "warn" : ""}`}>{st.running ? `running · ${st.version}` : st.installed ? "installed, not running" : "not installed"}</span></div>
      {st.running && <div className="small mut" style={{ marginTop: 6 }}>Ready. Models you download below run on this PC and cost nothing.</div>}
      {!st.running && st.installed && !running && (
        <div style={{ marginTop: 8 }}><div className="small mut">Ollama is installed but switched off.</div>
          <button className="acc" disabled={busy} onClick={start} style={{ marginTop: 8 }}>{busy ? "Starting…" : "Start Ollama"}</button></div>)}
      {!st.installed && !running && plan && (
        <div style={{ marginTop: 8 }}>
          {plan.supported ? (<>
            <div className="small">One click installs Ollama for your user account. No administrator rights needed.</div>
            {plan.release && <ul className="small mut" style={{ margin: "8px 0", paddingLeft: 18 }}>
              <li>Version {plan.release.version || "latest"}{plan.size_gb ? `, a ${plan.size_gb} GB download` : ""}</li>
              <li>{plan.release.verified ? `Checksum verified against the ${plan.release.source}.` : plan.release.note}</li>
              {(plan.steps ?? []).map((s) => <li key={s}>{s}</li>)}</ul>}
            {plan.needs_confirm && <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />I understand this download cannot be verified, install it anyway</label>}
            <button className="acc" disabled={busy || (!!plan.needs_confirm && !accept)} onClick={install} style={{ marginTop: 8 }}>{busy ? "Starting…" : "Install Ollama"}</button>
          </>) : (
            <div><b>{plan.manual.title}</b>
              <ol className="small" style={{ paddingLeft: 18 }}>{plan.manual.steps.map((s) => <li key={s}>{s}</li>)}</ol>
              <a href={plan.manual.url} target="_blank" rel="noreferrer">{plan.manual.url}</a></div>)}
        </div>)}
      {running && (
        <div style={{ marginTop: 10 }}>
          <div className="small">{i.step || "Working…"}{pct !== null && i.state === "downloading" ? ` · ${pct}% (${gb(i.done)} of ${gb(i.total)} GB)` : ""}</div>
          {pct !== null && i.state === "downloading" && <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${pct}%` }} /></div>}
          {i.state === "downloading" && <button className="ghost small" onClick={cancel} style={{ marginTop: 6 }}>Cancel</button>}
        </div>)}
      {!running && (i.state === "failed" || i.state === "needs_manual" || i.state === "cancelled") && (
        <div className={`small ${i.state === "cancelled" ? "mut" : "err"}`} style={{ marginTop: 8 }}>
          {i.state === "cancelled" ? "Cancelled. Nothing was installed." : i.error}
          {i.state !== "cancelled" && <div><button className="ghost small" onClick={() => void load()}>Check again</button></div>}
        </div>)}
      {i.log.length > 0 && <details style={{ marginTop: 8 }}><summary className="small mut">Details</summary><pre className="small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{i.log.join("\n")}</pre></details>}
      {err && <div className="small err" style={{ marginTop: 6 }}>{err}</div>}
    </div>
  );
}
