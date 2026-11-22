"use client";
// The security scorecard: the OWASP Top 10 for LLM applications (2025) and for agentic applications (2026), each risk shown with a live
// status computed from your real settings. Green = on, amber = partly, red = missing, grey = no control maps to it. A to-do list, not a certificate.
// "Overall" judges the app's settings; "Per agent" judges each agent's own tools, guardrails, memory and access, and ranks them worst first.
import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE, api } from "@/lib/api";
import InfoTip from "./InfoTip";

type Status = "pass" | "warn" | "fail";
type Check = { id: string; title: string; maps: string[]; status: Status | "na"; detail: string; fix: string; link: string; kind: string; scope?: "agent" | "shared" };
type Risk = { id: string; name: string; status: Status | "none" | "na"; checks: string[] };
type Summary = { count: number; average: number | null; bands: Record<string, number>; lowest: { id: string; name: string; percent: number } | null };
type AgentInfo = { id: string; name: string; builtin: boolean; framework: string; runtime: string; tools: number };
type Card = {
  percent: number; counts: { pass: number; warn: number; fail: number; na?: number }; note: string; checks: Check[];
  frameworks: { id: string; name: string; url: string; risks: Risk[]; covered: number }[]; agents?: Summary; agent?: AgentInfo;
};
type Issue = { id: string; title: string; status: Status; detail: string; fix: string; link: string };
type Row = AgentInfo & { percent: number; band: string; counts: { pass: number; warn: number; fail: number; na: number }; top_issue: Issue | null };

const WORD = { pass: "Covered", warn: "Partly", fail: "Missing", none: "Not covered", na: "Not applicable" } as const;
const MARK = { pass: "✓", warn: "!", fail: "✕", none: "–", na: "·" } as const;
const TONE = { pass: "ok", warn: "warn", fail: "bad", none: "", na: "" } as const;
const BAR = (p: number) => (p < 50 ? "b" : p < 80 ? "w" : "");

/** The score header, the two OWASP lists and the risk detail for one card (overall or one agent's). */
function CardView({ card, title, actions, children }: { card: Card; title: string; actions: React.ReactNode; children?: React.ReactNode }) {
  const [fw, setFw] = useState("llm2025");
  const [open, setOpen] = useState("");
  const frame = card.frameworks.find((f) => f.id === fw)!;
  const byId = Object.fromEntries(card.checks.map((c) => [c.id, c]));
  const risk = frame.risks.find((r) => r.id === open);
  const na = card.counts.na ?? 0;
  return (
    <div data-testid="scorecard">
      <div className="card">
        <div className="card-head"><b>{title}</b><InfoTip id="security-score" /><span className="grow" />{actions}</div>
        <div className="row" style={{ alignItems: "center", gap: 16, margin: "10px 0" }}>
          <div style={{ fontSize: 40, fontWeight: 700, lineHeight: 1 }} aria-label={`Score ${card.percent} percent`}>{card.percent}%</div>
          <div className="grow"><div className="bar" role="presentation"><i className={BAR(card.percent)} style={{ width: `${card.percent}%` }} /></div>
            <div className="small mut" style={{ marginTop: 4 }}>{card.counts.pass} in place · {card.counts.warn} partly · {card.counts.fail} missing{na ? ` · ${na} not applicable` : ""}</div></div>
        </div>
        <p className="small mut">{card.note}</p>
      </div>
      {children}

      <div className="tabs" role="tablist">{card.frameworks.map((f) => (
        <button key={f.id} role="tab" aria-selected={fw === f.id} className={`tab ${fw === f.id ? "on" : ""}`} onClick={() => { setFw(f.id); setOpen(""); }}>{f.name.replace("OWASP Top 10 for ", "").replace(/ \d{4}$/, "")} <span className="count">{f.name.match(/\d{4}$/)?.[0]}</span></button>))}</div>
      <div className="small mut" style={{ marginBottom: 8 }}>{frame.name} · <a href={frame.url} target="_blank" rel="noreferrer">source</a> · {frame.covered} of {frame.risks.length} risks have a control here</div>
      <div className="cat-grid">{frame.risks.map((r) => (
        <button key={r.id} className="card cat-card risk" aria-pressed={open === r.id} data-status={r.status} onClick={() => setOpen(open === r.id ? "" : r.id)} style={{ textAlign: "left" }}>
          <div className="card-head"><b>{r.id}</b><span className={`pill ${TONE[r.status]}`}><span aria-hidden="true">{MARK[r.status]}</span> {WORD[r.status]}</span></div>
          <div>{r.name}</div>
        </button>))}</div>
      {risk && <div className="card" data-testid="risk-detail">
        <div className="card-head"><b>{risk.id} {risk.name}</b><span className={`pill ${TONE[risk.status]}`}>{WORD[risk.status]}</span></div>
        {risk.checks.length === 0 && <p className="small mut">No control in Agent Studio is mapped to this risk yet.</p>}
        {risk.checks.map((id) => { const c = byId[id]; return (
          <div key={id} style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }}>
            <div className="row"><span className={`pill ${TONE[c.status]}`}><span aria-hidden="true">{MARK[c.status]}</span> {WORD[c.status]}</span><b>{c.title}</b>{c.kind === "built-in" && <span className="pill">built in</span>}{c.scope === "shared" && <span className="pill" title="Reads the same for every agent">whole app</span>}</div>
            <div className="small">{c.detail}</div>
            {c.fix && <div className="small">What to do: {c.fix} <a href={c.link}>Open →</a></div>}
          </div>); })}
      </div>}
    </div>
  );
}

