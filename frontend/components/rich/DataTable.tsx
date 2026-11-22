"use client";
// A table in an answer you can work with: sort, filter, hide columns, page through big results, expand long cells, copy, and export what you see as CSV, JSON, Excel or Markdown.
import { useMemo, useState } from "react";
import { cellText, compareCells, downloadFile, fileSafe, toCsv, toJson, toMarkdown, toTsv, toXlsx, type Cell, type Col, type Table } from "@/lib/tables";
import BlockActions from "./BlockActions";

const LONG = 120, SIZES = [25, 50, 100];

export default function DataTable({ table, name = "", plain = false }: { table: Table; name?: string; plain?: boolean }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [q, setQ] = useState("");
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);
  const title = table.title || name || "table";
  const cols = useMemo(() => table.columns.map((c, i) => ({ ...c, i })).filter((c) => !hidden.has(c.i)), [table.columns, hidden]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let r = table.rows.map((cells, idx) => ({ cells, idx }));
    if (needle) r = r.filter((x) => cols.some((c) => cellText(x.cells[c.i]).toLowerCase().includes(needle)));
    if (sort) { const t = table.columns[sort.col].type; r = [...r].sort((a, b) => sort.dir * compareCells(a.cells[sort.col], b.cells[sort.col], t) || a.idx - b.idx); }
    return r;
  }, [table, q, sort, cols]);

  const pages = Math.max(1, Math.ceil(rows.length / size)), at = Math.min(page, pages - 1);
  const shown = rows.slice(at * size, at * size + size);
  const view = (): { c: Col[]; r: Cell[][] } => ({ c: cols, r: rows.map((x) => cols.map((c) => x.cells[c.i])) });
  const file = fileSafe(title, "table");
  const exportAs = (kind: "csv" | "json" | "xlsx" | "md") => {
    const { c, r } = view();
    if (kind === "csv") downloadFile(`${file}.csv`, toCsv(c, r), "text/csv;charset=utf-8");
    else if (kind === "json") downloadFile(`${file}.json`, toJson(c, r), "application/json");
    else if (kind === "md") downloadFile(`${file}.md`, toMarkdown(c, r), "text/markdown;charset=utf-8");
    else downloadFile(`${file}.xlsx`, toXlsx(c, r, title) as BlobPart, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  };
  const copy = () => { const { c, r } = view(); navigator.clipboard?.writeText(toTsv(c, r)).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); }); };
  const toggleSort = (col: number) => { setSort((s) => (!s || s.col !== col ? { col, dir: 1 } : s.dir === 1 ? { col, dir: -1 } : null)); setPage(0); };
  const toggleCol = (i: number) => setHidden((h) => { const n = new Set(h); if (n.has(i)) n.delete(i); else if (n.size < table.columns.length - 1) n.add(i); return n; });
  const toggleCell = (k: string) => setOpen((o) => { const n = new Set(o); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  return (
    <figure className="data-table" data-testid="data-table">
      {table.title && <figcaption><b>{table.title}</b></figcaption>}
      <div className="dt-bar">
        <input type="search" aria-label="Filter rows" placeholder="Filter rows" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        <details className="dt-menu"><summary>Columns</summary>
          <div className="dt-pop" role="group" aria-label="Show or hide columns">
            {table.columns.map((c, i) => <label key={i} className="row"><input type="checkbox" checked={!hidden.has(i)} onChange={() => toggleCol(i)} /> {c.label}</label>)}
          </div></details>
        <button className="ghost small" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        <details className="dt-menu"><summary>Export</summary>
          <div className="dt-pop" role="group" aria-label="Export the rows and columns shown">
            <div className="small mut">Exports the rows and columns you are showing.</div>
            <button className="ghost small" onClick={() => exportAs("csv")}>CSV</button>
            <button className="ghost small" onClick={() => exportAs("json")}>JSON</button>
            <button className="ghost small" onClick={() => exportAs("xlsx")}>Excel (.xlsx)</button>
            <button className="ghost small" onClick={() => exportAs("md")}>Markdown</button>
          </div></details>
      </div>
      <div className="rich-scroll">
        <table>
          <thead><tr>{cols.map((c) => (
            <th key={c.i} scope="col" className={c.type === "number" ? "num" : ""} aria-sort={sort?.col === c.i ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
              <button className="th-sort" onClick={() => toggleSort(c.i)} title={`Sort by ${c.label}`}>{c.label}<span aria-hidden="true">{sort?.col === c.i ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</span></button></th>))}</tr></thead>
          <tbody>
            {shown.map((x) => (
              <tr key={x.idx}>{cols.map((c) => {
                const t = cellText(x.cells[c.i]), k = `${x.idx}:${c.i}`, long = t.length > LONG, full = open.has(k);
                return <td key={c.i} className={c.type === "number" ? "num" : ""}>{long && !full ? t.slice(0, LONG) + "…" : t}
                  {long && <button className="link small" aria-expanded={full} onClick={() => toggleCell(k)}>{full ? " less" : " more"}</button>}</td>;
              })}</tr>))}
            {!shown.length && <tr><td colSpan={Math.max(1, cols.length)} className="mut">No rows match this filter.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="dt-foot small">
        <span className="mut">{rows.length === table.rows.length ? `${rows.length} row${rows.length === 1 ? "" : "s"}` : `${rows.length} of ${table.rows.length} rows`}{table.truncated ? " (the first 5000 only)" : ""}</span>
        {rows.length > SIZES[0] && <span className="dt-pager">
          <label>Rows per page <select value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(0); }}>{SIZES.map((s) => <option key={s}>{s}</option>)}</select></label>
          <button className="ghost small" disabled={at === 0} onClick={() => setPage(at - 1)}>Previous</button>
          <span>Page {at + 1} of {pages}</span>
          <button className="ghost small" disabled={at >= pages - 1} onClick={() => setPage(at + 1)}>Next</button></span>}
        {!plain && <BlockActions canvas={{ type: "table" }} label="table" name={`${file}.csv`} content={() => { const { c, r } = view(); return toCsv(c, r); }} />}
      </div>
    </figure>
  );
}
