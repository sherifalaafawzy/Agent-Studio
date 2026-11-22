"use client";
// In the agent editor: which agent framework runs this agent (with what it would lose), and whether it may be called through
// the OpenAI-compatible API or over A2A.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "./InfoTip";

type Fw = { id: string; label: string; blurb: string; state: string; native: boolean; billing: string; models: string };
type AgentLike = { framework?: string; api_access?: boolean; a2a?: boolean; tools: string[]; mcp: string[]; skills: string[]; sub_agents: string[]; acp_agents: string[]; rag?: string[]; can_spawn: boolean };
type Compat = { framework: string; ready: boolean; warnings: string[] };

export default function AgentAccess({ agent, onChange }: { agent: AgentLike; onChange: (patch: { framework?: string; api_access?: boolean; a2a?: boolean }) => void }) {
  const [fws, setFws] = useState<Fw[]>([]);
  const [compat, setCompat] = useState<Compat | null>(null);
  const fid = agent.framework ?? "adk";
  useEffect(() => { api<{ frameworks: Fw[] }>("/api/frameworks").then((m) => setFws(m.frameworks)).catch(() => setFws([])); }, []);
  const key = JSON.stringify([fid, agent.tools, agent.mcp, agent.skills, agent.sub_agents, agent.acp_agents, agent.rag, agent.can_spawn]);
  useEffect(() => {
    api<Compat>("/api/frameworks/compat", { method: "POST", body: JSON.stringify({ framework: fid, tools: agent.tools, mcp: agent.mcp, skills: agent.skills, sub_agents: agent.sub_agents,
      acp_agents: agent.acp_agents, rag: agent.rag ?? [], can_spawn: agent.can_spawn }) }).then(setCompat).catch(() => setCompat(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const cur = fws.find((f) => f.id === fid);
  return (
    <div className="card" id="ag-access" data-testid="agent-access" style={{ marginTop: 14 }}>
      <div className="card-head"><b>Framework and access</b><InfoTip id="frameworks" /></div>
      <div className="grid">
        <label>Agent framework
          <select value={fid} onChange={(e) => onChange({ framework: e.target.value })} aria-label="Agent framework">
            {(fws.length ? fws : [{ id: "adk", label: "Google ADK", state: "ready" } as Fw]).map((f) => (
              <option key={f.id} value={f.id}>{f.label}{f.native ? " (built in)" : f.state === "ready" ? " (installed)" : " (not installed)"}</option>))}
          </select></label>
      </div>
      {cur && <div className="small mut" style={{ marginTop: 6 }}>{cur.blurb} <i>{cur.models}.</i></div>}
      {compat && !compat.ready && <div className="small" role="alert" style={{ marginTop: 6, color: "var(--warn)" }}>This framework is not installed yet. Install it in <a href="/settings/#harness">Settings &gt; Agents &amp; sandbox</a>, or the agent will not run.</div>}
      {compat && compat.warnings.length > 0 && (
        <ul className="small" data-testid="compat-warnings" style={{ margin: "6px 0 0", paddingLeft: 18, color: "var(--warn)" }}>{compat.warnings.map((w) => <li key={w}>{w}</li>)}</ul>)}
      <div className="row" style={{ marginTop: 12, gap: 18, flexWrap: "wrap" }}>
        <label className="check"><input type="checkbox" checked={!!agent.api_access} onChange={(e) => onChange({ api_access: e.target.checked })} /> API access (OpenAI-compatible)<InfoTip id="api-access" /></label>
        <label className="check"><input type="checkbox" checked={!!agent.a2a} onChange={(e) => onChange({ a2a: e.target.checked })} /> A2A (other agents can call it)</label>
      </div>
      {(agent.api_access || agent.a2a) && <div className="small mut" style={{ marginTop: 4 }}>Callers need a key and the feature switched on in <a href="/settings/#access">Settings &gt; API &amp; A2A</a>.</div>}
    </div>
  );
}
