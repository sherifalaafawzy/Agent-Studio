"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, Provider, usd } from "@/lib/api";

type Msg = { role: "user" | "bot" | "refused"; text: string; meta?: string };

export default function ChatPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [pid, setPid] = useState("mock");
  const [model, setModel] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<string | undefined>();
  const [offline, setOffline] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Provider[]>("/api/providers").then((p) => {
      setProviders(p); setOffline(false);
      const first = p.find((x) => x.enabled && x.id !== "mock") ?? p.find((x) => x.id === "mock");
      if (first) setPid(first.id);
    }).catch(() => setOffline(true));
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const prov = providers.find((p) => p.id === pid);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput(""); setBusy(true);
    setMsgs((m) => [...m, { role: "user", text }]);
    try {
      const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ provider: pid, model: model || undefined, message: text, session_id: session }) });
      setSession(r.session_id);
      setMsgs((m) => [...m, { role: "bot", text: r.reply, meta: `${r.provider}/${r.model} · ${r.usage.input_tokens} in / ${r.usage.output_tokens} out · ${usd(r.usage.cost_usd)} · spent ${usd(r.billing.spent_usd)} of ${usd(r.billing.cap_usd)}` }]);
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
        <select value={pid} onChange={(e) => { setPid(e.target.value); setModel(""); }}>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.label}{p.enabled ? "" : " (disabled)"}</option>)}
        </select>
        <input placeholder={`model (default: ${prov?.default_model ?? ""})`} value={model} onChange={(e) => setModel(e.target.value)} style={{ width: 260 }} />
        <button onClick={() => { setMsgs([]); setSession(undefined); }}>New chat</button>
        {offline && <span className="err">Backend offline — run <code>npm run dev</code></span>}
      </div>
      <div className="msgs">
        {msgs.length === 0 && <div className="mut">Pick a provider and say hello. Every call is checked against that provider&apos;s spend cap <b>before</b> it is sent. The Mock provider needs no keys and costs nothing.</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>{m.text}{m.meta && <div className="meta">{m.meta}</div>}</div>
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
