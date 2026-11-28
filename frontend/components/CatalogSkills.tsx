"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import Markdown from "@/components/Markdown";
import { ICheck, IBook } from "@/components/Icons";

export type CatalogSkill = { name: string; description: string; mcp: string[]; domain: string; group: string; source: string; used_by: { id: string; name: string }[] };
type AgentRow = { id: string; name: string; skills: string[]; builtin?: boolean };

/** The domain skills: browse by domain, read one, and add it to an agent with one click. */
export default function CatalogSkills({ domain, q }: { domain: string; q: string }) {
  const [skills, setSkills] = useState<CatalogSkill[]>([]);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [target, setTarget] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([api<CatalogSkill[]>("/api/catalog/skills"), api<AgentRow[]>("/api/agents")]);
      setSkills(s); setAgents(a); setErr("");
    } catch (e) { setErr("Backend offline: " + (e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function read(name: string) {
    if (open === name) { setOpen(null); return; }
    setOpen(name); setBody("");
    try { setBody((await api<{ body: string }>(`/api/skills/${name}`)).body); } catch (e) { setBody("Could not load: " + (e as Error).message); }
  }
  async function add(skill: CatalogSkill) {
    const id = target[skill.name] || agents[0]?.id;
    const agent = agents.find((a) => a.id === id);
    if (!agent) return;
    try {
      await api(`/api/agents/${agent.id}`, { method: "PUT", body: JSON.stringify({ skills: [...new Set([...agent.skills, skill.name])] }) });
      toast(`“${skill.name}” added to ${agent.name}`);
      await load();
    } catch (e) { toast((e as Error).message, "err"); }
  }

  const shown = skills.filter((s) => (!domain || s.domain === domain) && (!q || `${s.name} ${s.description} ${s.group}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <div>
      {err && <p className="err">{err}</p>}
      <div className="cat-grid" data-testid="skill-grid">
        {shown.map((s) => {
          const on = agents.find((a) => a.id === (target[s.name] || agents[0]?.id));
          const has = !!on && (on.skills.includes(s.name) || on.skills.includes("*") || on.skills.map((x) => x.toLowerCase()).includes(`group:${s.group}`.toLowerCase()));
          return (
            <div key={s.name} className="card cat-card" data-skill={s.name}>
              <div className="card-head"><IBook size={16} /><div className="cat-title"><b>{s.name}</b></div><span className="pill">{s.group}</span></div>
              <p className="small" style={{ margin: "8px 0" }}>{s.description}</p>
              {s.mcp.length > 0 && <div className="small mut">Works with: {s.mcp.map((m) => <span key={m} className="pill" style={{ marginRight: 4 }}>{m}</span>)}</div>}
              <div className="small mut" style={{ marginTop: 6 }}>{s.used_by.length ? `Used by ${s.used_by.map((a) => a.name).join(", ")}` : "No agent uses it yet"}</div>
              <div className="row cat-actions" style={{ flexWrap: "wrap" }}>
                <select aria-label={`Agent for ${s.name}`} value={target[s.name] || agents[0]?.id || ""} onChange={(e) => setTarget({ ...target, [s.name]: e.target.value })}>
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
                <button className="acc" disabled={!on || has} onClick={() => add(s)}>{has ? <><ICheck size={14} /> Added</> : "Add to agent"}</button>
                <button className="ghost" aria-expanded={open === s.name} onClick={() => read(s.name)}>{open === s.name ? "Hide" : "Read"}</button>
              </div>
              {open === s.name && <div className="cat-body"><Markdown text={body} /></div>}
            </div>
          );
        })}
      </div>
      {shown.length === 0 && !err && <div className="empty">No skills match.</div>}
    </div>
  );
}
