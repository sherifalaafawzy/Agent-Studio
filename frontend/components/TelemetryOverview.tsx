"use client";
// The simple view of telemetry: what happened, what it cost, did anything go wrong, what is slow. Everything here is computed on the server
// (no model involved). Click a number to see the runs behind it; click a run to have it explained in plain words.
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api, usd } from "@/lib/api";
import InfoTip from "./InfoTip";

type Run = { trace_id: string; agent: string; agents: string[]; started: number; duration_ms: number; status: string; errors: number; cost_usd: number; tokens_in: number; tokens_out: number;
  model_calls: number; tool_calls: number; models: string[]; tools: Record<string, number> };
type Insights = {
  days: number; kpis: { runs: number; errors: number; error_rate: number; cost_usd: number; tokens_in: number; tokens_out: number; model_calls: number; tool_calls: number; p50_ms: number; p95_ms: number };
  trend: { day: string; runs: number; errors: number; cost_usd: number }[];
  top: { agents: { name: string; runs: number; errors: number; cost_usd: number }[]; models: { name: string; calls: number; cost_usd: number; tokens: number }[]; tools: { name: string; calls: number; errors: number; avg_ms: number }[] };
  errors: { trace_id: string; agent: string; what: string; cause: string; at: number }[]; anomalies: { kind: string; severity: string; trace_id: string | null; title: string; detail: string }[]; runs: Run[];
};
type Filters = { days: number; agent: string; status: string; tool: string; model: string };
type View = { name: string; filters: Filters };
const DEFAULT: Filters = { days: 7, agent: "", status: "", tool: "", model: "" };
const VIEWS_KEY = "as.telemetry.views";
const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
const when = (t: number) => new Date(t * 1000).toLocaleString();

function readViews(): View[] { try { return JSON.parse(localStorage.getItem(VIEWS_KEY) || "[]"); } catch { return []; } }
function writeViews(v: View[]) { try { localStorage.setItem(VIEWS_KEY, JSON.stringify(v)); } catch { /* private window: views just are not kept */ } }

function Kpi({ label, value, sub, onClick, info }: { label: string; value: string; sub?: string; onClick?: () => void; info: string }) {
  const body = <><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div>{sub && <div className="kpi-sub">{sub}</div>}</>;
  return onClick ? <button className="kpi" onClick={onClick} title={info}>{body}</button> : <div className="kpi" title={info}>{body}</div>;
}

/** Cost per day as bars, with the biggest day labelled directly and a table underneath for anyone who prefers numbers. */
function Trend({ data }: { data: Insights["trend"] }) {
  const max = Math.max(...data.map((d) => d.cost_usd), 0.000001);
  const best = data.reduce((a, d) => (d.cost_usd > a.cost_usd ? d : a), data[0]);
  const w = 100 / data.length;
  const bar = Math.min(w * 0.7, 4);                                // thin marks: seven days must not turn into seven slabs
  return (
    <div>
      <svg viewBox="0 0 100 32" role="img" aria-label={`Cost per day. Highest: ${usd(best.cost_usd)} on ${best.day}`} style={{ width: "100%", height: 120 }} preserveAspectRatio="none">
        <line x1={0} x2={100} y1={30.2} y2={30.2} className="viz-axis" vectorEffect="non-scaling-stroke" />
        {data.map((d, i) => <rect key={d.day} x={i * w + (w - bar) / 2} width={bar} y={30 - (d.cost_usd / max) * 26} height={Math.max(0.4, (d.cost_usd / max) * 26)}
          fill={d.errors ? "var(--bad)" : "var(--viz-1)"}><title>{`${d.day}: ${usd(d.cost_usd)}, ${d.runs} runs, ${d.errors} failed`}</title></rect>)}
      </svg>
      <div className="small mut">Highest day: <b>{usd(best.cost_usd)}</b> on {best.day}. Red bars are days with failed runs.</div>
      <details><summary className="small">Show as a table</summary>
        <table><thead><tr><th>Day</th><th>Runs</th><th>Failed</th><th>Cost</th></tr></thead><tbody>
          {data.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.runs}</td><td>{d.errors || ""}</td><td>{usd(d.cost_usd)}</td></tr>)}</tbody></table></details>
    </div>
  );
}

