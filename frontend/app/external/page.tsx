"use client";
import { useEffect, useRef, useState } from "react";
import { API_BASE, api } from "@/lib/api";

type AcpAgent = { id: string; label: string; installed: boolean; enabled: boolean; notes: string; command: string };
type Mode = { id: string; name: string; description?: string };
type Ev = Record<string, any>;
type Block =
  | { k: "text"; text: string }
  | { k: "thought"; text: string }
  | { k: "tool"; id: string; title: string; kind?: string; status?: string; output?: string }
  | { k: "ask"; ev: Ev; decision?: string }          // permission | question | plan_approval
  | { k: "note"; text: string; bad?: boolean }
  | { k: "plan"; entries: { content: string; status: string }[] };
type Turn = { role: "user" | "agent"; text?: string; blocks?: Block[]; done?: Ev; error?: string };

function apply(blocks: Block[], ev: Ev): Block[] {
  const b = [...blocks];
  const last = b[b.length - 1];
  switch (ev.type) {
    case "text": case "thought": {
      const k = ev.type as "text" | "thought";
      if (last && last.k === k) b[b.length - 1] = { ...last, text: last.text + ev.text }; else b.push({ k, text: ev.text });
      break;
    }
    case "tool_call": b.push({ k: "tool", id: ev.id, title: ev.title, kind: ev.kind, status: ev.status }); break;
    case "tool_update": {
      const i = b.findIndex((x) => x.k === "tool" && x.id === ev.id);
      if (i >= 0) { const t = b[i] as Extract<Block, { k: "tool" }>; b[i] = { ...t, status: ev.status ?? t.status, title: ev.title ?? t.title, output: ev.output || t.output }; }
      break;
    }
    case "plan": b.push({ k: "plan", entries: ev.entries }); break;
    case "permission": case "question": case "plan_approval": b.push({ k: "ask", ev }); break;
    case "resolved": {
      const i = b.findIndex((x) => x.k === "ask" && x.ev.request_id === ev.request_id);
      if (i >= 0) b[i] = { ...(b[i] as Extract<Block, { k: "ask" }>), decision: ev.decision };
      break;
    }
    case "auto": b.push({ k: "note", text: `auto-${ev.decision}: ${ev.title} (${ev.kind}, policy ${ev.policy})`, bad: ev.decision === "rejected" }); break;
    case "fs_write": b.push({ k: "note", text: `wrote ${ev.path} (${ev.bytes} bytes, via workspace-jailed fs)` }); break;
    case "ext": b.push({ k: "note", text: `${ev.method}: ${String(ev.data).slice(0, 200)}` }); break;
  }
  return b;
}

function AskCard({ b, onAnswer }: { b: Extract<Block, { k: "ask" }>; onAnswer: (rid: string, body: Ev) => void }) {
  const ev = b.ev;
  const [sel, setSel] = useState<Record<string, string[]>>({});
  if (b.decision) return <div className="small mut">↳ {ev.title ?? ev.name ?? ev.type}: <b>{b.decision}</b></div>;
  if (ev.type === "permission") return (
    <div className="card" style={{ borderColor: "var(--warn)" }}>
      <b>Permission requested</b> <span className="pill">{ev.kind ?? "tool"}</span>
      <div className="small" style={{ margin: "6px 0", wordBreak: "break-word" }}>{ev.title}</div>
      {ev.input && <pre className="small mut" style={{ whiteSpace: "pre-wrap", maxHeight: 140, overflow: "auto" }}>{typeof ev.input === "string" ? ev.input : JSON.stringify(ev.input)}</pre>}
      <div className="row">
        {ev.options.map((o: Ev) => <button key={o.id} className={o.kind.startsWith("allow") ? "pri" : ""} onClick={() => onAnswer(ev.request_id, { option_id: o.id })}>{o.name}</button>)}
        <button onClick={() => onAnswer(ev.request_id, { cancel: true })}>Cancel</button>
      </div>
    </div>
  );
  if (ev.type === "question") return (
    <div className="card" style={{ borderColor: "var(--warn)" }}>
      <b>{ev.title ?? "The agent asks"}</b>
      {ev.questions.map((q: Ev) => (
        <div key={q.id} style={{ margin: "8px 0" }}><div>{q.prompt}</div>
          {q.options.map((o: Ev) => (
            <label key={o.id} style={{ flexDirection: "row", gap: 6, alignItems: "center" }}>
              <input type={q.allowMultiple ? "checkbox" : "radio"} name={q.id} checked={(sel[q.id] ?? []).includes(o.id)}
                onChange={(e) => setSel((s) => ({ ...s, [q.id]: q.allowMultiple ? (e.target.checked ? [...(s[q.id] ?? []), o.id] : (s[q.id] ?? []).filter((x) => x !== o.id)) : [o.id] }))} /> {o.label}
            </label>))}
        </div>))}
      <div className="row">
        <button className="pri" onClick={() => onAnswer(ev.request_id, { answers: Object.entries(sel).map(([questionId, selectedOptionIds]) => ({ questionId, selectedOptionIds })) })}>Answer</button>
        <button onClick={() => onAnswer(ev.request_id, { skip: true })}>Skip</button>
      </div>
    </div>
  );
  return (
    <div className="card" style={{ borderColor: "var(--warn)" }}>
      <b>Plan approval{ev.name ? `: ${ev.name}` : ""}</b>
      {ev.overview && <div className="small mut">{ev.overview}</div>}
      <pre className="small" style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto" }}>{ev.plan}</pre>
      <div className="row">
        <button className="pri" onClick={() => onAnswer(ev.request_id, { accepted: true })}>Accept plan</button>
        <button onClick={() => onAnswer(ev.request_id, { accepted: false, reason: "rejected by user" })}>Reject</button>
      </div>
    </div>
  );
}

