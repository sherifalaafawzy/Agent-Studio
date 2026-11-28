"use client";
import { IEdit } from "@/components/Icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import AgentWizard from "@/components/AgentWizard";
import InfoTip from "@/components/InfoTip";
import { MemoryPicker, type MemCfg } from "@/components/MemoryUI";
import AgentSecurity, { type Identity } from "@/components/AgentSecurity";
import AgentAccess from "@/components/AgentAccess";
import AgentAgui, { aguiBadge } from "@/components/AgentAgui";
import AgentLive, { liveBadge } from "@/components/AgentLive";
import AgentRuntime from "@/components/AgentRuntime";
import DeployClaude from "@/components/DeployClaude";
import { A2AConnect, AgentCreateChoice, RemotePanel, type Remote } from "@/components/RemoteAgents";
import { usePageContext } from "@/lib/pagectx";

type Agent = {
  id: string; name: string; description: string; instruction: string; provider: string | null; tier: string; model: string | null;
  tools: string[]; mcp: string[]; skills: string[]; sub_agents: string[]; builtin: boolean; can_spawn: boolean;
  acp_agents: string[]; acp_policy: string; rag?: string[]; memory?: MemCfg; guardrails?: string[]; identity?: Identity;
  framework?: string; api_access?: boolean; a2a?: boolean; runtime?: string; sandbox?: string; data?: string[];
  agui?: { components: boolean; endpoint: boolean };
  live?: Partial<import("@/lib/live").LiveProfile>;
};
type Tool = { name: string; description: string; read_only: boolean };
type Mcp = { id: string; label: string; ready: boolean; enabled: boolean; status: string };
type Skill = { name: string; description: string; group?: string };
type KbRef = { id: string; label: string; description?: string };
type Ext = { id: string; label: string; installed: boolean; enabled: boolean };
type Score = { percent: number; band: string; counts: { fail: number; warn: number } };
const SCORE_TONE: Record<string, string> = { good: "ok", middling: "warn", low: "bad" };

const blank: Agent = { id: "", name: "", description: "", instruction: "", provider: null, tier: "flash", model: null, tools: [], mcp: [], skills: [], sub_agents: [], builtin: false, can_spawn: false, acp_agents: [], acp_policy: "ask", rag: [], memory: {}, guardrails: [], identity: {}, framework: "adk", api_access: false, a2a: false, runtime: "local", sandbox: "", data: [] };

function Tree({ id, agents, depth = 0 }: { id: string; agents: Agent[]; depth?: number }) {
  const a = agents.find((x) => x.id === id);
  if (!a || depth > 4) return null;
  return (
    <div style={{ marginLeft: depth * 18 }}>
      <div className="small">{depth ? "└ " : ""}<b>{a.name}</b> <span className={`pill ${a.tier === "pro" ? "ok" : ""}`}>{a.tier === "custom" ? a.model : a.tier}</span>{" "}
        <span className="mut">{a.tools.length} tools{a.mcp.length ? ` · mcp: ${a.mcp.join(",")}` : ""}</span></div>
      {a.sub_agents.map((s) => <Tree key={s} id={s} agents={agents} depth={depth + 1} />)}
    </div>
  );
}

function toggle(list: string[], v: string) { return list.includes(v) ? list.filter((x) => x !== v) : [...list, v]; }

