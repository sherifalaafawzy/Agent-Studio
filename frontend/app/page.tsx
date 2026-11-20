"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, Provider, usd } from "@/lib/api";

type Step = { agent: string; type: "call" | "result"; tool: string; args?: string; result?: string };
type Call = { agent: string; provider: string; model: string; cost_usd: number; input_tokens: number; output_tokens: number };
type Msg = { role: "user" | "bot" | "refused"; text: string; meta?: string; steps?: Step[]; calls?: Call[]; warnings?: string[] };
type Agent = { id: string; name: string; tier: string; description: string; sub_agents: string[] };

export default function ChatPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [pid, setPid] = useState("mock");
  const [agent, setAgent] = useState("");
  const [model, setModel] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<string | undefined>();
  const [offline, setOffline] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    Promise.all([api<Provider[]>("/api/providers"), api<Agent[]>("/api/agents")]).then(([p, a]) => {
      setProviders(p); setAgents(a); setOffline(false);
      const first = p.find((x) => x.enabled && x.id !== "mock") ?? p.find((x) => x.id === "mock");
      if (first) setPid(first.id);
    }).catch(() => setOffline(true));
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const prov = providers.find((p) => p.id === pid);
  const ag = agents.find((a) => a.id === agent);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput(""); setBusy(true);
    setMsgs((m) => [...m, { role: "user", text }]);
    try {
      const r = await api("/api/chat", { method: "POST", body: JSON.stringify({
        provider: pid, agent: agent || undefined, model: model || undefined, workspace: workspace || undefined, message: text, session_id: session }) });
      setSession(r.session_id);
      const meta = `${r.agent === "plain" ? "plain chat" : r.agent} on ${r.provider} · ${r.model_calls.length} model call${r.model_calls.length === 1 ? "" : "s"} · ${r.usage.input_tokens} in / ${r.usage.output_tokens} out · ${usd(r.usage.cost_usd)} · spent ${usd(r.billing.spent_usd)} of ${usd(r.billing.cap_usd)}`;
      setMsgs((m) => [...m, { role: "bot", text: r.reply || "(no final reply)", meta, steps: r.steps, calls: r.model_calls, warnings: r.warnings },
        ...(r.halted ? [{ role: "refused" as const, text: "⛔ Turn stopped early: " + r.halted.message, meta: "partial progress above is real; nothing further was sent to any provider" }] : [])]);
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) {
        const d = e.detail;
        setMsgs((m) => [...m, { role: "refused", text: "⛔ " + d.message, meta: `projected ${usd(d.projected_usd)} · remaining ${usd(d.remaining_usd)} · nothing was sent to the provider` }]);
      } else {
        setMsgs((m) => [...m, { role: "refused", text: "Error: " + (e as Error).message }]);
      }
    } finally { setBusy(false); }
  }

  return (
    <div className="chat">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>Chat</h2>
        <select value={agent} onChange={(e) => setAgent(e.target.value)} title="Agent">
          <option value="">Plain chat (no tools)</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name} [{a.tier}]{a.sub_agents.length ? ` +${a.sub_agents.length} sub` : ""}</option>)}
        </select>
        <select value={pid} onChange={(e) => { setPid(e.target.value); setModel(""); }} title="Provider (tiers resolve against it)">
          {providers.map((p) => <option key={p.id} value={p.id}>{p.label}{p.enabled ? "" : " (disabled)"}</option>)}
        </select>
        <input placeholder={agent ? "model override (root only)" : `model (default: ${prov?.default_model ?? ""})`} value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 210 }} />
        <input placeholder="workspace dir (optional)" value={workspace} onChange={(e) => setWorkspace(e.target.value)} style={{ width: 200 }} />
        <button onClick={() => { setMsgs([]); setSession(undefined); }}>New chat</button>
        {offline && <span className="err">Backend offline — run <code>npm run dev</code></span>}
      </div>
      {ag && <div className="mut small" style={{ marginBottom: 8 }}>{ag.description}</div>}
      <div className="msgs">
        {msgs.length === 0 && <div className="mut">Pick an agent and a provider. Every model call by every agent (including sub-agents) is checked against that provider&apos;s cap <b>before</b> it is sent. The Mock provider needs no keys; script it with lines like <code>!tool list_dir {"{}"}</code> or <code>!agent explorer {'{"request":"…"}'}</code>.</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.text}
            {m.meta && <div className="meta">{m.meta}</div>}
            {m.warnings?.map((w, j) => <div key={j} className="meta" style={{ color: "var(--warn)" }}>⚠ {w}</div>)}
            {(m.steps?.length || m.calls?.length) ? (
              <details style={{ marginTop: 6 }}>
                <summary className="meta">trace: {m.steps?.filter((s) => s.type === "call").length ?? 0} tool calls · {m.calls?.length ?? 0} model calls</summary>
                {m.steps?.map((s, j) => (
                  <div key={j} className="meta" style={{ fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
                    {s.type === "call" ? `▶ ${s.agent} → ${s.tool}(${s.args})` : `  ◀ ${s.tool}: ${s.result}`}
                  </div>
                ))}
                {m.calls?.map((c, j) => <div key={"c" + j} className="meta">$ {c.agent} · {c.provider}/{c.model} · {c.input_tokens}/{c.output_tokens} tok · {usd(c.cost_usd)}</div>)}
              </details>
            ) : null}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="row">
        <textarea className="grow" rows={2} value={input} placeholder="Message…" onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="pri" disabled={busy || !input.trim()} onClick={send}>{busy ? "…" : "Send"}</button>
      </div>
    </div>
  );
}