export default function ExternalPage() {
  const [agents, setAgents] = useState<AcpAgent[]>([]);
  const [agent, setAgent] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [policy, setPolicy] = useState("ask");
  const [mode, setMode] = useState("");
  const [modes, setModes] = useState<Mode[]>([]);
  const [session, setSession] = useState<string | undefined>();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<AcpAgent[]>("/api/acp/agents").then((a) => { setAgents(a); const first = a.find((x) => x.installed && x.enabled); if (first) setAgent(first.id); })
      .catch((e) => setErr("Backend offline: " + e.message));
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const patchAgentTurn = (fn: (t: Turn) => Turn) => setTurns((ts) => { const c = [...ts]; c[c.length - 1] = fn(c[c.length - 1]); return c; });
  const reset = async () => {
    if (session) await api(`/api/acp/sessions/${session}`, { method: "DELETE" }).catch(() => {});
    setSession(undefined); setTurns([]); setModes([]); setMode("");
  };
  const stop = async () => { if (session) await api(`/api/acp/sessions/${session}/cancel`, { method: "POST" }).catch(() => {}); };
  const answer = async (rid: string, body: Ev) => { try { await api(`/api/acp/respond/${rid}`, { method: "POST", body: JSON.stringify(body) }); } catch (e) { setErr((e as Error).message); } };

  async function send() {
    const text = input.trim();
    if (!text || busy || !agent) return;
    setInput(""); setBusy(true); setErr("");
    setTurns((t) => [...t, { role: "user", text }, { role: "agent", blocks: [] }]);
    const ac = (abort.current = new AbortController());
    try {
      const res = await fetch(API_BASE + "/api/acp/chat", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ac.signal,
        body: JSON.stringify({ agent, message: text, session_id: session, workspace: workspace || undefined, policy, mode: mode || undefined }),
      });
      if (!res.ok || !res.body) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`);
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!chunk.startsWith("data: ")) continue;
          const ev: Ev = JSON.parse(chunk.slice(6));
          if (ev.type === "session") { setSession(ev.session_id); if (ev.modes) { setModes(ev.modes.available); setMode((m) => m || ev.modes.current); } }
          else if (ev.type === "done") patchAgentTurn((t) => ({ ...t, done: ev }));
          else if (ev.type === "error") patchAgentTurn((t) => ({ ...t, error: ev.message }));
          else if (ev.type === "usage") { /* shown in the footer from "done" */ }
          else patchAgentTurn((t) => ({ ...t, blocks: apply(t.blocks ?? [], ev) }));
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") patchAgentTurn((t) => ({ ...t, error: (e as Error).message }));
    } finally { setBusy(false); abort.current = null; }
  }

  const cur = agents.find((a) => a.id === agent);
  const dangerous = /bypass|yolo|dangerously/i.test(mode);
  return (
    <div className="chat">
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>External agents</h2>
        <span className="mut small">run Claude Code, Cursor, Antigravity, Grok Build over ACP · not covered by the spend caps</span>
      </div>
      <div className="row" style={{ marginBottom: 8 }}>
        <label>Agent
          <select value={agent} disabled={!!session} onChange={(e) => setAgent(e.target.value)}>
            {agents.map((a) => <option key={a.id} value={a.id} disabled={!a.installed || !a.enabled}>{a.label}{!a.installed ? " (not installed)" : !a.enabled ? " (disabled)" : ""}</option>)}
          </select></label>
        <label>Permissions
          <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
            <option value="ask">ask me for everything</option><option value="reads_auto">auto-allow reads, ask for the rest</option><option value="readonly">read-only (auto-reject non-reads)</option>
          </select></label>
        {modes.length > 0 && <label>Agent mode
          <select value={mode} onChange={(e) => setMode(e.target.value)}>{modes.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>}
        <label className="grow">Workspace (blank = default)
          <input value={workspace} disabled={!!session} placeholder="absolute path" onChange={(e) => setWorkspace(e.target.value)} /></label>
        <button onClick={reset} disabled={busy}>New session</button>
        {busy && <button onClick={stop}>Stop</button>}
      </div>
      {cur && !cur.installed && <p className="err small">`{cur.command}` not found on PATH. {cur.notes}</p>}
      {dangerous && <p className="err small">⚠ “{mode}” lets the agent act without asking you. Permission prompts will not appear and Agent Studio cannot sandbox that agent.</p>}
      {session && <div className="small mut" style={{ marginBottom: 6 }}>session {session} · the agent keeps its own context between messages</div>}
      {err && <p className="err small">{err}</p>}
      <div className="msgs">
        {turns.length === 0 && <p className="mut">Pick an agent and send a message. Its tool calls stream in below; anything that writes, executes or fetches asks you first (unless you chose otherwise).</p>}
        {turns.map((t, i) => t.role === "user" ? <div key={i} className="msg user">{t.text}</div> : (
          <div key={i} className="msg bot" style={{ maxWidth: "92%", width: "92%" }}>
            {(t.blocks ?? []).map((b, j) => {
              if (b.k === "text") return <div key={j} style={{ whiteSpace: "pre-wrap" }}>{b.text}</div>;
              if (b.k === "thought") return <details key={j}><summary className="small mut">thinking…</summary><div className="small mut" style={{ whiteSpace: "pre-wrap" }}>{b.text}</div></details>;
              if (b.k === "tool") return (
                <details key={j} style={{ margin: "4px 0" }}><summary className="small"><span className={`pill ${b.status === "completed" ? "ok" : b.status === "failed" ? "bad" : ""}`}>{b.status ?? "…"}</span> {b.kind ? `${b.kind}: ` : ""}{b.title}</summary>
                  {b.output && <pre className="small mut" style={{ whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}>{b.output}</pre>}</details>);
              if (b.k === "plan") return <div key={j} className="small">{b.entries.map((e, n) => <div key={n}>{e.status === "completed" ? "☑" : "☐"} {e.content}</div>)}</div>;
              if (b.k === "note") return <div key={j} className={`small ${b.bad ? "err" : "mut"}`}>• {b.text}</div>;
              return <AskCard key={j} b={b} onAnswer={answer} />;
            })}
            {t.error && <div className="err">⚠ {t.error}</div>}
            {!t.done && !t.error && busy && i === turns.length - 1 && <div className="mut small">working…</div>}
            {t.done && <div className="meta">{t.done.stop_reason} · {t.done.seconds}s · turn {t.done.turns}/{t.done.max_turns}
              {t.done.usage ? ` · ${t.done.usage.input_tokens} in / ${t.done.usage.output_tokens} out` : " · usage not reported"}
              {t.done.reported_cost != null ? ` · agent-reported cost ${t.done.currency ?? ""} ${Number(t.done.reported_cost).toFixed(4)}` : ""}
              {t.done.context ? ` · context ${t.done.context.used}/${t.done.context.size}` : ""} · not capped by Agent Studio</div>}
          </div>))}
        <div ref={end} />
      </div>
      <div className="row">
        <textarea className="grow" rows={2} value={input} placeholder="Message the agent (Enter to send, Shift+Enter for newline)" onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button className="pri" onClick={send} disabled={busy || !agent || !input.trim()}>Send</button>
      </div>
    </div>
  );
}
