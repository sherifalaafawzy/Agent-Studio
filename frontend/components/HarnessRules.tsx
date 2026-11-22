"use client";
// Settings > Agents & sandbox: the rules every tool call passes through (permission mode, loop guard, project instructions, checkpoints,
// your own hooks, limits) and the web tools (off by default).
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "./Nav";
import InfoTip from "./InfoTip";

type Hook = { event: string; tool: string; match: string; action: string; message: string };
type Policy = { permission_mode: string; loop_guard_repeats: number; project_instructions: boolean; checkpoints_enabled: boolean; hooks: Hook[]; max_tool_calls_per_turn: number;
  max_framework_steps: number; web_enabled: boolean; web_allow_hosts: string[]; web_search_url: string };
type View = { policy: Policy; modes: string[]; web_search_key_set: boolean };
const MODE_TEXT: Record<string, string> = {
  full: "Full: every tool the agent was given",
  accept_edits: "Accept edits: read and change files, but no shell, new skills or servers",
  plan: "Plan: read-only, the agent can only look and describe what it would do",
};
const NEW_HOOK: Hook = { event: "pre_tool", tool: "*", match: "", action: "block", message: "" };

export default function HarnessRules() {
  const [v, setV] = useState<View | null>(null);
  const [p, setP] = useState<Policy | null>(null);
  const [hosts, setHosts] = useState("");
  const [key, setKey] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api<View>("/api/harness/policy").then((x) => { setV(x); setP(x.policy); setHosts(x.policy.web_allow_hosts.join("\n")); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!v || !p) return <div className="card" data-testid="harness-rules">{err ? <span role="status" style={{ color: "var(--bad)" }}>Harness rules could not be loaded: {err}</span> : "Loading the harness rules…"}</div>;
  const set = <K extends keyof Policy>(k: K, val: Policy[K]) => setP({ ...p, [k]: val });
  const setHook = (i: number, patch: Partial<Hook>) => set("hooks", p.hooks.map((h, j) => (j === i ? { ...h, ...patch, ...(patch.event ? { action: patch.event === "pre_tool" ? "block" : "note" } : {}) } : h)));
  const save = async () => {
    try {
      const body = { ...p, web_allow_hosts: hosts.split(/[\s,]+/).filter(Boolean) };
      const r = await api<View>("/api/harness/policy", { method: "PUT", body: JSON.stringify(body) });
      setV(r); setP(r.policy); setErr(""); toast("Harness rules saved");
    } catch (e) { setErr((e as Error).message); }
  };
  const saveKey = async (clear = false) => {
    try { const r = await api<View>("/api/harness/web-key", { method: "PUT", body: JSON.stringify({ brave_api_key: clear ? "" : key }) }); setV(r); setKey(""); toast(clear ? "Search key removed" : "Search key saved"); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card" data-testid="harness-rules">
      <div className="card-head"><b>Harness rules</b><InfoTip id="harness-rules" /></div>
      <div className="card-desc">These apply to every agent, whichever framework runs it. Your project&apos;s <code>AGENTS.md</code> or <code>CLAUDE.md</code> can also tell agents how to work in it.</div>
      <div className="grid">
        <label>Permission mode
          <select value={p.permission_mode} onChange={(e) => set("permission_mode", e.target.value)} aria-label="Permission mode">{v.modes.map((m) => <option key={m} value={m}>{MODE_TEXT[m] ?? m}</option>)}</select></label>
        <label>Loop guard: refuse the same call after this many repeats (0 = off)
          <input type="number" min={0} max={20} value={p.loop_guard_repeats} aria-label="Loop guard repeats" onChange={(e) => set("loop_guard_repeats", Number(e.target.value))} /></label>
        <label>Max tool calls per turn
          <input type="number" min={1} max={1000} value={p.max_tool_calls_per_turn} aria-label="Max tool calls per turn" onChange={(e) => set("max_tool_calls_per_turn", Number(e.target.value))} /></label>
        <label>Max steps for other frameworks
          <input type="number" min={1} max={200} value={p.max_framework_steps} aria-label="Max framework steps" onChange={(e) => set("max_framework_steps", Number(e.target.value))} /></label>
      </div>
      <div className="row" style={{ gap: 18, flexWrap: "wrap", marginTop: 8 }}>
        <label className="check"><input type="checkbox" checked={p.project_instructions} onChange={(e) => set("project_instructions", e.target.checked)} /> Read AGENTS.md / CLAUDE.md from the workspace</label>
        <label className="check"><input type="checkbox" checked={p.checkpoints_enabled} onChange={(e) => set("checkpoints_enabled", e.target.checked)} /> Keep checkpoints so file changes can be undone</label>
      </div>

      <div style={{ marginTop: 14 }}><b>Your hooks</b> <span className="small mut">small rules, no code: block a tool call whose details match a pattern, or add a reminder to a tool&apos;s result</span></div>
      {p.hooks.map((h, i) => (
        <div key={i} className="grid" data-testid="hook-row" style={{ marginTop: 6 }}>
          <label>When<select value={h.event} onChange={(e) => setHook(i, { event: e.target.value })} aria-label={`Hook ${i + 1} event`}><option value="pre_tool">before a tool runs (block it)</option><option value="post_tool">after a tool runs (add a note)</option></select></label>
          <label>Tool<input value={h.tool} placeholder="* or run_shell" aria-label={`Hook ${i + 1} tool`} onChange={(e) => setHook(i, { tool: e.target.value })} /></label>
          <label>If the call matches (pattern, optional)<input value={h.match} placeholder="e.g. rm\s+-rf" aria-label={`Hook ${i + 1} pattern`} onChange={(e) => setHook(i, { match: e.target.value })} /></label>
          <label>Message<input value={h.message} placeholder="what the agent should be told" aria-label={`Hook ${i + 1} message`} onChange={(e) => setHook(i, { message: e.target.value })} /></label>
          <button onClick={() => set("hooks", p.hooks.filter((_, j) => j !== i))} aria-label={`Remove hook ${i + 1}`}>Remove</button>
        </div>))}
      <div className="row" style={{ marginTop: 6 }}><button onClick={() => set("hooks", [...p.hooks, { ...NEW_HOOK }])}>Add a hook</button></div>

      <div style={{ marginTop: 14 }}><b>Web tools</b> <span className="small mut">off by default. Agents can then read public web pages (never your own network) and search.</span></div>
      <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={p.web_enabled} onChange={(e) => set("web_enabled", e.target.checked)} /> Let agents use web_fetch and web_search</label>
      {p.web_enabled && (
        <div className="grid" style={{ marginTop: 6 }}>
          <label>Only these sites (one per line, empty = any public site)<textarea rows={3} value={hosts} aria-label="Allowed sites" onChange={(e) => setHosts(e.target.value)} /></label>
          <label>SearXNG address (optional)<input value={p.web_search_url} placeholder="https://search.example.com" aria-label="Search address" onChange={(e) => set("web_search_url", e.target.value)} /></label>
          <label>Brave Search key (optional, write-only)
            <input type="password" autoComplete="off" value={key} placeholder={v.web_search_key_set ? "(stored: type to replace)" : "paste key"} aria-label="Brave Search key" onChange={(e) => setKey(e.target.value)} /></label>
          <div className="row"><button disabled={!key} onClick={() => saveKey()}>Save key</button>{v.web_search_key_set && <button onClick={() => saveKey(true)}>Remove key</button>}</div>
        </div>)}
      {err && <div className="small" role="alert" style={{ color: "var(--bad)", marginTop: 8 }}>{err}</div>}
      <div className="row" style={{ marginTop: 12 }}><button className="pri" onClick={save}>Save harness rules</button></div>
    </div>
  );
}
