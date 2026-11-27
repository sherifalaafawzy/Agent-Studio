"use client";
// "Runs": every run of every workflow in one table. Search and filter (workflow, status, what started it, when, "needs you"), sort, page, select several to delete,
// and open one in a side panel (a full screen on a phone). Running runs refresh by themselves.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { toast } from "@/components/Nav";
import InfoTip from "@/components/InfoTip";
import { ConfirmDelete } from "@/components/Modal";
import RunDetail from "@/components/workflows/RunDetail";
import { ago, fmtDuration, ORIGIN_LABEL, RUN_STATUS, type RunList, type RunRow } from "@/lib/workflows";

const PAGE = 25, POLL_MS = 3000;
const RANGES: [string, string, number][] = [["", "Any time", 0], ["1h", "Last hour", 3600], ["24h", "Last 24 hours", 86400], ["7d", "Last 7 days", 604800], ["30d", "Last 30 days", 2592000]];

function Boxes({ r }: { r: RunRow }) {
  const c = r.counts;
  return (
    <span className="small">{c.done + c.error} of {c.total} ran{c.error > 0 && <span style={{ color: "var(--bad-text)" }}> · {c.error} failed</span>}{c.skipped > 0 && <span className="mut"> · {c.skipped} skipped</span>}{c.waiting > 0 && <span style={{ color: "var(--warn-text)" }}> · {c.waiting} waiting</span>}</span>
  );
}

