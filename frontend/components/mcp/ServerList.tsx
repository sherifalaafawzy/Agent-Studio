"use client";
import { useEffect, useRef, useState } from "react";
import { ISearch } from "@/components/Icons";
import BrandIcon from "@/components/BrandIcon";
import { GROUPS, access, leavesPc, type Filter, type GroupId, type Row } from "./shared";

export type AccessFilter = "any" | "read" | "write";
type Props = {
  rows: Row[]; loading: boolean; sel: string | null; onSelect: (id: string) => void; issueSources: Set<string>;
  q: string; setQ: (v: string) => void; filter: Filter; setFilter: (f: Filter) => void; counts: Record<Filter, number>;
  acc: AccessFilter; setAcc: (v: AccessFilter) => void; domain: string; setDomain: (v: string) => void; domains: { id: string; label: string }[];
};

const FILTERS: Filter[] = ["all", "ready", "off", "pending"];

/** The left column: search, filters, and the servers in four groups. A listbox: arrow keys move, Enter opens. */
export default function ServerList(p: Props) {
  const [closed, setClosed] = useState<Record<GroupId, boolean>>({ setup: false, installed: false, builtin: false, available: true });
  const box = useRef<HTMLDivElement>(null);
  const searching = !!(p.q || p.domain || p.acc !== "any" || p.filter !== "all");
  const isOpen = (g: GroupId) => (g === "available" && searching ? true : !closed[g]);
  const visible = p.rows.filter((r) => isOpen(r.group));
  const order = GROUPS.flatMap((g) => visible.filter((r) => r.group === g.id));

  // keep the selected row inside the scroll area when the selection moves by keyboard or deep link
  useEffect(() => { box.current?.querySelector<HTMLElement>("[aria-selected='true']")?.scrollIntoView?.({ block: "nearest" }); }, [p.sel]);

  function key(e: React.KeyboardEvent) {
    const i = order.findIndex((r) => r.id === p.sel);
    const go = (n: number) => { const r = order[Math.max(0, Math.min(order.length - 1, n))]; e.preventDefault(); p.onSelect(r.id); requestAnimationFrame(() => box.current?.querySelector<HTMLElement>(`[data-row='${r.id}']`)?.focus()); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(order.length - 1);
  }

  return (
    <div className="mcp-listcol">
      <div className="mcp-search">
        <ISearch size={14} />
        <input placeholder="Search servers" aria-label="Search servers" value={p.q} onChange={(e) => p.setQ(e.target.value)} />
      </div>
      <div className="mcp-chips" role="group" aria-label="Show">
        {FILTERS.map((k) => (
          <button key={k} className={`chip ${p.filter === k ? "on" : ""}`} aria-pressed={p.filter === k} onClick={() => p.setFilter(k)}>
            {k === "pending" ? "Needs approval" : k[0].toUpperCase() + k.slice(1)}<span className="count">{p.counts[k]}</span></button>))}
      </div>
      <details className="mcp-adv-filters" open={p.acc !== "any" || !!p.domain}>
        <summary>More filters{p.acc !== "any" || p.domain ? " · on" : ""}</summary>
        <label>Access
          <select value={p.acc} onChange={(e) => p.setAcc(e.target.value as AccessFilter)}>
            <option value="any">Any</option><option value="read">Read-only</option><option value="write">Can change things</option></select></label>
        <label>Area
          <select value={p.domain} onChange={(e) => p.setDomain(e.target.value)}>
            <option value="">All areas</option>{p.domains.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
      </details>

      <div className="mcp-scroll" data-tour="mcp-list" ref={box}>
        {p.loading && <div className="mcp-skel" aria-label="Loading servers">{Array.from({ length: 7 }, (_, i) => <div key={i} className="skeleton" />)}</div>}
        {!p.loading && p.rows.length === 0 && <div className="empty small">Nothing here.</div>}
        <div role="listbox" aria-label="MCP servers" onKeyDown={key}>
          {GROUPS.map((g) => {
            const items = p.rows.filter((r) => r.group === g.id);
            if (!items.length) return null;
            const open = isOpen(g.id);
            return (
              <div key={g.id} role="group" aria-label={g.title} className="mcp-group">
                <button className="mcp-group-head" aria-expanded={open} onClick={() => setClosed({ ...closed, [g.id]: open })} title={g.hint}>
                  <span className={`caret ${open ? "open" : ""}`} aria-hidden>›</span><h3>{g.title}</h3><span className="count">{items.length}</span></button>
                {open && items.map((r) => {
                  const on = r.id === p.sel;
                  const acc = access(r.c);
                  return (
                    <div key={r.id} role="option" aria-selected={on} tabIndex={on || (!p.sel && order[0]?.id === r.id) ? 0 : -1} data-row={r.id} className={`mcp-row ${on ? "on" : ""} ${r.m && !r.m.enabled ? "off" : ""}`}
                      onClick={() => p.onSelect(r.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); p.onSelect(r.id); } }}>
                      <BrandIcon id={r.id} label={r.label} size={34} />
                      <span className="mcp-row-main">
                        <span className="mcp-row-name"><span className={`dot ${r.tone}`} title={r.statusText} /><span className="mcp-name">{r.label}</span>
                          {p.issueSources.has(r.id) && <span className="pill bad" title="The harness raised an issue for this server">issue</span>}
                          <span className="grow" />{r.m ? <span className={`pill ${r.tone}`}>{r.statusText}</span> : <span className="pill">{r.statusText}</span>}</span>
                        <span className="mcp-row-desc">{r.description || "No description."}</span>
                        {(acc !== "unknown" || leavesPc(r.m, r.c)) && <span className="mcp-row-badges">
                          {acc === "read" && <span className="pill ok">read-only</span>}
                          {acc === "write" && <span className="pill warn">can write</span>}
                          {leavesPc(r.m, r.c) && <span className="pill">data leaves PC</span>}
                        </span>}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
