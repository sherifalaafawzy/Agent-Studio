"use client";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { IPlus } from "@/components/Icons";
import InfoTip from "@/components/InfoTip";
import type { CatalogServer } from "@/components/CatalogMcp";
import IssuesBar from "@/components/mcp/IssuesBar";
import ServerList, { type AccessFilter } from "@/components/mcp/ServerList";
import ServerDetail from "@/components/mcp/ServerDetail";
import AddServerFlow from "@/components/mcp/AddServerFlow";
import { GROUPS, access, buildRows, type Filter, type Issue, type Mcp, type Row } from "@/components/mcp/shared";
import { usePageContext } from "@/lib/pagectx";

const DOMAIN_LABEL: Record<string, string> = { accounting: "Accounting", sales: "Sales", marketing: "Marketing", manufacturing: "Manufacturing", engineering: "Engineering", data: "Data", "machine-learning": "Machine learning", "deep-learning": "Deep learning" };
const isPhone = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(max-width: 820px)").matches;

function Mcps() {
  const sp = useSearchParams();
  const wanted = sp.get("server");
  const [list, setList] = useState<Mcp[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogServer[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [sel, setSel] = useState<string | null>(wanted);
  const [view, setView] = useState<"list" | "detail">(wanted ? "detail" : "list");
  usePageContext("mcp", () => ({ selectedServer: sel, screen: view, filter }));
  const [filter, setFilter] = useState<Filter>("all");
  const [acc, setAcc] = useState<AccessFilter>("any");
  const [domain, setDomain] = useState("");
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState<{ start: string | null } | null>(null);
  const [err, setErr] = useState("");
  const headRef = useRef<HTMLHeadingElement>(null);
  const focusDetail = useRef(false);
  const pending = useRef<string | null>(wanted);          // a server we were sent to that may not be in the list yet
  const [catalogDone, setCatalogDone] = useState(false);

  const load = useCallback(async () => {
    try { setList(await api<Mcp[]>("/api/mcp")); setErr(""); } catch (e) { setErr("Backend offline: " + (e as Error).message); }
    api<CatalogServer[]>("/api/catalog/mcp").then(setCatalog).catch(() => setCatalog([])).finally(() => setCatalogDone(true));
  }, []);
  const loadIssues = useCallback(() => api<Issue[]>("/api/issues").then(setIssues).catch(() => setIssues([])), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadIssues(); const t = setInterval(loadIssues, 10000); return () => clearInterval(t); }, [loadIssues]);
  const reload = useCallback(() => { load(); loadIssues(); }, [load, loadIssues]);

  const all = useMemo(() => list ?? [], [list]);
  const rows = useMemo(() => buildRows(all, catalog), [all, catalog]);
  const domains = useMemo(() => [...new Set(catalog.flatMap((c) => c.domains))].sort().map((id) => ({ id, label: DOMAIN_LABEL[id] ?? id })), [catalog]);

  const match = (r: Row) => {
    const m = r.m;
    if (filter !== "all") {
      if (!m) return false;
      if (filter === "ready" ? !m.ready : filter === "off" ? m.enabled || m.status === "pending_approval" : m.status !== "pending_approval") return false;
    }
    if (acc !== "any" && access(r.c) !== acc) return false;
    if (domain && !r.c?.domains.includes(domain)) return false;
    return !q || `${r.label} ${r.description} ${r.id} ${r.c?.publisher ?? ""}`.toLowerCase().includes(q.toLowerCase());
  };
  const shown = rows.filter(match);
  const ordered = GROUPS.flatMap((g) => shown.filter((r) => r.group === g.id));
  const counts: Record<Filter, number> = { all: all.length, ready: all.filter((m) => m.ready).length,
    off: all.filter((m) => !m.enabled && m.status !== "pending_approval").length, pending: all.filter((m) => m.status === "pending_approval").length };

  // Desktop shows a detail panel from the start: the deep-linked server, or the first one in the list.
  useEffect(() => {
    if (list === null) return;
    const found = !!sel && rows.some((r) => r.id === sel);
    if (found && sel === pending.current) pending.current = null;
    if (found) return;
    if (sel && sel === pending.current && !catalogDone) return;       // still loading: the catalog may hold it
    pending.current = null;
    if (ordered[0]) setSel(ordered[0].id);
  }, [list, rows, sel, ordered, catalogDone]);
  useEffect(() => { if (view === "detail" && focusDetail.current) { focusDetail.current = false; headRef.current?.focus(); } }, [view, sel]);

  const pick = useCallback((id: string) => {
    setSel(id); setView("detail"); focusDetail.current = isPhone();
    try { const u = new URL(window.location.href); u.searchParams.set("server", id); window.history.replaceState(window.history.state, "", u); } catch { /* the address bar is a convenience */ }
  }, []);
  const back = () => { setView("list"); requestAnimationFrame(() => document.querySelector<HTMLElement>("[role=option][aria-selected=true]")?.focus()); };
  const selRow = rows.find((r) => r.id === sel);

  return (
    <div className="mcp-page">
      <div className="page-head"><h2>MCP servers</h2><InfoTip id="mcp" /><span className="grow" />
        <button className="acc" onClick={() => setAdding({ start: null })} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><IPlus size={15} />Add server</button></div>
      <p className="page-sub">Tools your agents can use. Local servers run on this machine at pinned versions (filesystem and git are limited to the workspace). Give servers to agents in <a href="/agents/">Agent Studio</a>.</p>
      <IssuesBar issues={issues} onChange={reload} onOpen={(s) => { if (rows.some((r) => r.id === s)) pick(s); }} />
      {err && <p className="err">{err}</p>}
      <div className="mcp-split" data-view={view}>
        <aside className="mcp-list" aria-label="Servers">
          <ServerList rows={shown} loading={list === null && !err} sel={sel} onSelect={pick} issueSources={new Set(issues.map((i) => i.source))}
            q={q} setQ={setQ} filter={filter} setFilter={setFilter} counts={counts} acc={acc} setAcc={setAcc} domain={domain} setDomain={setDomain} domains={domains} />
        </aside>
        <section className="mcp-detail" aria-label="Server details" data-tour="mcp-detail">
          {selRow
            ? <ServerDetail key={selRow.id} row={selRow} issues={issues.filter((i) => i.source === selRow.id)} onChange={reload} onBack={back} headRef={headRef} onAdd={(id) => setAdding({ start: id })} />
            : <div className="empty">{err ? "No servers to show." : list === null ? "Loading…" : "Pick a server on the left to see its settings."}</div>}
        </section>
      </div>
      {adding && <AddServerFlow catalog={catalog} startWith={adding.start} onClose={() => setAdding(null)} onAdded={(id) => { pending.current = id; setCatalogDone(false); reload(); pick(id); }} />}
    </div>
  );
}

export default function McpPage() {
  return <Suspense fallback={<div className="card" style={{ margin: 24 }}>Loading…</div>}><Mcps /></Suspense>;
}
