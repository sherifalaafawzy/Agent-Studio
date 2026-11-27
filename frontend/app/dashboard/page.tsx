"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, usd } from "@/lib/api";
import InfoTip from "@/components/InfoTip";

type Day = { day: string; cost_usd: number; calls: number; input_tokens: number; output_tokens: number };
type Prov = { provider: string; label: string; kind: string; enabled: boolean; cap_usd: number; cap_period: string; spent_usd: number;
  remaining_usd: number; pct_used: number; calls: number; refused: number; auth_ready: boolean;
  credits: { remaining_usd: number | null; mode: string; source: string | null } };
type Ext = { id: string; label: string; installed: boolean; enabled: boolean; month: { turns: number; minutes: number; reported_cost: number | null; cost_reported_turns: number };
  month_turn_cap: number; month_minutes_cap: number; month_cost_cap: number };
type Dash = {
  kpis: { month_spent_usd: number; month_calls: number; month_refused: number; credits_left_usd: number | null; credits_tracked: number; turns_7d: number; running: number; external_turns_month: number };
  daily: Day[]; providers: Prov[]; external: Ext[];
  recent: { id: string; title: string; agent: string | null; provider: string; updated: number; messages: number; running: boolean }[];
  health: { providers_ready: number; providers_enabled: number; mcp_ready: number; mcp_total: number; agents: number; skills: number; otel_records: number; external_installed: number; external_total: number };
};

const ago = (ts: number) => { const s = Date.now() / 1000 - ts; return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)}m ago` : s < 172800 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`; };
const money = (n: number) => (n >= 100 ? `$${n.toFixed(0)}` : n >= 1 ? `$${n.toFixed(2)}` : usd(n));

function Kpi({ label, value, sub, href }: { label: string; value: string; sub?: string; href?: string }) {
  const body = <><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div>{sub && <div className="kpi-sub">{sub}</div>}</>;
  return href ? <Link href={href} className="kpi">{body}</Link> : <div className="kpi">{body}</div>;
}

