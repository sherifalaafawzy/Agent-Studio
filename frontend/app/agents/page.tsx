"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Agent = {
  id: string; name: string; description: string; instruction: string; provider: string | null; tier: string; model: string | null;
  tools: string[]; mcp: string[]; skills: string[]; sub_agents: string[]; builtin: boolean;
};
type Tool = { name: string; description: string; read_only: boolean };
type Mcp = { id: string; label: string; ready: boolean; enabled: boolean; status: string };
type Skill = { name: string; description: string };

const blank: Agent = { id: "", name: "", description: "", instruction: "", provider: null, tier: "flash", model: null, tools: [], mcp: [], skills: [], sub_agents: [], builtin: false };

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

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [mcp, setMcp] = useState<Mcp[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [sel, setSel] = useState<string>("engineer");
  const [f, setF] = useState<Agent>(blank);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const [a, t, m, s] = await Promise.all([api<Agent[]>("/api/agents"), api<Tool[]>("/api/tools"), api<Mcp[]>("/api/mcp"), api<Skill[]>("/api/skills")]);
    setAgents(a); setTools(t); setMcp(m); setSkills(s);
  }, []);
  useEffect(() => { load().catch((e) => setMsg("Backend offline: " + e.message)); }, [load]);
  useEffect(() => { const a = agents.find((x) => x.id === sel); if (a && !isNew) setF(a); }, [sel, agents, isNew]);

  const set = (k: keyof Agent, v: any) => setF((s) => ({ ...s, [k]: v }));

  async function save() {
    try {
      if (isNew) await api("/api/agents", { method: "POST", body: JSON.stringify(f) });
      else await api(`/api/agents/${f.id}`, { method: "PUT", body: JSON.stringify(f) });
      setMsg("Saved"); setIsNew(false); setSel(f.id); await load();
    } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }
  async function del() {
    if (!confirm(`Delete agent ${f.id}?`)) return;
    try { await api(`/api/agents/${f.id}`, { method: "DELETE" }); setMsg("Deleted"); setSel("engineer"); await load(); } catch (e) { setMsg("⚠ " + (e as Error).message); }
  }

  return (
    <div>
      <h2>Agent Studio</h2>
      <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
        <div style={{ width: 240 }}>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}><b>Agents</b>
              <button onClick={() => { setIsNew(true); setF({ ...blank }); setMsg(""); }}>+ New</button></div>
            {agents.map((a) => (
              <div key={a.id} onClick={() => { setIsNew(false); setSel(a.id); setMsg(""); }}
                style={{ padding: "6px 8px", marginTop: 4, borderRadius: 6, cursor: "pointer", background: !isNew && sel === a.id ? "var(--panel2)" : "transparent" }}>
                {a.name} <span className={`pill ${a.tier === "pro" ? "ok" : ""}`}>{a.tier}</span>
                {a.sub_agents.length > 0 && <span className="mut small"> · {a.sub_agents.length} sub</span>}
              </div>
            ))}
          </div>
          {!isNew && sel && <div className="card"><div className="mut small" style={{ marginBottom: 6 }}>Delegation tree</div><Tree id={sel} agents={agents} /></div>}
        </div>

        <div className="grow card" style={{ minWidth: 420 }}>
          <div className="grid">
            <label>Id {isNew ? "(a-z, 0-9, _)" : ""}<input value={f.id} disabled={!isNew} onChange={(e) => set("id", e.target.value)} /></label>
            <label>Name<input value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
            <label>Model tier
              <select value={f.tier} onChange={(e) => set("tier", e.target.value)}>
                <option value="pro">pro (planning/judgement)</option><option value="flash">flash (cheap/fast)</option><option value="custom">custom model</option>
              </select></label>
            {f.tier === "custom" && <label>Model<input value={f.model ?? ""} onChange={(e) => set("model", e.target.value)} /></label>}
            <label>Provider<input value={f.provider ?? ""} placeholder="(inherit from chat)" onChange={(e) => set("provider", e.target.value || null)} /></label>
          </div>
          <label style={{ marginTop: 10 }}>Description (how a parent agent decides to delegate to this one)
            <input value={f.description} onChange={(e) => set("description", e.target.value)} /></label>
          <label style={{ marginTop: 10 }}>Instruction
            <textarea rows={6} value={f.instruction} onChange={(e) => set("instruction", e.target.value)} /></label>

          <h3>Tools</h3>
          <div className="row">{tools.map((t) => (
            <label key={t.name} title={t.description} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={f.tools.includes(t.name)} onChange={() => set("tools", toggle(f.tools, t.name))} />{t.name}{t.read_only ? "" : " ✎"}
            </label>))}</div>
          <h3>MCP servers</h3>
          <div className="row">{mcp.map((m) => (
            <label key={m.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={f.mcp.includes(m.id)} onChange={() => set("mcp", toggle(f.mcp, m.id))} />
              {m.label} <span className={`pill ${m.ready ? "ok" : ""}`}>{m.ready ? "ready" : m.status === "pending_approval" ? "pending" : "off"}</span>
            </label>))}</div>
          <h3>Skills</h3>
          <div className="row">
            <label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={f.skills.includes("*")} onChange={() => set("skills", f.skills.includes("*") ? [] : ["*"])} />all
            </label>
            {!f.skills.includes("*") && skills.map((s) => (
              <label key={s.name} title={s.description} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <input type="checkbox" checked={f.skills.includes(s.name)} onChange={() => set("skills", toggle(f.skills, s.name))} />{s.name}
              </label>))}
          </div>
          <h3>Sub-agents (AgentTool · isolated context)</h3>
          <div className="row">{agents.filter((a) => a.id !== f.id).map((a) => (
            <label key={a.id} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <input type="checkbox" checked={f.sub_agents.includes(a.id)} onChange={() => set("sub_agents", toggle(f.sub_agents, a.id))} />{a.name}
            </label>))}</div>

          <div className="row" style={{ marginTop: 14 }}>
            <button className="pri" onClick={save}>{isNew ? "Create" : "Save"}</button>
            {!isNew && !f.builtin && <button onClick={del}>Delete</button>}
            <span className="small mut">{msg}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
