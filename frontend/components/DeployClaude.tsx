"use client";
// Deploy an agent to Claude Code (the CLI): choose what to do, PREVIEW every file and command with its diff, then confirm.
// Nothing is written until the user presses the confirm button; an existing file or server is only replaced when its own box is ticked.
import { useState } from "react";
import { api } from "@/lib/api";
import FolderPicker from "./FolderPicker";
import { Modal } from "./Modal";

type Mode = "export" | "live";
type Scope = "project" | "user";
type PlanFile = { path: string; kind: string; action: "create" | "update" | "unchanged" | "blocked"; note: string; content: string; diff: string };
type PlanCmd = { name: string; action: "create" | "update" | "unchanged"; display: string; diff: string };
type Plan = {
  mode: Mode; scope: Scope; agent: { id: string; name: string }; root: string; files: PlanFile[]; commands: PlanCmd[];
  not_carried: { item: string; why: string }[]; warnings: string[]; notes: string[]; prerequisites: { id: string; ok: boolean; text: string }[];
  env_vars: { name: string; used_by: string; help: string }[]; claude_found: boolean; hash: string;
};
type Result = { path?: string; command?: string; status: string; detail?: string; backup?: string };

export const ACTION_TEXT: Record<string, string> = { create: "new", update: "already exists", unchanged: "no change", blocked: "blocked" };
const KIND_TEXT: Record<string, string> = { agent: "Agent", skill: "Skill", "mcp-config": "MCP servers" };
const PWSH_KEY = '[Environment]::SetEnvironmentVariable("AGENT_STUDIO_KEY", "<paste the key>", "User")';

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

