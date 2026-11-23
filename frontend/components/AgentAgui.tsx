"use client";
// In the agent editor and the wizard: the two AG-UI switches of one agent.
//   components: the agent may show charts, tables, maps and cards (and is taught how). Off: plain Markdown answers, fewer instruction tokens.
//   endpoint:   external AG-UI clients may run this agent through POST /api/agui. Off: the endpoint refuses it. Agent Studio's own chat is not affected.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "./InfoTip";

export type AguiFlags = { components: boolean; endpoint: boolean };
export const AGUI_ON: AguiFlags = { components: true, endpoint: true };

/** The flags of an agent as stored, with anything missing treated as on (older agents). */
export const aguiOf = (a: { agui?: Partial<AguiFlags> | null } | undefined): AguiFlags => ({ ...AGUI_ON, ...(a?.agui ?? {}) });

/** A short label for the agents list when something is switched off; null when everything is on. */
export function aguiBadge(a: { agui?: Partial<AguiFlags> | null }): string | null {
  const f = aguiOf(a);
  if (!f.components && !f.endpoint) return "AG-UI off";
  if (!f.components) return "no components";
  if (!f.endpoint) return "no AG-UI stream";
  return null;
}

export default function AgentAgui({ agent, isNew, onChange, compact }: { agent: { agui?: Partial<AguiFlags> | null }; isNew?: boolean; onChange: (agui: AguiFlags) => void; compact?: boolean }) {
  const [dflt, setDflt] = useState<AguiFlags>(AGUI_ON);                // what a new agent starts with (Settings > Rich components)
  useEffect(() => {
    if (!isNew || agent.agui) return;
    api<{ new_agents?: Partial<AguiFlags> }>("/api/uikit/config").then((c) => setDflt({ ...AGUI_ON, ...(c.new_agents ?? {}) })).catch(() => setDflt(AGUI_ON));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isNew]);
  const f = agent.agui ? aguiOf(agent) : dflt;
  const set = (patch: Partial<AguiFlags>) => onChange({ ...f, ...patch });
  return (
    <div className={compact ? "" : "card"} id="ag-agui" data-testid="agent-agui" style={compact ? { marginTop: 10 } : { marginTop: 14 }}>
      <div className="card-head"><b>AG-UI</b><InfoTip id="agui-agent" /></div>
      <div className="small mut">AG-UI is how an agent shows rich answers and how other apps can watch it work. Choose what this agent may do.</div>
      <div style={{ marginTop: 8 }}>
        <label className="check"><input type="checkbox" checked={f.components} onChange={(e) => set({ components: e.target.checked })} aria-label="Rich components in answers" /> Rich components in answers</label>
        <div className="small mut" style={{ marginLeft: 24 }}>
          {f.components
            ? "It can show charts, tables, maps and cards, and is told how."
            : "Switched off: it cannot show charts, maps or cards and is not taught about them (this saves instruction tokens). Its answers are plain Markdown."}
        </div>
      </div>
      <div style={{ marginTop: 8 }}>
        <label className="check"><input type="checkbox" checked={f.endpoint} onChange={(e) => set({ endpoint: e.target.checked })} aria-label="AG-UI stream for other apps" /> AG-UI stream for other apps</label>
        <div className="small mut" style={{ marginLeft: 24 }}>
          {f.endpoint
            ? <>Other AG-UI clients may run this agent through <code>POST /api/agui</code>.</>
            : <>Switched off: <code>/api/agui</code> refuses this agent. Agent Studio&apos;s own chat still works.</>}
        </div>
      </div>
    </div>
  );
}
