"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import InfoTip from "@/components/InfoTip";
import TelemetryOverview from "@/components/TelemetryOverview";

type TraceRow = { trace_id: string; root: string | null; spans: number; errors: number; duration_ms: number | null; sources: string[]; last: number };
type Rec = { id: number; ts: number; signal: string; source: string; name: string; trace_id: string; span_id: string; parent_id: string; start_ns: number; end_ns: number;
  duration_ms: number | null; status: string; attrs: Record<string, any>; body: any };

const ago = (ts: number) => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`; };

function Waterfall({ spans }: { spans: Rec[] }) {
  const [sel, setSel] = useState<Rec | null>(null);
  const by = Object.fromEntries(spans.map((s) => [s.span_id, s]));
  const t0 = Math.min(...spans.map((s) => s.start_ns)), t1 = Math.max(...spans.map((s) => s.end_ns || s.start_ns)), span = Math.max(1, t1 - t0);
  const depth = (s: Rec) => { let d = 0, p = s.parent_id; while (p && by[p] && d < 8) { d++; p = by[p].parent_id; } return d; };
  return (
    <div className="card">
      {spans.map((s) => (
        <div key={s.id} onClick={() => setSel(s)} style={{ display: "grid", gridTemplateColumns: "minmax(180px,34%) 1fr 70px", gap: 8, alignItems: "center", cursor: "pointer", padding: "2px 0" }}>
          <div className="small" style={{ paddingLeft: depth(s) * 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            <span className={`pill ${s.status === "error" ? "bad" : "ok"}`} style={{ marginRight: 4 }}>{s.source}</span>{s.name}</div>
          <div style={{ position: "relative", height: 10, background: "var(--panel2)", borderRadius: 4 }}>
            <div style={{ position: "absolute", left: `${((s.start_ns - t0) / span) * 100}%`, width: `${Math.max(0.6, (((s.end_ns || s.start_ns) - s.start_ns) / span) * 100)}%`, top: 0, bottom: 0,
              background: s.status === "error" ? "var(--bad)" : "var(--accent, #5b8def)", borderRadius: 4 }} />
          </div>
          <div className="small mut">{s.duration_ms ?? "—"} ms</div>
        </div>))}
      {sel && <div style={{ marginTop: 10 }}><b>{sel.name}</b> <span className="mut small">{sel.source} · span {sel.span_id} · {sel.duration_ms} ms · {sel.status}</span>
        <pre className="small" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{JSON.stringify(sel.attrs, null, 2)}</pre></div>}
    </div>
  );
}

export default function TelemetryPage() {
  const [tab, setTab] = useState<"trace" | "metric" | "log">("trace");
  const [source, setSource] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  const [traces, setTraces] = useState<TraceRow[]>([]);
  const [recs, setRecs] = useState<Rec[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [spans, setSpans] = useState<Rec[]>([]);
  const [live, setLive] = useState(true);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const q = source ? `&source=${encodeURIComponent(source)}` : "";
      const sum = await api<{ by_source: { source: string }[] }>("/api/otel/summary");
      setSources([...new Set(sum.by_source.map((x) => x.source))]);
      if (tab === "trace") setTraces(await api<TraceRow[]>(`/api/otel/traces?limit=60${q}`));
      else setRecs(await api<Rec[]>(`/api/otel/records?signal=${tab}&limit=200${q}`));
      if (open) setSpans(await api<Rec[]>(`/api/otel/traces/${open}`).catch(() => []));
      setErr("");
    } catch (e) { setErr("Backend offline: " + (e as Error).message); }
  }, [tab, source, open]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!live) return; const t = setInterval(load, 3000); return () => clearInterval(t); }, [live, load]);

  return (
    <div>
      <div className="page-head"><h2>Telemetry</h2><InfoTip id="telemetry" /></div>
      <TelemetryOverview />
      <div className="row" style={{ marginBottom: 6 }}>
        {(["trace", "metric", "log"] as const).map((t) => <button key={t} className={tab === t ? "pri" : ""} onClick={() => { setTab(t); setOpen(null); }}>{t === "trace" ? "Traces" : t === "metric" ? "Metrics" : "Logs"}</button>)}
        <select value={source} onChange={(e) => setSource(e.target.value)}><option value="">all agents</option>{sources.map((s) => <option key={s} value={s}>{s}</option>)}</select>
        <label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> live</label>
        <a href="/settings/" style={{ textDecoration: "underline" }} className="small">connector settings</a>
        {err && <span className="err">{err}</span>}
      </div>
      <p className="mut small">Everything stored by the OpenTelemetry connector, from all agents — Agent Studio teams and agent-created sub-agents (span tree: turn → agent → model/tool calls) and external agents (a span per turn / tool call, plus whatever they export).</p>

      {tab === "trace" && (open ? (
        <div><button onClick={() => { setOpen(null); setSpans([]); }}>← all traces</button> <span className="mut small">trace {open}</span>
          {spans.length > 0 && <Waterfall spans={spans} />}</div>
      ) : (
        <div className="card"><table><thead><tr><th>Root</th><th>Agents</th><th>Spans</th><th>Errors</th><th>Duration</th><th>When</th></tr></thead><tbody>
          {traces.map((t) => <tr key={t.trace_id} style={{ cursor: "pointer" }} onClick={() => setOpen(t.trace_id)}>
            <td>{t.root ?? "(no root)"}</td><td>{t.sources.join(", ")}</td><td>{t.spans}</td><td>{t.errors || ""}</td><td>{t.duration_ms ?? "—"} ms</td><td className="mut">{ago(t.last)}</td></tr>)}
          {traces.length === 0 && <tr><td colSpan={6} className="mut">No traces yet — run a chat or an external agent.</td></tr>}</tbody></table></div>
      ))}

      {tab !== "trace" && (
        <div className="card"><table><thead><tr><th>When</th><th>Agent</th><th>{tab === "metric" ? "Metric" : "Event"}</th><th>{tab === "metric" ? "Value" : "Body"}</th><th>Attributes</th></tr></thead><tbody>
          {recs.map((r) => <tr key={r.id}><td className="mut">{ago(r.ts)}</td><td>{r.source}</td><td>{r.name}</td>
            <td>{tab === "metric" ? `${r.body?.value ?? ""} ${r.body?.unit ?? ""}` : String(r.body ?? "").slice(0, 120)}</td>
            <td className="mut small" style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{JSON.stringify(r.attrs)}</td></tr>)}
          {recs.length === 0 && <tr><td colSpan={5} className="mut">Nothing yet. External agents only send {tab}s if they export OpenTelemetry — check Settings → Telemetry.</td></tr>}</tbody></table></div>
      )}
    </div>
  );
}
