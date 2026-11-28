"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { ITrash } from "@/components/Icons";
import BrandIcon from "@/components/BrandIcon";

type Fw = { id: string; label: string; blurb: string; licence: string; size_mb: number; packages: string[]; notes: string; state: "not_installed" | "installing" | "ready" | "failed"; version: string | null; error: string | null };
type TrainerStatus = { frameworks: Fw[]; install: { running: boolean; framework: string | null; log: string[]; error: string | null }; jobs_running: number };
export type Job = { id: string; state: string; mode: string; framework: string; task: string; target: string; name: string; epoch: number | null; epochs: number | null; progress: number | null; error: string | null; metrics: Record<string, unknown> | null };
type McpRow = { id: string; enabled: boolean; ready: boolean };

const POLL_MS = 2000;
const size = (mb: number) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`);
const STATE_TEXT: Record<string, string> = { not_installed: "Not installed", installing: "Installing…", ready: "Ready", failed: "Failed" };

/** The headline numbers of a finished job, in plain words. */
export function summarize(m: Record<string, unknown> | null): string {
  if (!m) return "";
  const num = (k: string) => (typeof m[k] === "number" ? (m[k] as number) : null);
  if (num("accuracy") !== null) return `accuracy ${num("accuracy")} (always guessing the most common answer: ${num("majority_class_baseline")}), macro F1 ${num("macro_f1")}`;
  if (num("mae") !== null) return `mean error ${num("mae")} (guessing the average: ${num("mean_baseline_mae")}), R² ${num("r2")}`;
  const first = Object.entries(m).find(([, v]) => typeof v === "number");
  return first ? `${first[0]} ${first[1]}` : "";
}

/** Install Ludwig / PyTorch / Keras one at a time (size and licence first) and watch training jobs. */
export default function TrainerPanel() {
  const [st, setSt] = useState<TrainerStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [mcp, setMcp] = useState<McpRow | null>(null);
  const [plan, setPlan] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [device, setDevice] = useState("auto");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, j, m] = await Promise.all([api<TrainerStatus>("/api/trainer/status"), api<Job[]>("/api/trainer/jobs"), api<McpRow[]>("/api/mcp")]);
      setSt(s); setJobs(j); setMcp(m.find((x) => x.id === "trainer") ?? null); setErr("");
      return s.install.running || s.jobs_running > 0;
    } catch (e) { setErr("Backend offline: " + (e as Error).message); return false; }
  }, []);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    let t: ReturnType<typeof setTimeout> | undefined;
    load().then((again) => { if (live && again) t = setTimeout(() => setTick((n) => n + 1), POLL_MS); });
    return () => { live = false; if (t) clearTimeout(t); };
  }, [load, tick]);
  const kick = () => setTick((n) => n + 1);

  async function install(fw: Fw) {
    setErr("");
    try { await api("/api/trainer/install", { method: "POST", body: JSON.stringify({ framework: fw.id, accepted, device }) }); setPlan(null); setAccepted(false); kick(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function uninstall(fw: Fw) {
    if (!window.confirm(`Remove ${fw.label} from this PC? Finished jobs stay.`)) return;
    try { await api("/api/trainer/uninstall", { method: "POST", body: JSON.stringify({ framework: fw.id }) }); kick(); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function cancel(j: Job) {
    try { await api(`/api/trainer/jobs/${j.id}/cancel`, { method: "POST" }); kick(); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function enable(on: boolean) {
    try { await api("/api/mcp/trainer", { method: "PUT", body: JSON.stringify({ enabled: on }) }); kick(); toast(on ? "Agents can use the trainer" : "Trainer switched off for agents"); } catch (e) { toast((e as Error).message, "err"); }
  }

  const ready = (st?.frameworks ?? []).some((f) => f.state === "ready");
  return (
    <section className="card" aria-labelledby="trainer-h">
      <div className="card-head"><h3 id="trainer-h" style={{ margin: 0 }}>Model trainer</h3><InfoTip id="trainer" /><span className="grow" />
        {mcp && <label style={{ flexDirection: "row", gap: 6, alignItems: "center" }}><input type="checkbox" checked={mcp.enabled} disabled={!ready} onChange={(e) => enable(e.target.checked)} />Let agents use it</label>}</div>
      <p className="small mut">Teach a model from a spreadsheet on this PC. Each framework lives in its own folder and is installed only when you click. Agents then use the “Model trainer” server to train, check and cancel jobs.</p>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="cat-grid">
        {(st?.frameworks ?? []).map((f) => (
          <div key={f.id} className="card cat-card" data-framework={f.id} style={{ boxShadow: "none" }}>
            <div className="card-head"><BrandIcon id={f.id} label={f.label} size={30} /><div className="cat-title"><b>{f.label}</b></div><span className={`pill ${f.state === "ready" ? "ok" : f.state === "failed" ? "bad" : ""}`}>{STATE_TEXT[f.state]}{f.version ? ` ${f.version}` : ""}</span></div>
            <p className="small">{f.blurb}</p>
            <div className="small mut">Download about {size(f.size_mb)} · {f.licence}</div>
            {f.error && <p className="err small">{f.error}</p>}
            {f.state === "installing" && st && <pre className="small log" aria-label="install log">{st.install.log.slice(-4).join("\n")}</pre>}
            <div className="row cat-actions">
              {f.state === "ready"
                ? <button className="ghost" onClick={() => uninstall(f)} aria-label={`Remove ${f.label}`}><ITrash size={14} /> Remove</button>
                : <button className="acc" disabled={f.state === "installing" || !!st?.install.running} onClick={() => { setPlan(plan === f.id ? null : f.id); setAccepted(false); }}>{f.state === "failed" ? "Try again" : "Install…"}</button>}
            </div>
            {plan === f.id && (
              <div className="cat-panel">
                <p className="small">{f.notes}</p>
                <p className="small mut">Installs: {f.packages.join(", ")}</p>
                {f.id === "pytorch" && <label>Build<select value={device} onChange={(e) => setDevice(e.target.value)}><option value="auto">Automatic (default for this system)</option><option value="cpu">CPU only (smaller download on Linux)</option></select></label>}
                <label style={{ flexDirection: "row", gap: 8, alignItems: "flex-start", marginTop: 8 }}>
                  <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
                  <span className="small">I accept the {f.licence} licence and a download of about {size(f.size_mb)}.</span></label>
                <div className="row" style={{ marginTop: 8, justifyContent: "flex-end" }}><button className="acc" disabled={!accepted} onClick={() => install(f)}>Install {f.label}</button></div>
              </div>)}
          </div>))}
      </div>
      <h4 style={{ margin: "16px 0 6px" }}>Training jobs</h4>
      {jobs.length === 0 ? <div className="small mut">No jobs yet. Ask an agent to train a model on a CSV in your workspace.</div> : (
        <table className="tbl"><thead><tr><th>Job</th><th>What</th><th>State</th><th>Result</th><th /></tr></thead><tbody>
          {jobs.map((j) => (
            <tr key={j.id} data-job={j.id}>
              <td><code>{j.id}</code></td>
              <td>{j.name || `${j.mode} · ${j.framework}`}<div className="small mut">{j.task} → {j.target}</div></td>
              <td><span className={`pill ${j.state === "done" ? "ok" : j.state === "failed" ? "bad" : ""}`}>{j.state}</span>{j.progress !== null && j.state === "running" && <div className="small mut">{j.epoch ?? 0}/{j.epochs ?? "?"} epochs</div>}</td>
              <td className="small">{j.error ?? summarize(j.metrics)}</td>
              <td>{(j.state === "running" || j.state === "starting") && <button className="ghost" onClick={() => cancel(j)}>Cancel</button>}</td>
            </tr>))}
        </tbody></table>)}
    </section>
  );
}