export default function RunsTab() {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(""), [qd, setQd] = useState("");
  const [workflow, setWorkflow] = useState(params.get("workflow") ?? ""), [status, setStatus] = useState(""), [origin, setOrigin] = useState(""), [range, setRange] = useState(""), [needs, setNeeds] = useState(false);
  const [sort, setSort] = useState("started"), [order, setOrder] = useState<"asc" | "desc">("desc"), [offset, setOffset] = useState(0);
  const [data, setData] = useState<RunList | null>(null), [err, setErr] = useState("");
  const [flows, setFlows] = useState<{ id: string; name: string }[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set()), [confirm, setConfirm] = useState(false);
  const [sel, setSel] = useState<string | null>(params.get("run"));
  useEffect(() => { const t = setTimeout(() => setQd(q), 250); return () => clearTimeout(t); }, [q]);
  useEffect(() => { api<{ workflows: { id: string; name: string }[] }>("/api/workflows").then((r) => setFlows(r.workflows)).catch(() => setFlows([])); }, []);
  const query = useMemo(() => {
    const p = new URLSearchParams({ sort, order, limit: String(PAGE), offset: String(offset) });
    if (qd) p.set("q", qd);
    if (workflow) p.set("workflow", workflow);
    if (status) p.set("status", status);
    if (origin) p.set("origin", origin);
    if (needs) p.set("needs_you", "true");
    return p;
  }, [qd, workflow, status, origin, needs, sort, order, offset]);
  const load = useCallback(() => {
    const p = new URLSearchParams(query);
    const secs = RANGES.find((x) => x[0] === range)?.[2] ?? 0;
    if (secs) p.set("since", String(Math.floor(Date.now() / 1000) - secs));
    return api<RunList>(`/api/workflows/runs?${p}`).then((r) => { setData(r); setErr(""); }).catch((e) => { setErr((e as Error).message); setData((d) => d ?? { runs: [], total: 0, counts: {}, needs_you: 0, retention: 0, limit: PAGE, offset: 0 }); });
  }, [query, range]);
  useEffect(() => { load(); }, [load]);
  const active = (data?.counts.running ?? 0) > 0;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [active, load]);
  const filtered = !!(qd || workflow || status || origin || range || needs);
  const reset = () => { setQ(""); setQd(""); setWorkflow(""); setStatus(""); setOrigin(""); setRange(""); setNeeds(false); setOffset(0); };
  const change = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0); setPicked(new Set()); };
  const open = (id: string | null) => { setSel(id); router.replace(id ? `/workflows/?tab=runs&run=${id}` : "/workflows/?tab=runs"); };
  const rows = data?.runs ?? [];
  const total = data?.total ?? 0;
  const counts = data?.counts ?? {};
  const all = Object.values(counts).reduce((a, b) => a + b, 0);
  const sortBy = (k: string) => { if (sort === k) setOrder(order === "desc" ? "asc" : "desc"); else { setSort(k); setOrder("desc"); } setOffset(0); };
  const selectable = rows.filter((r) => r.status !== "running");
  const th = (k: string, label: string) => (
    <th aria-sort={sort === k ? (order === "asc" ? "ascending" : "descending") : "none"}><button className="link" onClick={() => sortBy(k)}>{label}{sort === k ? (order === "asc" ? " ↑" : " ↓") : ""}</button></th>
  );
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const del = async () => {
    setConfirm(false);
    try {
      const r = await api<{ deleted: string[]; skipped: { id: string; reason: string }[] }>("/api/workflows/runs/delete", { method: "POST", body: JSON.stringify({ ids: [...picked] }) });
      toast(`Deleted ${r.deleted.length} run(s)${r.skipped.length ? `; left ${r.skipped.length} that cannot be deleted (${r.skipped[0].reason})` : ""}`);
      if (sel && r.deleted.includes(sel)) open(null);
      setPicked(new Set());
      load();
    } catch (e) { setErr((e as Error).message); }
  };
  const chips: [string, string, number][] = [["", "All", all], ...Object.entries(RUN_STATUS).map(([k, v]) => [k, v.label, counts[k] ?? 0] as [string, string, number])];
  const first = total === 0 ? 0 : offset + 1, last = Math.min(offset + PAGE, total);

  return (
    <div data-testid="runs-tab">
      <div className="row" style={{ marginBottom: 10, flexWrap: "wrap" }}>
        <input value={q} placeholder="Search runs (task, error, workflow)" aria-label="Search runs" style={{ minWidth: 220 }} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <select value={workflow} aria-label="Workflow" onChange={(e) => change(setWorkflow)(e.target.value)}><option value="">Any workflow</option>{flows.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select>
        <select value={origin} aria-label="Started by" onChange={(e) => change(setOrigin)(e.target.value)}><option value="">Anything started it</option>{Object.entries(ORIGIN_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select value={range} aria-label="When" onChange={(e) => change(setRange)(e.target.value)}>{RANGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <button className={`chip ${needs ? "on" : ""}`} aria-pressed={needs} onClick={() => change(setNeeds)(!needs)}>Needs you{data && data.needs_you > 0 ? ` (${data.needs_you})` : ""}</button>
        <span className="grow" /><InfoTip id="wf-runs" /><button onClick={load}>Refresh</button>
        {picked.size > 0 && <button onClick={() => setConfirm(true)}>Delete {picked.size} selected…</button>}
      </div>
      <div className="runs-chips" role="group" aria-label="Show runs by result">
        {chips.map(([k, label, n]) => <button key={k || "all"} className={`chip ${status === k ? "on" : ""}`} aria-pressed={status === k} onClick={() => change(setStatus)(k)}>{k && <span aria-hidden="true">{RUN_STATUS[k].icon}</span>} {label} <b>{n}</b></button>)}
      </div>
      {err && <div className="small" role="alert" style={{ color: "var(--bad-text)", marginBottom: 8 }}>{err} <button className="link" onClick={load}>Try again</button></div>}

      <div className={`runs-layout ${sel ? "has-detail" : ""}`}>
        <div className="runs-main">
          {data === null && <div data-testid="runs-loading">{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 44, marginBottom: 8 }} />)}</div>}
          {data && total === 0 && !filtered && !err && <div className="card" data-testid="runs-empty">No runs yet. Run a workflow from the <button className="link" onClick={() => router.replace("/workflows/")}>Flows</button> tab and its record shows up here.</div>}
          {data && total === 0 && filtered && <div className="card" data-testid="runs-none">No run matches. <button className="link" onClick={reset}>Clear the filters</button></div>}
          {rows.length > 0 && (<>
            <table className="tbl wf-table" data-testid="runs-table"><thead><tr>
              <th><input type="checkbox" aria-label="Select all runs on this page" checked={selectable.length > 0 && selectable.every((r) => picked.has(r.id))} onChange={(e) => setPicked(e.target.checked ? new Set(selectable.map((r) => r.id)) : new Set())} /></th>
              <th>Workflow</th>{th("status", "Result")}<th>Started by</th>{th("started", "Started")}{th("duration", "Took")}<th>Boxes</th>{th("cost", "Cost")}<th>Version</th></tr></thead><tbody>
              {rows.map((r) => {
                const s = RUN_STATUS[r.status] ?? { label: r.status, icon: "", tone: "" };
                return (
                  <tr key={r.id} className={sel === r.id ? "on" : ""} data-testid="run-row">
                    <td><input type="checkbox" aria-label={`Select run ${r.id}`} disabled={r.status === "running"} checked={picked.has(r.id)} onChange={() => toggle(r.id)} /></td>
                    <td><button className="link" aria-label={`Open run ${r.id}`} onClick={() => open(r.id)}><b>{r.name}</b></button>{r.needs_you && <span className="pill warn" style={{ marginLeft: 6 }}>needs you</span>}
                      <div className="small mut" style={{ overflowWrap: "anywhere" }}>{r.error ? <span style={{ color: "var(--bad-text)" }}>{r.error}</span> : r.task || "(no task)"}</div></td>
                    <td data-label="Result"><span className={`pill ${s.tone}`}><span aria-hidden="true">{s.icon}</span> {s.label}</span></td>
                    <td data-label="Started by">{ORIGIN_LABEL[r.origin] ?? r.origin}</td>
                    <td data-label="Started" title={new Date(r.started * 1000).toLocaleString()}>{ago(r.started)}</td>
                    <td data-label="Took">{fmtDuration(r.duration)}{r.status === "running" ? "…" : ""}</td>
                    <td data-label="Boxes"><Boxes r={r} /></td>
                    <td data-label="Cost" className="small">{r.cost_usd > 0 ? `$${r.cost_usd.toFixed(4)}` : "–"}{r.tokens > 0 ? ` · ${r.tokens.toLocaleString()} tokens` : ""}</td>
                    <td data-label="Version">v{r.version}</td></tr>);
              })}</tbody></table>
            <div className="row small" style={{ marginTop: 10 }} data-testid="runs-pager">
              <span className="mut">Showing {first}–{last} of {total}{data && data.retention > 0 ? `. Agent Studio keeps the latest ${data.retention} runs of each workflow.` : ""}</span><span className="grow" />
              <button className="small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button><button className="small" disabled={last >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div></>)}
        </div>
        {sel && <RunDetail key={sel} id={sel} onClose={() => open(null)} onChanged={load} onOpen={(id) => { open(id); load(); }} />}
      </div>
      {confirm && <ConfirmDelete what={`${picked.size} run(s)`} onClose={() => setConfirm(false)} onConfirm={del} />}
    </div>
  );
}