const SORTS = { worst: "Worst first", best: "Best first", name: "Name" } as const;
const SHOWS = { all: "All agents", attention: "Needs attention (below 50%)", fix: "Has something to fix" } as const;

/** Every agent with its score and most serious issue, worst first, with filters. */
function Ranking({ rows, summary, onOpen }: { rows: Row[]; summary: Summary | null; onOpen: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [show, setShow] = useState<keyof typeof SHOWS>("all");
  const [sort, setSort] = useState<keyof typeof SORTS>("worst");
  const [own, setOwn] = useState(false);
  const shown = useMemo(() => {
    const f = rows.filter((r) => (!q || `${r.name} ${r.id}`.toLowerCase().includes(q.toLowerCase())) && (!own || !r.builtin)
      && (show === "all" || (show === "attention" ? r.percent < 50 : r.counts.fail + r.counts.warn > 0)));
    return sort === "name" ? [...f].sort((a, b) => a.name.localeCompare(b.name)) : sort === "best" ? [...f].reverse() : f;
  }, [rows, q, show, sort, own]);
  return (
    <div data-testid="agent-ranking">
      <div className="card">
        <div className="card-head"><b>Agents, weakest first</b><InfoTip id="agent-scorecard" /><span className="grow" />
          <a className="small" href={`${API_BASE}/api/security/agents?format=md`} download>All agents (Markdown)</a>
          <a className="small" href={`${API_BASE}/api/security/agents?format=full`} download>All agents (JSON)</a></div>
        {summary && summary.count > 0 && <p className="small mut" style={{ margin: "4px 0 10px" }}>{summary.count} agents · average {summary.average}% · {summary.bands.low ?? 0} below 50% · {summary.bands.good ?? 0} at 80% or more</p>}
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <input aria-label="Find an agent" placeholder="Find an agent…" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 160 }} />
          <select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value as keyof typeof SHOWS)}>{Object.entries(SHOWS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as keyof typeof SORTS)}>{Object.entries(SORTS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><input type="checkbox" checked={own} onChange={(e) => setOwn(e.target.checked)} />Only my own agents</label>
        </div>
      </div>
      {shown.length === 0 && <p className="small mut">No agent matches.</p>}
      <ul className="sc-rank" style={{ listStyle: "none", padding: 0, margin: 0 }}>{shown.map((r) => (
        <li key={r.id} className="card sc-row" data-testid="rank-row" data-band={r.band}>
          <div className="sc-name"><b>{r.name}</b> {r.builtin && <span className="pill">built in</span>}{r.runtime !== "local" && <span className="pill warn">runs on {r.runtime}</span>}</div>
          <div className="sc-score"><b aria-label={`Score ${r.percent} percent`}>{r.percent}%</b><div className="bar" aria-hidden="true"><i className={BAR(r.percent)} style={{ width: `${r.percent}%` }} /></div></div>
          <div className="sc-counts small mut">{r.counts.fail} missing · {r.counts.warn} partly</div>
          <div className="sc-issue small">{r.top_issue ? <><span className={`pill ${TONE[r.top_issue.status]}`}><span aria-hidden="true">{MARK[r.top_issue.status]}</span> {WORD[r.top_issue.status]}</span> {r.top_issue.title}</> : <span className="mut">Nothing to fix</span>}</div>
          <button className="small" onClick={() => onOpen(r.id)} aria-label={`Open the scorecard of ${r.name}`}>Open</button>
        </li>))}</ul>
    </div>
  );
}

