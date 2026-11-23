"use client";
// Science Lab > Safety: what the lab refuses, asks you to confirm, and allows; a tester for the screen; and the level, which can be raised but never lowered or switched off.
import { useState } from "react";
import { api } from "@/lib/api";
import { Config } from "@/lib/science";
import { toast } from "../Nav";

type Decision = { action: "allow" | "confirm" | "block"; category: string; reason: string; help: string };

export default function SafetyPanel({ cfg, onChange }: { cfg: Config; onChange: (c: Config) => void }) {
  const [text, setText] = useState("");
  const [d, setD] = useState<Decision | null>(null);
  const [err, setErr] = useState("");
  const test = async () => {
    try { setD(await api<Decision>("/api/science/screen", { method: "POST", body: JSON.stringify({ text }) })); setErr(""); } catch (e) { setErr((e as Error).message); }
  };
  const raise = async () => {
    try { onChange(await api<Config>("/api/science/config", { method: "PUT", body: JSON.stringify({ safety_level: "strict" }) })); toast("Safety level raised to strict"); } catch (e) { setErr((e as Error).message); }
  };
  const p = cfg.policy;
  const List = ({ title, items, tone }: { title: string; items: string[]; tone: string }) => (
    <div className="card sci-policy"><div className={`sci-badge ${tone}`}>{title}</div><ul>{items.map((x) => <li key={x}>{x}</li>)}</ul></div>
  );
  return (
    <div data-testid="science-safety">
      <div className="card">
        <div className="card-head"><b>Safety policy</b> <span className="chip">level: {cfg.safety_level}</span></div>
        <div className="card-desc">Chemistry and biology can be misused. The lab checks every request, search and piece of code first. This lowers risk; it cannot remove it. Blocks are always explained, never silent, and are recorded (the length of the text only, never the text) in the security export.</div>
        <div className="grid">
          <List title="Blocked" items={p.blocked} tone="bad" />
          <List title="A person must confirm" items={p.confirm} tone="warn" />
          <List title="Fine to ask" items={p.allowed} tone="ok" />
          <List title="Tools that do not exist here" items={p.tools_not_offered} tone="mut" />
        </div>
        <div className="small" role="note" style={{ marginTop: 8 }}><b>Limits.</b> {p.limits.join(" ")}</div>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="ghost" disabled={cfg.safety_level === "strict"} onClick={raise}>{cfg.safety_level === "strict" ? "Already strict" : "Raise to strict (also blocks wet-lab procedures with biological hazards)"}</button>
        </div>
      </div>
      <div className="card">
        <div className="card-head"><b>Try the screen</b></div>
        <div className="card-desc">Type a request to see what the lab would do with it. Nothing is sent anywhere. A block here is recorded in the security export like any other.</div>
        <textarea value={text} rows={3} aria-label="Request to check" onChange={(e) => setText(e.target.value)} placeholder="For example: what are the symptoms of nerve agent exposure?" />
        <div className="row" style={{ marginTop: 6 }}><button className="acc" disabled={!text.trim()} onClick={test}>Check</button></div>
        {err && <div className="err small" role="alert">{err}</div>}
        {d && <div role="status" data-testid="screen-result" className={`sci-badge ${d.action === "allow" ? "ok" : d.action === "confirm" ? "warn" : "bad"}`} style={{ marginTop: 8, display: "block" }}>
          <b>{d.action === "allow" ? "Allowed" : d.action === "confirm" ? "Needs a person's confirmation" : "Blocked"}</b>{d.reason ? `: ${d.reason}` : ""} {d.help}</div>}
      </div>
    </div>
  );
}