/** Daily spend: one series, so no legend (the title names it); hover tooltip per bar; table view for exact values. */
function SpendChart({ days }: { days: Day[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const max = Math.max(...days.map((d) => d.cost_usd), 0);
  const W = 720, H = 200, L = 56, B = 22, T = 10, R = 6;
  const bw = (W - L - R) / days.length;
  const nice = (v: number) => { if (v <= 0) return 0.01; const e = 10 ** Math.floor(Math.log10(v)); return [1, 2, 2.5, 5, 10].map((m) => m * e).find((x) => x >= v)!; };
  const top = nice(max);
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const ticks = [0, top / 2, top];
  return (
    <div className="card dash-chart">
      <div className="row"><b>Model spend per day</b><span className="mut small">last {days.length} days · all providers · capped calls</span><span className="grow" />
        <div className="seg"><button className={!table ? "on" : ""} onClick={() => setTable(false)}>Chart</button><button className={table ? "on" : ""} onClick={() => setTable(true)}>Table</button></div></div>
      {table ? (
        <div style={{ maxHeight: 240, overflow: "auto", marginTop: 8 }}><table><thead><tr><th>Day</th><th>Spend</th><th>Calls</th><th>Tokens in → out</th></tr></thead>
          <tbody>{[...days].reverse().map((d) => <tr key={d.day}><td>{d.day}</td><td>{usd(d.cost_usd)}</td><td>{d.calls}</td><td>{d.input_tokens.toLocaleString()} → {d.output_tokens.toLocaleString()}</td></tr>)}</tbody></table></div>
      ) : (
        <div style={{ position: "relative" }}>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Model spend per day" onMouseLeave={() => setHover(null)}>
            {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="viz-grid" /><text x={L - 6} y={y(t) + 3} textAnchor="end" className="viz-tick">{money(t)}</text></g>)}
            {days.map((d, i) => {
              const h = Math.max(0, H - B - y(d.cost_usd));
              const x = L + i * bw + 1, w = Math.max(1, bw - 2);                       // 2px surface gap between bars
              return <g key={d.day} onMouseEnter={() => setHover(i)}>
                <rect x={L + i * bw} y={T} width={bw} height={H - T - B} fill="transparent" />{/* hit target bigger than the mark */}
                {d.cost_usd > 0 && <path className={`viz-bar ${hover === i ? "on" : ""}`}
                  d={`M${x},${H - B} V${H - B - h + Math.min(4, h)} Q${x},${H - B - h} ${x + Math.min(4, w / 2)},${H - B - h} H${x + w - Math.min(4, w / 2)} Q${x + w},${H - B - h} ${x + w},${H - B - h + Math.min(4, h)} V${H - B} Z`} />}
              </g>;
            })}
            <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="viz-axis" />
            {[0, Math.floor(days.length / 2), days.length - 1].map((i) => <text key={i} x={L + i * bw + bw / 2} y={H - 6} textAnchor="middle" className="viz-tick">{days[i]?.day.slice(5)}</text>)}
          </svg>
          {hover !== null && days[hover] && (
            <div className="viz-tip" style={{ left: `${((L + hover * bw + bw / 2) / W) * 100}%` }}>
              <b>{days[hover].day}</b><div>{usd(days[hover].cost_usd)} · {days[hover].calls} call{days[hover].calls === 1 ? "" : "s"}</div>
              <div className="mut">{days[hover].input_tokens.toLocaleString()} → {days[hover].output_tokens.toLocaleString()} tokens</div></div>)}
          {max === 0 && <div className="small mut" style={{ textAlign: "center", marginTop: -110, marginBottom: 90 }}>No model spend in this period.</div>}
        </div>
      )}
    </div>
  );
}

/** Meter list: spend against each provider's own cap; the fill carries severity, the figure says it in words. */
function CapMeters({ provs }: { provs: Prov[] }) {
  return (
    <div className="card">
      <div className="row"><b>Spend vs caps</b><span className="grow" /><Link href="/settings/#providers" className="small">Manage</Link></div>
      {provs.length === 0 && <div className="small mut" style={{ marginTop: 8 }}>No providers enabled yet.</div>}
      {provs.map((p) => {
        const pct = Math.min(100, p.pct_used);
        const sev = pct >= 90 ? "b" : pct >= 70 ? "w" : "";
        return (
          <div key={p.provider} className="meter-row">
            <div className="meter-name"><span>{p.label}</span>{!p.auth_ready && p.kind !== "ollama" && p.kind !== "mock" && <span className="pill bad" title="authentication incomplete">auth</span>}</div>
            <div className="bar"><i className={sev} style={{ width: `${pct}%` }} /></div>
            <div className="meter-fig">{money(p.spent_usd)} <span className="mut">of {money(p.cap_usd)}{p.cap_period !== "total" ? `/${p.cap_period === "daily" ? "day" : "mo"}` : ""}</span>
              {sev && <span className={`pill ${sev === "b" ? "bad" : "warn"}`}>{sev === "b" ? "⚠ near cap" : "70%+"}</span>}
              <div className="small mut">{p.credits.remaining_usd !== null ? `${money(p.credits.remaining_usd)} credit left` : "credits not tracked"}{p.refused ? ` · ${p.refused} refused` : ""}</div></div>
          </div>
        );
      })}
    </div>
  );
}

function ExternalList({ ext }: { ext: Ext[] }) {
  const shown = ext.filter((a) => a.installed);
  return (
    <div className="card">
      <div className="row"><b>External agents this month</b><span className="grow" /><Link href="/settings/#external" className="small">Limits</Link></div>
      <div className="small mut" style={{ margin: "2px 0 6px" }}>Billed to their own accounts. Agent Studio enforces the limits you set, before each turn.</div>
      {shown.length === 0 && <div className="small mut">None installed.</div>}
      {shown.map((a) => {
        const cap = a.month_turn_cap || 0;
        const pct = cap ? Math.min(100, (a.month.turns / cap) * 100) : 0;
        return (
          <div key={a.id} className="meter-row">
            <div className="meter-name"><span>{a.label}</span>{!a.enabled && <span className="pill">off</span>}</div>
            <div className="bar">{cap ? <i className={pct >= 90 ? "b" : pct >= 70 ? "w" : ""} style={{ width: `${pct}%` }} /> : null}</div>
            <div className="meter-fig">{a.month.turns} turn{a.month.turns === 1 ? "" : "s"}{cap ? <span className="mut"> of {cap}</span> : <span className="mut"> · no turn limit</span>}
              <div className="small mut">{a.month.minutes} min · {a.month.cost_reported_turns ? `${usd(a.month.reported_cost ?? 0)} reported` : "cost not reported"}</div></div>
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardPage() {
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Dash>("/api/dashboard").then((x) => { setD(x); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  if (err && !d) return <div><h2>Dashboard</h2><div className="empty" role="alert">Agent Studio&apos;s server is not answering, so there is nothing to show yet. It retries every 15 seconds. ({err})</div></div>;
  if (!d) return (
    <div aria-busy="true" data-testid="dash-loading">
      <div className="page-head"><h2>Dashboard</h2></div>
      <p className="page-sub">Loading spend, activity and system health…</p>
      <div className="kpis">{Array.from({ length: 6 }, (_, i) => <div key={i} className="skeleton skel-kpi" />)}</div>
      <div className="skeleton skel-chart" />
      <div className="dash-2"><div className="skeleton skel-card" /><div className="skeleton skel-card" /></div>
    </div>);
  const k = d.kpis, h = d.health;
  return (
    <div>
      <div className="page-head"><h2>Dashboard</h2><InfoTip id="dashboard" /><span className="grow" /><Link href="/?new=1" className="chip">New chat</Link></div>
      <p className="page-sub">Spend, credits, activity and system health at a glance. Updates every 15 seconds.</p>
      <div className="kpis">
        <Kpi label="Spent this month" value={money(k.month_spent_usd)} sub={`${k.month_calls} model calls${k.month_refused ? ` · ${k.month_refused} refused by the guard` : ""}`} href="/billing/" />
        <Kpi label="Credits left" value={k.credits_left_usd !== null ? money(k.credits_left_usd) : "—"} sub={k.credits_tracked ? `across ${k.credits_tracked} tracked provider${k.credits_tracked === 1 ? "" : "s"}` : "set balances in Settings"} href="/settings/#providers" />
        <Kpi label="Messages, 7 days" value={String(k.turns_7d)} sub="your chat turns" href="/runs/" />
        <Kpi label="Running now" value={String(k.running)} sub={k.running ? "background runs in progress" : "nothing running"} />
        <Kpi label="External agent turns" value={String(k.external_turns_month)} sub="this month, own accounts" href="/settings/#external" />
        <Kpi label="Ready" value={`${h.providers_ready}/${h.providers_enabled}`} sub={`providers · ${h.mcp_ready}/${h.mcp_total} MCP · ${h.external_installed}/${h.external_total} external`} href="/settings/" />
      </div>
      <SpendChart days={d.daily} />
      <div className="dash-2">
        <CapMeters provs={d.providers} />
        <ExternalList ext={d.external} />
      </div>
      <div className="dash-2">
        <div className="card">
          <div className="row"><b>Recent runs</b><span className="grow" /><Link href="/runs/" className="small">All runs</Link></div>
          {d.recent.length === 0 && <div className="small mut" style={{ marginTop: 8 }}>No chats yet.</div>}
          {d.recent.map((r) => (
            <Link key={r.id} href={`/runs/?s=${r.id}`} className="recent-row">
              {r.running ? <span className="run-dot" /> : <span className="dot" />}
              <span className="t">{r.title}</span><span className="mut small">{r.agent ?? "plain"} · {ago(r.updated)}</span></Link>))}
        </div>
        <div className="card">
          <b>System</b>
          <div className="kv" style={{ marginTop: 10 }}>
            <span>Agents</span><span><Link href="/agents/">{h.agents}</Link></span>
            <span>Skills</span><span><Link href="/skills/">{h.skills}</Link></span>
            <span>MCP servers ready</span><span><Link href="/mcp/">{h.mcp_ready} of {h.mcp_total}</Link></span>
            <span>External agents</span><span>{h.external_installed} of {h.external_total} installed</span>
            <span>Telemetry records</span><span><Link href="/telemetry/">{h.otel_records.toLocaleString()}</Link></span>
          </div>
        </div>
      </div>
    </div>
  );
}