export default function TelemetryOverview() {
  const [f, setF] = useState<Filters>(DEFAULT);
  const [data, setData] = useState<Insights | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(true);
  const [explain, setExplain] = useState<{ trace_id: string; summary: string[] } | null>(null);
  const [pick, setPick] = useState<string[]>([]);
  const [views, setViews] = useState<View[]>([]);
  const [viewName, setViewName] = useState("");

  const qs = (extra: Record<string, string> = {}) => new URLSearchParams({ days: String(f.days), ...(f.agent ? { agent: f.agent } : {}), ...(f.status ? { status: f.status } : {}),
    ...(f.tool ? { tool: f.tool } : {}), ...(f.model ? { model: f.model } : {}), ...extra }).toString();
  const load = useCallback(() => api<Insights>(`/api/telemetry/insights?${qs()}`).then((d) => { setData(d); setErr(""); }).catch((e) => setErr(e.message)), [f]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setViews(readViews()); }, []);

  const why = async (id: string) => { try { setExplain(await api(`/api/telemetry/runs/${id}/explain`)); } catch (e) { setErr((e as Error).message); } };
  const saveView = () => { const v = [...views.filter((x) => x.name !== viewName.trim()), { name: viewName.trim(), filters: f }]; setViews(v); writeViews(v); setViewName(""); };
  const dropView = (name: string) => { const v = views.filter((x) => x.name !== name); setViews(v); writeViews(v); };
  const togglePick = (id: string) => setPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));

  const k = data?.kpis;
  const chosen = data ? pick.map((id) => data.runs.find((r) => r.trace_id === id)).filter(Boolean) as Run[] : [];

  return (
    <div className="card" data-tour="telemetry-overview">
      <div className="card-head"><b>Overview</b><InfoTip id="telemetry-kpi" /><span className="grow" />
        <button className="ghost small" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Show"}</button></div>
      {err && <p className="err small" role="alert">{err}</p>}
      {open && <>
        <div className="row" style={{ flexWrap: "wrap", marginBottom: 8 }}>
          <label>Period<select value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })}>{[1, 7, 30, 90].map((d) => <option key={d} value={d}>{d === 1 ? "last day" : `last ${d} days`}</option>)}</select></label>
          <label>Agent<select value={f.agent} onChange={(e) => setF({ ...f, agent: e.target.value })}><option value="">all</option>{data?.top.agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}</select></label>
          <label>Outcome<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">all</option><option value="ok">worked</option><option value="error">failed</option></select></label>
          <label>Model<select value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })}><option value="">all</option>{data?.top.models.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}</select></label>
          <label>Tool<select value={f.tool} onChange={(e) => setF({ ...f, tool: e.target.value })}><option value="">all</option>{data?.top.tools.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}</select></label>
          <button className="ghost small" onClick={() => setF(DEFAULT)}>Clear filters</button>
        </div>
        <div className="row small" style={{ flexWrap: "wrap", marginBottom: 8 }}>
          <input aria-label="Name this view" placeholder="Save these filters as…" value={viewName} onChange={(e) => setViewName(e.target.value)} />
          <button className="small" disabled={!viewName.trim()} onClick={saveView}>Save view</button>
          {views.map((v) => <span key={v.name} className="pill"><button className="ghost small" onClick={() => setF(v.filters)}>{v.name}</button><button className="ghost small" aria-label={`Delete view ${v.name}`} onClick={() => dropView(v.name)}>✕</button></span>)}
          <span className="grow" />
          <a className="small" href={`${API_BASE}/api/telemetry/export?${qs({ format: "csv" })}`}>Export CSV</a>
          <a className="small" href={`${API_BASE}/api/telemetry/export?${qs({ format: "json" })}`}>Export JSON</a>
        </div>

        {k && <div className="kpis">
          <Kpi label="Runs" value={String(k.runs)} sub={`${k.model_calls} model calls · ${k.tool_calls} tool calls`} info="All runs in this period. Click to clear the outcome filter." onClick={() => setF({ ...f, status: "" })} />
          <Kpi label="Failed runs" value={String(k.errors)} sub={`${Math.round(k.error_rate * 100)}% of runs`} info="Runs where something went wrong. Click to see only these." onClick={() => setF({ ...f, status: "error" })} />
          <Kpi label="Cost" value={usd(k.cost_usd)} sub={`${(k.tokens_in + k.tokens_out).toLocaleString()} tokens`} info="Estimated from the provider's prices. Local models are free." />
          <Kpi label="Typical run" value={fmtMs(k.p50_ms)} sub={`slowest 1 in 20: ${fmtMs(k.p95_ms)}`} info="Half of the runs finish faster than this." />
        </div>}
        {data && k && k.runs === 0 && <div className="empty">No runs in this period{f.agent || f.status || f.model || f.tool ? " with these filters" : ""} yet.</div>}
        {data && k && k.runs > 0 && <>
          <div className="lbl">Cost per day</div><Trend data={data.trend} />
          <div className="lbl" style={{ marginTop: 14 }}>Worth a look <InfoTip id="telemetry-anomaly" /></div>
          {data.anomalies.length === 0 && <div className="small mut">Nothing unusual.</div>}
          {data.anomalies.map((a, i) => <div key={i} className="small" style={{ padding: "4px 0" }}><span className={`pill ${a.severity === "bad" ? "bad" : ""}`}>{a.title}</span> {a.detail}
            {a.trace_id && <> <button className="ghost small" onClick={() => why(a.trace_id as string)}>Explain</button></>}</div>)}
          <div className="grid tel-top" style={{ marginTop: 14 }}>
            <div><div className="lbl">Top agents</div><table><tbody>{data.top.agents.map((a) => <tr key={a.name}><td>{a.name}</td><td>{a.runs} runs</td><td>{a.errors ? `${a.errors} failed` : ""}</td><td>{usd(a.cost_usd)}</td></tr>)}</tbody></table></div>
            <div><div className="lbl">Top models</div><table><tbody>{data.top.models.map((m) => <tr key={m.name}><td>{m.name}</td><td>{m.calls} calls</td><td>{usd(m.cost_usd)}</td></tr>)}</tbody></table></div>
            <div><div className="lbl">Top tools</div><table><tbody>{data.top.tools.map((t) => <tr key={t.name}><td>{t.name}</td><td>{t.calls} calls</td><td>{t.errors ? `${t.errors} failed` : ""}</td><td>{fmtMs(t.avg_ms)} avg</td></tr>)}</tbody></table></div>
          </div>
          {data.errors.length > 0 && <><div className="lbl" style={{ marginTop: 14 }}>What went wrong</div>
            {data.errors.slice(0, 6).map((e, i) => <div key={i} className="small" style={{ padding: "3px 0" }}><b>{e.what}</b> by {e.agent}: {e.cause} <button className="ghost small" onClick={() => why(e.trace_id)}>Explain</button></div>)}</>}

          <div className="lbl" style={{ marginTop: 14 }}>Runs <span className="mut small">(tick two to compare)</span></div>
          <table><thead><tr><th /><th>When</th><th>Agent</th><th>Outcome</th><th>Time</th><th>Cost</th><th /></tr></thead><tbody>
            {data.runs.map((r) => <tr key={r.trace_id}><td><input type="checkbox" aria-label={`Compare run ${r.trace_id.slice(0, 8)}`} checked={pick.includes(r.trace_id)} onChange={() => togglePick(r.trace_id)} /></td>
              <td className="mut">{when(r.started)}</td><td>{r.agent}</td><td>{r.status === "error" ? <span className="pill bad">failed</span> : "worked"}</td><td>{fmtMs(r.duration_ms)}</td><td>{usd(r.cost_usd)}</td>
              <td><button className="ghost small" onClick={() => why(r.trace_id)}>Explain</button></td></tr>)}</tbody></table>
          {chosen.length === 2 && <table style={{ marginTop: 8 }} aria-label="Comparison"><thead><tr><th /><th>{chosen[0].trace_id.slice(0, 8)}</th><th>{chosen[1].trace_id.slice(0, 8)}</th></tr></thead><tbody>
            {([["Agent", (r: Run) => r.agent], ["Outcome", (r: Run) => r.status], ["Time", (r: Run) => fmtMs(r.duration_ms)], ["Cost", (r: Run) => usd(r.cost_usd)], ["Tokens", (r: Run) => (r.tokens_in + r.tokens_out).toLocaleString()],
              ["Model calls", (r: Run) => String(r.model_calls)], ["Tool calls", (r: Run) => String(r.tool_calls)]] as [string, (r: Run) => string][]).map(([label, fn]) => <tr key={label}><td>{label}</td><td>{fn(chosen[0])}</td><td>{fn(chosen[1])}</td></tr>)}</tbody></table>}
          {explain && <div className="card" style={{ marginTop: 8 }} role="region" aria-label="Run explained"><div className="row"><b>What happened in this run</b><span className="grow" /><button className="ghost small" onClick={() => setExplain(null)}>Close</button></div>
            {explain.summary.map((l, i) => <p key={i} className="small" style={{ margin: "4px 0" }}>{l}</p>)}</div>}
        </>}
      </>}
    </div>
  );
}