/** Skills bucketed by group (General last), each bucket sorted by name. `group:<label>` entries in an agent's skills attach a whole group. */
function groupSkills(skills: Skill[]): [string, Skill[]][] {
  const by = new Map<string, Skill[]>();
  for (const s of skills) by.set(s.group || "General", [...(by.get(s.group || "General") ?? []), s]);
  return [...by.entries()].sort(([a], [b]) => Number(a === "General") - Number(b === "General") || a.localeCompare(b));
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [mcp, setMcp] = useState<Mcp[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [kbs, setKbs] = useState<KbRef[]>([]);
  const [ext, setExt] = useState<Ext[]>([]);
  const [sel, setSel] = useState<string>("engineer");
  const [f, setF] = useState<Agent>(blank);
  const [isNew, setIsNew] = useState(false);
  usePageContext("agents", () => ({ editing: isNew ? "a new agent" : { id: sel, name: f.name, tier: f.tier }, wizardOpen: wizard, chooserOpen: choose }));
  const [msg, setMsg] = useState("");
  const [wizard, setWizard] = useState(false);                     // the guided creator is the default way to make a new agent
  const [choose, setChoose] = useState(false);                     // "+ New" first asks: wizard, or connect an A2A agent?
  const [a2a, setA2a] = useState(false);                           // the connect-an-A2A-agent form
  const [remotes, setRemotes] = useState<Remote[]>([]);            // remote A2A agents (they run elsewhere)
  const [selRemote, setSelRemote] = useState("");
  const [deploy, setDeploy] = useState<{ id: string; name: string } | null>(null);   // the "Deploy to Claude Code" dialog
  const [scores, setScores] = useState<Record<string, Score>>({});      // each agent's own security score (a badge that opens its scorecard)
  const pending = useRef<{ id: string; hash: string } | null>(null);     // /agents/?agent=<id>#<section> from a scorecard's "fix" link

  const load = useCallback(async () => {
    const [a, t, m, s] = await Promise.all([api<Agent[]>("/api/agents"), api<Tool[]>("/api/tools"), api<Mcp[]>("/api/mcp"), api<Skill[]>("/api/skills")]);
    setAgents(a); setTools(t); setMcp(m); setSkills(s);
    api<Ext[]>("/api/acp/agents").then(setExt).catch(() => setExt([]));
    api<KbRef[]>("/api/rag/kbs").then(setKbs).catch(() => setKbs([]));
    api<Remote[]>("/api/access/remotes").then(setRemotes).catch(() => setRemotes([]));
    api<{ agents: ({ id: string } & Score)[] }>("/api/security/agents").then((r) => setScores(Object.fromEntries(r.agents.map((x) => [x.id, x])))).catch(() => setScores({}));
  }, []);
  useEffect(() => { load().catch((e) => setMsg("Backend offline: " + e.message)); }, [load]);
  useEffect(() => { if (new URLSearchParams(window.location.search).get("wizard") === "1") setWizard(true); }, []);
  useEffect(() => { const a = agents.find((x) => x.id === sel); if (a && !isNew) setF(a); }, [sel, agents, isNew]);
  useEffect(() => { const q = new URLSearchParams(window.location.search).get("agent"); if (q) pending.current = { id: q, hash: window.location.hash.slice(1) }; }, []);
  useEffect(() => {
    const p = pending.current;
    if (!p || !agents.some((a) => a.id === p.id)) return;
    if (sel !== p.id) { setSel(p.id); setIsNew(false); return; }
    if (f.id !== p.id) return;                                          // wait until the editor shows that agent, then scroll to the section
    pending.current = null;
    requestAnimationFrame(() => document.getElementById(p.hash)?.scrollIntoView({ block: "start" }));
  }, [agents, sel, f.id]);

  const set = (k: keyof Agent, v: any) => setF((s) => ({ ...s, [k]: v }));
  const closePanes = () => { setWizard(false); setChoose(false); setA2a(false); setSelRemote(""); };

  async function save() {
    try {
      if (isNew) await api("/api/agents", { method: "POST", body: JSON.stringify(f) });
      else await api(`/api/agents/${f.id}`, { method: "PUT", body: JSON.stringify(f) });
      setMsg(""); toast(`Saved ${f.name || f.id}`); setIsNew(false); setSel(f.id); await load();
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function del() {
    if (!confirm(`Delete agent ${f.id}?`)) return;
    try { await api(`/api/agents/${f.id}`, { method: "DELETE" }); setMsg("Deleted"); setSel("engineer"); await load(); } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }

  return (
    <div>
      <div className="page-head"><h2>Agent Studio</h2></div>
      <p className="page-sub">Press <b>+ New</b> for a guided creator, or use the advanced editor. Design your agents: model tier, instructions, tools, MCP servers, skills, sub-agents, agents they may build, and external agents they may call. <b>Super Engineer</b> has everything switched on.</p>
      <div className="ag-layout">
        <div className="ag-side">
          <div className="card" data-tour="agent-list">
            <div className="row" style={{ justifyContent: "space-between" }}><b>Agents</b>
              <button onClick={() => { closePanes(); setChoose(true); setMsg(""); }}>+ New</button></div>
            {agents.map((a) => (
              <div key={a.id} role="button" tabIndex={0} aria-pressed={!isNew && !selRemote && sel === a.id} className={`ag-item ${!isNew && !selRemote && sel === a.id ? "on" : ""}`}
                onClick={() => { closePanes(); setIsNew(false); setSel(a.id); setMsg(""); }}
                onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); closePanes(); setIsNew(false); setSel(a.id); setMsg(""); } }}>
                <div className="ag-item-top">
                  <span className="ag-item-name" title={a.name}>{a.name}</span> <span className={`pill ${a.tier === "pro" ? "ok" : ""}`}>{a.tier}</span>
                  <span className="grow" />
                  {scores[a.id] && <a className={`pill ag-score ${SCORE_TONE[scores[a.id].band] ?? ""}`} href={`/security/?agent=${a.id}`} onClick={(e) => e.stopPropagation()}
                    title="Security score: open this agent's scorecard" aria-label={`Security score ${scores[a.id].percent} percent: open the scorecard of ${a.name}`}>{scores[a.id].percent}%</a>}
                </div>
                {(a.sub_agents.length > 0 || a.can_spawn || liveBadge(a) || aguiBadge(a)) && <div className="ag-item-meta">
                  {a.sub_agents.length > 0 && <span className="mut small">{a.sub_agents.length} sub</span>}
                  {a.can_spawn && <span className="mut small" title="can create its own sub-agents">builds agents</span>}
                  {liveBadge(a) && <span className={`pill ${liveBadge(a) === "Live off" ? "" : "ok"}`} data-testid="live-badge" title="This agent's Live profile (see its Live section)">{liveBadge(a)}</span>}
                  {aguiBadge(a) && <span className="pill" data-testid="agui-badge" title="AG-UI is partly or fully switched off for this agent (see its AG-UI section)">{aguiBadge(a)}</span>}
                </div>}
                {!isNew && !selRemote && sel === a.id && <div><button className="ghost small" data-testid="deploy-claude-list" aria-label={`Deploy ${a.name} to Claude Code`}
                  onClick={(e) => { e.stopPropagation(); setDeploy({ id: a.id, name: a.name }); }}>Deploy to Claude Code…</button></div>}
              </div>
            ))}
            {remotes.length > 0 && <div className="mut small" style={{ marginTop: 10 }}>Remote A2A agents</div>}
            {remotes.map((r) => (
              <div key={r.id} data-testid="remote-row" onClick={() => { closePanes(); setIsNew(false); setSelRemote(r.id); setMsg(""); }}
                style={{ padding: "6px 8px", marginTop: 4, borderRadius: 6, cursor: "pointer", background: selRemote === r.id ? "var(--panel2)" : "transparent", opacity: r.enabled ? 1 : 0.6 }}>
                {r.label} <span className="pill acc">Remote A2A</span>
                <span className={`pill ${r.network_scope === "internet" ? "warn" : ""}`} title="Where this agent runs">{r.scope_label}</span>
              </div>))}
          </div>
          {!isNew && sel && !selRemote && <div className="card" data-tour="agent-tree"><div className="mut small" style={{ marginBottom: 6 }}>Delegation tree</div><Tree id={sel} agents={agents} /></div>}
        </div>

        {choose ? (
          <AgentCreateChoice onWizard={() => { closePanes(); setWizard(true); }} onA2A={() => { closePanes(); setA2a(true); }} />
        ) : a2a ? (
          <A2AConnect onCancel={() => setA2a(false)} onConnected={async (id) => { closePanes(); setSelRemote(id); await load(); }} />
        ) : selRemote && remotes.find((r) => r.id === selRemote) ? (
          <RemotePanel key={selRemote} r={remotes.find((r) => r.id === selRemote)!} agents={agents.map((a) => ({ id: a.id, name: a.name }))}
            onChanged={load} onDeleted={() => { setSelRemote(""); load(); }} />
        ) : wizard ? (
          <div className="ag-main">
            <AgentWizard agents={agents} mcp={mcp} skills={skills} kbs={kbs} ext={ext}
              onCancel={() => setWizard(false)} onAdvanced={() => { setWizard(false); setIsNew(true); setF({ ...blank }); setMsg(""); }}
              onCreated={async (id) => { setWizard(false); setIsNew(false); setSel(id); await load(); }} />
          </div>
        ) : (
        <div className="card ag-main">
          {!isNew && scores[f.id] && (
            <div className="ag-scorebar" data-testid="agent-score">
              <span className={`pill ${SCORE_TONE[scores[f.id].band] ?? ""}`}>Security score {scores[f.id].percent}%</span>
              <span className="small mut">{scores[f.id].counts.fail} missing · {scores[f.id].counts.warn} partly</span>
              <span className="grow" /><a className="small" href={`/security/?agent=${f.id}`}>View its scorecard →</a>
            </div>)}
          <div className="grid">
            <label>Id {isNew ? "(a-z, 0-9, _)" : ""}<input value={f.id} disabled={!isNew} onChange={(e) => set("id", e.target.value)} /></label>
            <label>Name<input value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
            <label>Model tier
              <select value={f.tier} onChange={(e) => set("tier", e.target.value)}>
                <option value="pro">pro (planning/judgement)</option><option value="flash">flash (cheap/fast)</option><option value="custom">custom model</option>
              </select><InfoTip id="tiers" /></label>
            {f.tier === "custom" && <label>Model<input value={f.model ?? ""} onChange={(e) => set("model", e.target.value)} /></label>}
            <label>Provider<input value={f.provider ?? ""} placeholder="(inherit from chat)" onChange={(e) => set("provider", e.target.value || null)} /><InfoTip id="provider" /></label>
          </div>
          <label style={{ marginTop: 10 }}>Description (how a parent agent decides to delegate to this one)
            <input value={f.description} onChange={(e) => set("description", e.target.value)} /><InfoTip id="agent-description" /></label>
          <label style={{ marginTop: 10 }}>Instruction
            <textarea rows={6} value={f.instruction} onChange={(e) => set("instruction", e.target.value)} /><InfoTip id="instructions" /></label>

          <h3 id="ag-tools">Tools <InfoTip id="tools" /></h3>
          <div className="check-grid">{tools.map((t) => (
            <label key={t.name} title={t.description}>
              <input type="checkbox" checked={f.tools.includes(t.name)} onChange={() => set("tools", toggle(f.tools, t.name))} /><span className="check-name">{t.name}</span>{!t.read_only && <span className="tool-writes" title="Can change files, run commands or send something" aria-label="can make changes"><IEdit size={12} /></span>}
            </label>))}</div>
          <p className="small mut tool-legend"><IEdit size={12} /> marks a tool that can change files, run commands or send something. The others only read.</p>
          <h3 id="ag-mcp">MCP servers <InfoTip id="mcp" /></h3>
          <div className="check-grid wide">{mcp.map((m) => (
            <label key={m.id} title={m.label}>
              <input type="checkbox" checked={f.mcp.includes(m.id)} onChange={() => set("mcp", toggle(f.mcp, m.id))} />
              <span className="check-name">{m.label}</span> <span className={`pill ${m.ready ? "ok" : ""}`}>{m.ready ? "ready" : m.status === "pending_approval" ? "pending" : "off"}</span>
            </label>))}</div>
          <h3>Skills <InfoTip id="skills" /></h3>
          <div className="row">
            <label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={f.skills.includes("*")} onChange={() => set("skills", f.skills.includes("*") ? [] : ["*"])} />all
            </label>
          </div>
          {!f.skills.includes("*") && groupSkills(skills).map(([g, list]) => {
            const ref = `group:${g}`;
            const whole = f.skills.some((x) => x.toLowerCase() === ref.toLowerCase());
            return (
              <div key={g} className="ag-skillgroup" data-group={g}>
                <label className="ag-skillgroup-head" title="Attach every skill in this group, including ones added later">
                  <input type="checkbox" checked={whole} onChange={() => set("skills", whole ? f.skills.filter((x) => x.toLowerCase() !== ref.toLowerCase()) : [...f.skills, ref])} />
                  <b>{g}</b> <span className="mut small">whole group · {list.length}</span>
                </label>
                <div className="row">{list.map((s) => (
                  <label key={s.name} title={s.description} style={{ flexDirection: "row", alignItems: "center", gap: 4, opacity: whole ? 0.55 : 1 }}>
                    <input type="checkbox" checked={whole || f.skills.includes(s.name)} disabled={whole} onChange={() => set("skills", toggle(f.skills, s.name))} />{s.name}
                  </label>))}</div>
              </div>);
          })}
          <h3 id="ag-rag">Knowledge bases <InfoTip id="rag" /></h3>
          <div className="row">
            <label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={(f.rag ?? []).includes("*")} onChange={() => set("rag", (f.rag ?? []).includes("*") ? [] : ["*"])} />all knowledge bases
            </label>
            {!(f.rag ?? []).includes("*") && kbs.map((kb) => (
              <label key={kb.id} title={kb.description} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <input type="checkbox" checked={(f.rag ?? []).includes(kb.id)} onChange={() => set("rag", toggle(f.rag ?? [], kb.id))} />{kb.label}
              </label>))}
            {kbs.length === 0 && <span className="small mut">None yet: create one on the <a href="/knowledge/">Knowledge</a> page.</span>}
          </div>
          <div className="mut small">Attached knowledge bases give this agent the <code>rag_search</code> and <code>rag_list</code> tools automatically.</div>
          <h3 id="ag-memory">Memory <InfoTip id="memory-types" /></h3>
          <MemoryPicker value={f.memory} onChange={(m) => set("memory", m)} />
          <div className="mut small">Memory gives this agent <code>memory_search</code>, <code>memory_save</code> and <code>memory_forget</code>. Manage what it remembered on the <a href="/memory/">Memory</a> page.</div>
          <h3>Sub-agents (AgentTool · isolated context) <InfoTip id="sub-agents" /></h3>
          <div className="row">{agents.filter((a) => a.id !== f.id).map((a) => (
            <label key={a.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={f.sub_agents.includes(a.id)} onChange={() => set("sub_agents", toggle(f.sub_agents, a.id))} />{a.name}
            </label>))}</div>

          <h3 id="ag-spawn">Create its own sub-agents <InfoTip id="spawn" /></h3>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={f.can_spawn} onChange={(e) => set("can_spawn", e.target.checked)} />
            Allow this agent to build and run sub-agents at runtime (<code>spawn_agent</code>)</label>
          <div className="mut small">It can only hand a new sub-agent the tools and MCP servers it holds itself (ticked above), never <code>create_skill</code> / <code>propose_mcp_server</code>; sub-agents it creates cannot create more; every model call still goes through the spend caps. Created agents appear live in Chat → Agents and can be saved from there. The master switch and per-turn limit are in Settings.</div>

          <h3 id="ag-acp">Call external agents (ACP) <InfoTip id="acp" /></h3>
          <div className="row">{ext.map((x) => (
            <label key={x.id} style={{ flexDirection: "row", alignItems: "center", gap: 4, opacity: x.installed && x.enabled ? 1 : 0.5 }} title={x.installed ? (x.enabled ? "" : "disabled in Settings") : "not installed"}>
              <input type="checkbox" checked={(f.acp_agents ?? []).includes(x.id)} onChange={() => set("acp_agents", toggle(f.acp_agents ?? [], x.id))} />{x.label}{!x.installed ? " (not installed)" : ""}
            </label>))}</div>
          <div className="row" style={{ marginTop: 8 }}>
            <label>Their permission requests
              <select value={f.acp_policy ?? "ask"} onChange={(e) => set("acp_policy", e.target.value)}>
                <option value="ask">ask me in the chat (unanswered for 10 min = denied)</option>
                <option value="reads_auto">auto-allow reads, ask for everything else</option>
                <option value="readonly">read-only: auto-deny anything that changes things</option>
              </select><InfoTip id="acp-policy" /></label>
          </div>
          <div className="mut small" style={{ marginTop: 4 }}>Gives this agent a <code>call_external_agent</code> tool. External agents work in the same workspace with their own models and tools, on <b>their own accounts</b> — Agent Studio&apos;s caps don&apos;t cover them. Their runs appear live in the Agents panel and in Runs.</div>

          <AgentSecurity agentId={f.id} isNew={isNew} guardrails={f.guardrails ?? []} identity={f.identity ?? {}} others={agents.filter((a) => a.id !== f.id)}
            onChange={(g, i) => setF((s) => ({ ...s, guardrails: g, identity: i }))} />

          <AgentAccess agent={f} onChange={(patch) => setF((s) => ({ ...s, ...patch }))} />
          <AgentAgui agent={f} isNew={isNew} onChange={(agui) => setF((s) => ({ ...s, agui }))} />
          <AgentLive agent={f} onChange={(live) => setF((s) => ({ ...s, live }))} />
          <AgentRuntime agent={f} isNew={isNew} onChange={(patch) => setF((s) => ({ ...s, ...patch }))} />

          <div className="row" style={{ marginTop: 14 }}>
            <button className="pri" onClick={save}>{isNew ? "Create" : "Save"}</button>
            {!isNew && <button onClick={() => setDeploy({ id: f.id, name: f.name || f.id })} title="Copy this agent into Claude Code, or let Claude Code call it live">Deploy to Claude Code…</button>}
            {!isNew && !f.builtin && <button onClick={del}>Delete</button>}
            <span className="small mut">{msg}</span>
          </div>
        </div>
        )}
      </div>
      {deploy && <DeployClaude agent={deploy} onClose={() => setDeploy(null)} />}
    </div>
  );
}