export default function DeployClaude({ agent, onClose }: { agent: { id: string; name: string }; onClose: () => void }) {
  const [mode, setMode] = useState<Mode>("export");
  const [scope, setScope] = useState<Scope>("project");
  const [path, setPath] = useState("");
  const [subs, setSubs] = useState(true);
  const [skills, setSkills] = useState(true);
  const [placement, setPlacement] = useState<"config" | "agent">("config");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState<Record<string, boolean>>({});
  const [run, setRun] = useState(false);
  const [results, setResults] = useState<Result[] | null>(null);
  const [key, setKey] = useState("");
  const [copied, setCopied] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const opts = () => ({ mode, scope, project_path: scope === "project" ? path : "", include_sub_agents: subs, include_skills: skills, mcp_placement: placement });
  const base = `/api/agents/${agent.id}/claude-code`;

  async function preview() {
    setBusy(true); setErr(""); setResults(null); setOver({}); setRun(false);
    try { setPlan(await api<Plan>(`${base}/plan`, { method: "POST", body: JSON.stringify(opts()) })); }
    catch (e) { setPlan(null); setErr((e as Error).message); }
    setBusy(false);
  }

  async function write() {
    if (!plan) return;
    setBusy(true); setErr("");
    const overwrite = [...plan.files.filter((f) => over[f.path]).map((f) => f.path), ...plan.commands.filter((c) => over[c.name]).map((c) => c.name)];
    try {
      const r = await api<{ ok: boolean; results: Result[] }>(`${base}/apply`, { method: "POST", body: JSON.stringify({ ...opts(), confirm: true, plan_hash: plan.hash, overwrite, run_commands: run }) });
      setResults(r.results);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }

  async function makeKey() {
    setBusy(true); setErr("");
    try {
      const r = await api<{ key: string }>("/api/access/keys", { method: "POST", body: JSON.stringify({ name: `Claude Code: ${agent.name}`.slice(0, 60), agents: [agent.id] }) });
      setKey(r.key);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }

  const writable = plan ? plan.files.filter((f) => f.action === "create" || (f.action === "update" && over[f.path])).length : 0;
  const cmdRun = plan ? plan.commands.filter((c) => c.action === "create" || (c.action === "update" && over[c.name])).length : 0;
  const nothing = !!plan && writable === 0 && !(run && cmdRun > 0);

  return (
    <Modal title={`Deploy “${agent.name}” to Claude Code`} onClose={onClose} wide>
      <div data-testid="deploy-claude">
        {!plan && (
          <>
            <div className="small mut">Claude Code is Anthropic&apos;s coding assistant for the terminal. You see every file first and nothing is written until you confirm.</div>
            <fieldset style={{ border: 0, padding: 0, margin: "12px 0 0" }}>
              <legend><b>What should happen?</b></legend>
              <label className="check"><input type="radio" name="dc-mode" checked={mode === "export"} onChange={() => setMode("export")} /> <span>Copy this agent into Claude Code <span className="mut small">(a subagent file, its skills and MCP servers; it then runs on Claude)</span></span></label>
              <label className="check"><input type="radio" name="dc-mode" checked={mode === "live"} onChange={() => setMode("live")} /> <span>Let Claude Code call the live agent <span className="mut small">(it keeps running here, with its guardrails, memory and spend caps)</span></span></label>
            </fieldset>
            <fieldset style={{ border: 0, padding: 0, margin: "12px 0 0" }}>
              <legend><b>Where?</b></legend>
              <label className="check"><input type="radio" name="dc-scope" checked={scope === "project"} onChange={() => setScope("project")} /> <span>One project folder <span className="mut small">(<code>.claude/</code> in that folder; share it with your team)</span></span></label>
              {scope === "project" && <div style={{ margin: "6px 0 6px 24px" }}><FolderPicker value={path} onChange={setPath} placeholder="e.g. H:/my-project" /></div>}
              <label className="check"><input type="radio" name="dc-scope" checked={scope === "user"} onChange={() => setScope("user")} /> <span>All my projects <span className="mut small">(your user folder, <code>~/.claude/</code>)</span></span></label>
            </fieldset>
            {mode === "export" && (
              <div style={{ margin: "12px 0 0" }}>
                <label className="check"><input type="checkbox" checked={subs} onChange={(e) => setSubs(e.target.checked)} /> Also copy its sub-agents</label>
                <label className="check"><input type="checkbox" checked={skills} onChange={(e) => setSkills(e.target.checked)} /> Also copy the skills it names</label>
              </div>)}
            <label style={{ marginTop: 10 }}>Where do its MCP servers go?
              <select value={placement} onChange={(e) => setPlacement(e.target.value as "config" | "agent")} aria-label="Where MCP servers go">
                <option value="config">In Claude Code&apos;s MCP settings ({scope === "project" ? ".mcp.json" : "claude mcp add"})</option>
                <option value="agent">Inside the agent file (only this agent sees them)</option>
              </select></label>
            {err && <div className="small err" role="alert" style={{ marginTop: 8 }}>{err}</div>}
            <div className="row" style={{ marginTop: 14 }}><button onClick={onClose}>Cancel</button><button className="pri" onClick={preview} disabled={busy || (scope === "project" && !path.trim())}>{busy ? "Preparing…" : "Preview"}</button></div>
          </>)}

        {plan && !results && (
          <>
            <div className="small">{plan.mode === "live" ? "Claude Code will call the running agent." : "A copy of the agent will be written for Claude Code."} Target: <code>{plan.root}</code></div>
            {plan.prerequisites.length > 0 && (
              <ul className="small" data-testid="deploy-prereq" style={{ margin: "8px 0", paddingLeft: 18 }}>
                {plan.prerequisites.map((p) => <li key={p.id} style={{ color: p.ok ? "var(--ok)" : "var(--warn)" }}>{p.ok ? "✓" : "✕"} {p.text}</li>)}
              </ul>)}
            {plan.mode === "live" && (
              <div className="card" style={{ margin: "8px 0" }}>
                {!key ? (
                  <div className="row"><span className="small grow">Claude Code needs a key to call this agent. It is shown once, here, and never written to a file.</span>
                    <button onClick={makeKey} disabled={busy}>Create a key for this agent</button></div>
                ) : (
                  <div data-testid="deploy-key">
                    <div className="small"><b>Copy the key now: it is shown only once.</b></div>
                    <div className="row" style={{ marginTop: 6 }}><code className="grow" style={{ wordBreak: "break-all" }}>{key}</code>
                      <button onClick={async () => setCopied((await copy(key)) ? "key" : "")}>{copied === "key" ? "Copied" : "Copy"}</button></div>
                    <div className="small mut" style={{ marginTop: 6 }}>Then set it as <code>AGENT_STUDIO_KEY</code> where Claude Code starts. In PowerShell: <code>{PWSH_KEY}</code></div>
                  </div>)}
              </div>)}
            {plan.warnings.length > 0 && <ul className="small" data-testid="deploy-warnings" style={{ margin: "8px 0", paddingLeft: 18, color: "var(--warn-text)" }}>{plan.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
            {plan.notes.length > 0 && <ul className="small mut" style={{ margin: "8px 0", paddingLeft: 18 }}>{plan.notes.map((w) => <li key={w}>{w}</li>)}</ul>}

            <h4 style={{ margin: "12px 0 4px" }}>Files</h4>
            {plan.files.length === 0 && <div className="small mut">No files.</div>}
            {plan.files.map((f) => (
              <div key={f.path} className="card" style={{ margin: "6px 0", padding: "8px 10px" }} data-testid="deploy-file">
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <span className={`pill ${f.action === "create" ? "ok" : f.action === "blocked" ? "bad" : f.action === "update" ? "warn" : ""}`}>{ACTION_TEXT[f.action]}</span>
                  <span className="small mut">{KIND_TEXT[f.kind] ?? f.kind}</span><code className="small grow" style={{ wordBreak: "break-all" }}>{f.path}</code>
                  {(f.action === "create" || f.action === "update") && <button className="ghost small" onClick={() => setOpen((o) => ({ ...o, [f.path]: !o[f.path] }))}>{open[f.path] ? "Hide" : "Show"} {f.action === "update" ? "changes" : "content"}</button>}
                </div>
                {f.note && <div className="small mut">{f.note}</div>}
                {f.action === "update" && <label className="check"><input type="checkbox" checked={!!over[f.path]} onChange={(e) => setOver((o) => ({ ...o, [f.path]: e.target.checked }))} /> Replace it (the old copy is kept in Agent Studio&apos;s data folder)</label>}
                {open[f.path] && <pre className="small" style={{ maxHeight: 260, overflow: "auto", margin: "6px 0 0", whiteSpace: "pre-wrap" }}>{f.diff}</pre>}
              </div>))}

            {plan.commands.length > 0 && (
              <>
                <h4 style={{ margin: "12px 0 4px" }}>Claude Code commands</h4>
                {plan.commands.map((c) => (
                  <div key={c.name} className="card" style={{ margin: "6px 0", padding: "8px 10px" }} data-testid="deploy-command">
                    <div className="row" style={{ gap: 8 }}><span className={`pill ${c.action === "create" ? "ok" : c.action === "update" ? "warn" : ""}`}>{ACTION_TEXT[c.action]}</span><b className="small grow">{c.name}</b>
                      {c.action !== "unchanged" && <button className="ghost small" onClick={async () => setCopied((await copy(c.display)) ? c.name : "")}>{copied === c.name ? "Copied" : "Copy command"}</button>}</div>
                    {c.action !== "unchanged" && <pre className="small" style={{ margin: "6px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{c.display}</pre>}
                    {c.action === "update" && <label className="check"><input type="checkbox" checked={!!over[c.name]} onChange={(e) => setOver((o) => ({ ...o, [c.name]: e.target.checked }))} /> Replace the server that is already there</label>}
                  </div>))}
                <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={run} disabled={!plan.claude_found} onChange={(e) => setRun(e.target.checked)} /> Run these commands for me{plan.claude_found ? "" : " (the claude program was not found on this PC)"}</label>
              </>)}

            {plan.env_vars.length > 0 && (
              <>
                <h4 style={{ margin: "12px 0 4px" }}>Set these in your environment</h4>
                <ul className="small" data-testid="deploy-env" style={{ margin: 0, paddingLeft: 18 }}>{plan.env_vars.map((v) => <li key={v.name}><code>{v.name}</code>: {v.help}</li>)}</ul>
              </>)}
            {plan.not_carried.length > 0 && (
              <details style={{ marginTop: 12 }} data-testid="deploy-not-carried"><summary><b>Not carried over ({plan.not_carried.length})</b></summary>
                <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>{plan.not_carried.map((n) => <li key={n.item}><b>{n.item}</b>: {n.why}</li>)}</ul></details>)}

            {err && <div className="small err" role="alert" style={{ marginTop: 8 }}>{err}</div>}
            <div className="row" style={{ marginTop: 14 }}>
              <button onClick={() => { setPlan(null); setErr(""); }}>Back</button>
              <button className="pri" onClick={write} disabled={busy || nothing}>{busy ? "Writing…" : `Confirm: write ${writable} file${writable === 1 ? "" : "s"}${run && cmdRun ? ` and run ${cmdRun} command${cmdRun === 1 ? "" : "s"}` : ""}`}</button>
            </div>
          </>)}

        {results && (
          <>
            <h4 style={{ margin: "0 0 6px" }}>Done</h4>
            <ul className="small" data-testid="deploy-results" style={{ margin: 0, paddingLeft: 18 }}>
              {results.map((r, i) => <li key={i}><span className={`pill ${r.status === "written" || r.status === "overwritten" || r.status === "done" ? "ok" : r.status === "failed" ? "bad" : ""}`}>{r.status}</span> <code>{r.path ?? r.command}</code>{r.detail ? ` — ${r.detail}` : ""}{r.backup ? ` (old copy kept: ${r.backup})` : ""}</li>)}
            </ul>
            <div className="small mut" style={{ marginTop: 8 }}>Claude Code picks up new subagent files within a few seconds. MCP servers from a project&apos;s <code>.mcp.json</code> ask for your approval the first time you start Claude Code there, and tools of a subagent ask for permission the first time they are used.</div>
            <div className="row" style={{ marginTop: 12 }}><button onClick={() => { setResults(null); setPlan(null); }}>Deploy again</button><button className="pri" onClick={onClose}>Close</button></div>
          </>)}
      </div>
    </Modal>
  );
}
