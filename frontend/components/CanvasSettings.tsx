"use client";
// Settings > Agents & sandbox: the canvas (a document you and an agent edit together). Whether it is on, whether the agent's changes apply at once or wait for your review,
// the size limit and how many versions are kept.
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Cfg = { enabled: boolean; auto_apply: boolean; max_chars: number; keep_versions: number };

export default function CanvasSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Cfg>("/api/canvas/config").then((c) => { setCfg(c); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!cfg) return <div className="card" data-testid="canvas-settings">{err ? <span role="status" style={{ color: "var(--bad)" }}>The canvas settings could not be loaded: {err}</span> : "Loading the canvas settings…"}</div>;
  const set = <K extends keyof Cfg>(k: K, v: Cfg[K]) => setCfg({ ...cfg, [k]: v });
  const save = async () => {
    try { setCfg(await api<Cfg>("/api/canvas/config", { method: "PUT", body: JSON.stringify(cfg) })); setErr(""); toast("Canvas settings saved"); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card" data-testid="canvas-settings">
      <div className="card-head"><b>Canvas</b><InfoTip id="canvas" /></div>
      <div className="card-desc">A document, code, table or web page you edit next to the chat with an agent. Open it with the Canvas button above a chat.</div>
      <div className="row" style={{ gap: 18, flexWrap: "wrap" }}>
        <label className="check"><input type="checkbox" checked={cfg.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Canvas is on</label>
        <label className="check"><input type="checkbox" checked={cfg.auto_apply} onChange={(e) => set("auto_apply", e.target.checked)} /> Apply agent changes straight away (otherwise you accept or reject each one)</label>
      </div>
      {cfg.auto_apply && <div className="small mut" role="note">Every change is still a version you can bring back. An agent in plan mode can only propose changes, whatever this says.</div>}
      <div className="grid" style={{ marginTop: 8 }}>
        <label>Largest document (characters)
          <input type="number" min={1000} max={2000000} value={cfg.max_chars} aria-label="Largest document" onChange={(e) => set("max_chars", Number(e.target.value))} /></label>
        <label>Versions kept for each canvas
          <input type="number" min={5} max={1000} value={cfg.keep_versions} aria-label="Versions kept" onChange={(e) => set("keep_versions", Number(e.target.value))} /></label>
      </div>
      {err && <div className="err small" role="alert">{err}</div>}
      <div className="row" style={{ marginTop: 8 }}><button className="acc" onClick={save}>Save canvas settings</button></div>
    </div>
  );
}
