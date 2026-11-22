"use client";
// Context window meter (like Claude's): how full the model's window is, what is in it, and "Summarize context".
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, usd } from "@/lib/api";
import { toast } from "./Nav";

export type CtxInfo = { model: string | null; window_tokens: number; used_tokens: number; percent: number; level: "ok" | "warn" | "critical";
  source: string; messages: number; summarized: boolean; summarized_messages: number; messages_after_summary: number };

const fmt = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

export function useContext(sid: string | undefined, refreshKey: unknown) {
  const [info, setInfo] = useState<CtxInfo | null>(null);
  const load = useCallback(() => { if (sid) api<CtxInfo>(`/api/chat/sessions/${sid}/context`).then(setInfo).catch(() => setInfo(null)); else setInfo(null); }, [sid]);
  useEffect(() => { load(); }, [load, refreshKey]);
  return { info, reload: load };
}

export default function ContextMeter({ sid, info, busy, onSummarized }: { sid?: string; info: CtxInfo | null; busy: boolean; onSummarized: () => void }) {
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const on = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, []);
  if (!sid || !info) return null;
  const pct = Math.min(100, info.percent);
  const color = info.level === "critical" ? "var(--bad)" : info.level === "warn" ? "var(--warn)" : "var(--viz-1, #2a78d6)";
  const r = 9, c = 2 * Math.PI * r;
  async function summarize() {
    setWorking(true);
    try {
      const res = await api<{ covered_messages: number; cost_usd: number }>(`/api/chat/sessions/${sid}/summarize`, { method: "POST" });
      toast(`Summarised ${res.covered_messages} messages (${usd(res.cost_usd)}). The chat now continues from the summary.`);
      setOpen(false);
      onSummarized();
    } catch (e) {
      toast(e instanceof ApiError ? (typeof e.detail === "string" ? e.detail : e.message) : (e as Error).message, "err");
    }
    setWorking(false);
  }
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className="ctx-btn" data-tour="context-meter" onClick={() => setOpen((v) => !v)} title={`Context: ${pct.toFixed(0)}% of the window used`} aria-label="Context window usage">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <circle cx="11" cy="11" r={r} fill="none" stroke="var(--line2)" strokeWidth="2.5" />
          <circle cx="11" cy="11" r={r} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round"
            strokeDasharray={`${(pct / 100) * c} ${c}`} transform="rotate(-90 11 11)" />
        </svg>
        <span className="ctx-pct" style={{ color: info.level === "ok" ? "var(--mut)" : color }}>{pct.toFixed(0)}%</span>
      </button>
      {open && (
        <div className="ctx-pop" role="dialog" aria-label="Context window">
          <div className="row" style={{ justifyContent: "space-between" }}><b>Context window</b><span className="small mut">{info.model}</span></div>
          <div className="ctx-bar"><i className={info.level} style={{ width: `${pct}%` }} /></div>
          <div className="ctx-nums"><span><b>{fmt(info.used_tokens)}</b> used</span><span className="mut">of {fmt(info.window_tokens)} tokens</span><span className="mut">{pct.toFixed(1)}%</span></div>
          <div className="small mut" style={{ marginTop: 4 }}>{info.source}. Everything the model reads each turn counts: your messages, its replies, tool results, and the system prompt.</div>
          <div className="ctx-kv">
            <span>Messages</span><span>{info.messages}</span>
            <span>Summary</span><span>{info.summarized ? `${info.summarized_messages} messages condensed` : "none yet"}</span>
            <span>Since summary</span><span>{info.messages_after_summary}</span>
          </div>
          {info.level !== "ok" && <div className={`ctx-tip ${info.level}`}>
            {info.level === "critical" ? "Nearly full. Summarise now so the next replies stay sharp and nothing is cut off." : "Getting full. Summarising keeps the chat fast and cheap."}</div>}
          <div className="row" style={{ marginTop: 10, justifyContent: "space-between" }}>
            <span className="small mut">Keeps the full chat visible; the model resumes from a summary.</span>
            <button className="acc small" disabled={busy || working || info.messages_after_summary === 0} onClick={summarize}>
              {working ? "Summarising…" : "Summarize context"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