export default function Scorecard({ initialAgent = "" }: { initialAgent?: string }) {
  const [card, setCard] = useState<Card | null>(null);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<"overall" | "agents">("overall");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [sel, setSel] = useState("");
  const [agentCard, setAgentCard] = useState<Card | null>(null);
  const loadOverall = useCallback(() => api<Card>("/api/security/scorecard").then((c) => { setCard(c); setErr(""); }).catch((e) => setErr(e.message)), []);
  const loadRows = useCallback(() => api<{ agents: Row[]; summary: Summary }>("/api/security/agents").then((r) => { setRows(r.agents); setSummary(r.summary); setErr(""); }).catch((e) => setErr(e.message)), []);
  const loadAgent = useCallback((id: string) => api<Card>(`/api/security/agents/${id}`).then((c) => { setAgentCard(c); setErr(""); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { loadOverall(); }, [loadOverall]);
  useEffect(() => { if (initialAgent) { setMode("agents"); setSel(initialAgent); } }, [initialAgent]);
  useEffect(() => { if (mode === "agents" && rows === null) loadRows(); }, [mode, rows, loadRows]);
  useEffect(() => { setAgentCard(null); if (mode === "agents" && sel) loadAgent(sel); }, [mode, sel, loadAgent]);

  const pick = (id: string) => {
    setSel(id);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("agent", id); else url.searchParams.delete("agent");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  };
  const again = () => { loadOverall(); if (mode === "agents") { loadRows(); if (sel) loadAgent(sel); } };

  const modes = (
    <div className="tabs" role="tablist" aria-label="What to score" style={{ marginBottom: 12 }}>
      {([["overall", "Overall"], ["agents", "Per agent"]] as const).map(([k, l]) => (
        <button key={k} role="tab" aria-selected={mode === k} className={`tab ${mode === k ? "on" : ""}`} onClick={() => { setMode(k); if (k === "overall") pick(""); }}>{l}</button>))}
    </div>);
  if (!card) return err ? <p className="err" role="alert">{err}</p> : null;

  const overallActions = (<>
    <button className="small" onClick={again}>Check again</button>
    <a className="small" href={`${API_BASE}/api/security/scorecard?format=md`} download>Download report (Markdown)</a></>);

  if (mode === "overall") {
    const s = card.agents;
    return (
      <div>{modes}
        <CardView card={card} title="Security score" actions={overallActions}>
          {s && s.count > 0 && s.lowest && (
            <div className="card" data-testid="agents-summary">
              <div className="card-head"><b>Your agents</b><InfoTip id="agent-scorecard" /><span className="grow" />
                <button className="small" onClick={() => setMode("agents")}>See each agent →</button></div>
              <div className="small">{s.count} agents · average {s.average}% · weakest: <b>{s.lowest.name}</b> ({s.lowest.percent}%) · {s.bands.low ?? 0} below 50%</div>
              <div className="small mut">This score judges the app&apos;s settings. Each agent also has its own score from its own tools, guardrails, memory and access.</div>
            </div>)}
        </CardView>
      </div>);
  }

  const mine = rows?.find((r) => r.id === sel);
  return (
    <div>{modes}
      {err && <p className="err" role="alert">{err}</p>}
      {!sel && (rows ? <Ranking rows={rows} summary={summary} onOpen={pick} /> : <p className="small mut">Loading agents…</p>)}
      {sel && (
        <div>
          <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
            <button className="small" onClick={() => pick("")}>← All agents</button>
            <select aria-label="Agent" value={sel} onChange={(e) => pick(e.target.value)}>
              {(rows ?? []).some((r) => r.id === sel) ? null : <option value={sel}>{sel}</option>}
              {(rows ?? []).map((r) => <option key={r.id} value={r.id}>{r.name} · {r.percent}%</option>)}</select>
            <span className="grow" />
            <a className="small" href={`/agents/?agent=${sel}`}>Edit this agent →</a>
          </div>
          {agentCard ? (
            <CardView card={agentCard} title={`Security score: ${agentCard.agent?.name ?? sel}`}
              actions={<><button className="small" onClick={again}>Check again</button>
                <a className="small" href={`${API_BASE}/api/security/agents/${sel}?format=md`} download>Download report (Markdown)</a>
                <a className="small" href={`${API_BASE}/api/security/agents/${sel}?download=1`} download>Download (JSON)</a></>}>
              <div className="small mut" style={{ margin: "0 0 10px" }} data-testid="agent-facts">
                {mine ? `${mine.builtin ? "Built-in agent" : "Your agent"} · ` : ""}{agentCard.agent?.framework} framework · runs on {agentCard.agent?.runtime === "local" ? "this PC" : agentCard.agent?.runtime} · {agentCard.agent?.tools} tools</div>
            </CardView>) : (!err && <p className="small mut">Loading…</p>)}
        </div>)}
    </div>
  );
}
