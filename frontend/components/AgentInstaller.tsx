"use client";
// One-click install of coding agents (Claude Code, Codex, Gemini CLI, ...). Safe methods run directly; a vendor script
// only runs after the user ticks a box next to the exact command, which is also offered as "copy and run it yourself".
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "./InfoTip";
import { toast } from "./Nav";

type Method = { kind: string; label: string; command: string; safe: boolean; warning?: string; note?: string; text?: string };
type Agent = { id: string; label: string; installed: boolean; path: string | null; version: string | null; signin: string; cost: string; note: string; manual: string; registry_extra: boolean; methods: Method[] };
type Catalog = { agents: Agent[]; registry: { source: string; fetched_at: number | null } };
type Job = { state: string; agent: string; action: string; log: string[]; error: string; running: boolean };

/** "grok 1.0.92 (b06756fd6256) [stable]" -> "1.0.92": the part a person cares about. */
export const shortVersion = (v: string) => v.match(/\d+(?:\.[\w-]+)+/)?.[0] ?? v.slice(0, 28);

export default function AgentInstaller({ only, onChange }: { only?: string[]; onChange?: (installed: number) => void }) {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);      // "agent:kind" of the script waiting for a yes
  const [yes, setYes] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try { const c = await api<Catalog>("/api/easy/agents"); setCat(c); onChange?.(c.agents.filter((a) => a.installed).length); }
    catch (e) { setErr((e as Error).message); }
  }, [onChange]);
  useEffect(() => { void load(); }, [load]);

  const running = job?.running ?? false;
  useEffect(() => {
    if (!running) return;
    timer.current = setInterval(async () => {
      try {
        const j = await api<Job>("/api/easy/agents/status");
        setJob(j);
        if (!j.running) { clearInterval(timer.current!); toast(j.state === "done" ? `${j.action === "uninstall" ? "Removed" : "Installed"} ${j.agent}` : `Could not ${j.action} ${j.agent}`, j.state === "done" ? "ok" : "err"); void load(); }
      } catch { /* keep polling */ }
    }, 1000);
    return () => clearInterval(timer.current!);
  }, [running, load]);

  async function run(agent: string, method: Method, confirmCommand = "") {
    setErr(""); setConfirm(null); setYes(false);
    try { setJob(await api<Job>("/api/easy/agents/install", { method: "POST", body: JSON.stringify({ agent, method: method.kind, confirm_command: confirmCommand }) })); }
    catch (e) { setErr((e as Error).message); }
  }
  async function remove(agent: string) {
    setErr("");
    try { setJob(await api<Job>("/api/easy/agents/uninstall", { method: "POST", body: JSON.stringify({ agent }) })); }
    catch (e) { setErr((e as Error).message); }
  }
  async function refresh() {
    try { const r = await api<{ source: string; count: number }>("/api/easy/agents/registry/refresh", { method: "POST" }); toast(`Registry: ${r.count} agents (${r.source})`); await load(); }
    catch (e) { setErr((e as Error).message); }
  }
  const copy = (text: string) => { void navigator.clipboard?.writeText(text); toast("Command copied"); };

  if (!cat) return <div className="small mut">{err || "Checking which coding agents are installed (the first check takes a few seconds)…"}</div>;
  const mine = cat.agents.filter((a) => !a.registry_extra && (!only || only.includes(a.id)));
  const extra = cat.agents.filter((a) => a.registry_extra);

  const card = (a: Agent) => {
    const busy = job?.running && job.agent === a.id;
    return (
      <div key={a.id} className="ai-card">
        <div className="row" style={{ gap: 8 }}><b>{a.label}</b>
          <span className={`pill ${a.installed ? "ok" : ""}`}>{a.installed ? `installed${a.version ? ` · ${shortVersion(a.version)}` : ""}` : "not installed"}</span>
          {a.installed && !a.registry_extra && <button className="ghost small" disabled={!!job?.running} onClick={() => remove(a.id)}>Remove</button>}</div>
        {a.note && <div className="small mut">{a.note}</div>}
        {a.manual && <div className="small" style={{ marginTop: 4 }}>{a.manual}</div>}
        <div className="small mut" style={{ marginTop: 4 }}><b>Who pays:</b> {a.cost}</div>
        <details style={{ marginTop: 4 }}><summary className="small mut">How to sign in</summary><div className="small">{a.signin}</div></details>
        {a.methods.map((m) => {
          const key = `${a.id}:${m.kind}`;
          if (m.kind === "manual") return a.manual ? null : <div key={key} className="small mut" style={{ marginTop: 6 }}>{m.text}</div>;
          return (
            <div key={key} className="ai-method">
              <div className="row" style={{ gap: 8 }}>
                <button className={m.safe && !a.installed ? "acc small" : "small"} disabled={!!job?.running}
                  onClick={() => (m.safe ? run(a.id, m) : setConfirm(confirm === key ? null : key))}>{a.installed ? "Reinstall" : "Install"}: {m.label}</button>
                {busy && <span className="small mut">working…</span>}
              </div>
              <div className="small mut"><code>{m.command}</code>{m.note ? ` — ${m.note}` : ""}</div>
              {!m.safe && confirm === key && (
                <div className="ai-warn">
                  <div className="small"><b>This runs a script from the internet on your PC.</b> {m.warning}</div>
                  <pre className="small" style={{ whiteSpace: "pre-wrap", margin: "6px 0" }}>{m.command}</pre>
                  <label className="small" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={yes} onChange={(e) => setYes(e.target.checked)} />I trust this vendor and want to run exactly this command</label>
                  <div className="row" style={{ marginTop: 6 }}>
                    <button className="acc small" disabled={!yes} onClick={() => run(a.id, m, m.command)}>Run it</button>
                    <button className="small" onClick={() => copy(m.command)}>Copy the command to run it yourself</button></div>
                </div>)}
            </div>);
        })}
        {job && job.agent === a.id && (job.log.length > 0 || job.error) && (
          <div style={{ marginTop: 6 }}>
            {job.state === "failed" && <div className="small err">{job.error}</div>}
            {job.state === "done" && <div className="small" style={{ color: "var(--ok-text)" }}>Done.</div>}
            <details><summary className="small mut">Details</summary><pre className="small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{job.log.join("\n")}</pre></details>
          </div>)}
      </div>);
  };

  return (
    <div className="ai">
      <div className="row small mut" style={{ marginBottom: 8 }}>Coding agents work on your files and bill their own accounts. <InfoTip id="acp" /></div>
      {mine.map(card)}
      {err && <div className="small err">{err}</div>}
      {!only && extra.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary className="small">More agents from the official registry ({extra.length})</summary>
          <div className="row small mut" style={{ margin: "6px 0" }}>Source: {cat.registry.source}. <button className="ghost small" onClick={refresh}>Refresh the list</button></div>
          {extra.map(card)}
        </details>)}
    </div>
  );
}
