"use client";
// Settings > Agents & sandbox: apps (small web apps agents build with you). Whether they are on, whether the builder's changes apply at once or wait for your review,
// whether server apps may run in a container, the preview port, size limits, versions kept, each app key's rate limit and your name and email for git commits.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Cfg = {
  enabled: boolean; auto_apply: boolean; allow_server_apps: boolean; preview_port: number; max_apps: number; max_files: number; max_file_kb: number; max_total_mb: number;
  keep_versions: number; key_rate_per_min: number; git_name: string; git_email: string;
};

const NUM: { key: keyof Cfg; label: string; min: number; max: number }[] = [
  { key: "preview_port", label: "Preview port", min: 1024, max: 65535 }, { key: "max_apps", label: "Most apps", min: 1, max: 500 }, { key: "max_files", label: "Most files per app", min: 10, max: 2000 },
  { key: "max_file_kb", label: "Largest file (KB)", min: 16, max: 4096 }, { key: "max_total_mb", label: "Largest app (MB)", min: 1, max: 200 },
  { key: "keep_versions", label: "Versions kept per app", min: 5, max: 500 }, { key: "key_rate_per_min", label: "Calls per minute for each app's key", min: 1, max: 10000 },
];

export default function AppsSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Cfg>("/api/apps/config").then((c) => { setCfg(c); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!cfg) return <div className="card" data-testid="apps-settings">{err ? <span role="status" style={{ color: "var(--bad-text)" }}>The apps settings could not be loaded: {err}</span> : "Loading the apps settings…"}</div>;
  const set = <K extends keyof Cfg>(k: K, v: Cfg[K]) => setCfg({ ...cfg, [k]: v });
  const save = async () => {
    try { setCfg(await api<Cfg>("/api/apps/config", { method: "PUT", body: JSON.stringify(cfg) })); setErr(""); toast("Apps settings saved"); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card" data-testid="apps-settings">
      <div className="card-head"><b>Apps</b><InfoTip id="apps" /></div>
      <div className="card-desc">Small web apps that an agent builds with you and that use your agents. Open the Apps page to make one.</div>
      <div className="row" style={{ gap: 18, flexWrap: "wrap" }}>
        <label className="check"><input type="checkbox" checked={cfg.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Apps are on</label>
        <label className="check"><input type="checkbox" checked={cfg.auto_apply} onChange={(e) => set("auto_apply", e.target.checked)} /> Apply the builder&apos;s changes straight away (otherwise you accept or reject each one)</label>
        <label className="check"><input type="checkbox" checked={cfg.allow_server_apps} onChange={(e) => set("allow_server_apps", e.target.checked)} /> Allow server apps to run <InfoTip id="app-server" /></label>
      </div>
      {cfg.allow_server_apps && <div className="small" role="note" style={{ color: "var(--warn-text)" }}>A server app runs in a container on this PC. A container that opens a web port must be on an open network, so it can reach the internet. It has no packages and its files are read-only.</div>}
      <div className="grid" style={{ marginTop: 8 }}>
        {NUM.map((n) => (
          <label key={n.key}>{n.label}
            <input type="number" min={n.min} max={n.max} value={cfg[n.key] as number} aria-label={n.label} onChange={(e) => set(n.key, Number(e.target.value) as never)} /></label>
        ))}
        <label>Your name for git commits
          <input type="text" value={cfg.git_name} aria-label="Git name" onChange={(e) => set("git_name", e.target.value)} placeholder="Ada Lovelace" /></label>
        <label>Your email for git commits
          <input type="text" value={cfg.git_email} aria-label="Git email" onChange={(e) => set("git_email", e.target.value)} placeholder="ada@example.com" /></label>
      </div>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ marginTop: 8 }}><button className="acc" onClick={save}>Save apps settings</button></div>
    </div>
  );
}
