"use client";
// A table in an answer you can work with: sort, filter, hide columns, page through big results, expand long cells, copy, group with subtotals, show data bars and
// colour scales, pin the first column, and export what you see as CSV, JSON, Excel, Markdown, a web page or a PDF.
import { useMemo, useRef, useState } from "react";
import { cellText, compareCells, downloadFile, fileSafe, toCsv, toJson, toMarkdown, toTsv, toXlsx, type Cell, type Col, type Table } from "@/lib/tables";
import { badgeIcon, badgeTone, columnRange, linkCell, progressOf, showCell } from "@/lib/uikit/cellfmt";
import { num, seqColor } from "@/lib/uikit/data";
import { fileName, htmlPage, printElement } from "@/lib/uikit/exporters";
import BlockActions from "./BlockActions";

const LONG = 120, SIZES = [25, 50, 100];
export type TableOpts = { groupBy?: number; pinFirst?: boolean; density?: "compact" | "comfortable"; select?: boolean };
type View = { kind: "row"; idx: number; cells: Cell[] } | { kind: "group"; label: string; count: number } | { kind: "sub"; label: string; sums: (number | null)[] };

function CellView({ col, v, range }: { col: Col; v: Cell; range: { lo: number; hi: number } }) {
  const t = showCell(col, v);
  if (col.format === "badge" && t) { const tone = badgeTone(t); return <span className={`pill ${tone}`}><span aria-hidden="true">{badgeIcon(tone)} </span>{t}</span>; }
  if (col.format === "link" && t) { const l = linkCell(v); return l.href ? <a href={l.href} target="_blank" rel="noopener noreferrer">{l.text}</a> : <>{l.text}</>; }
  if (col.format === "progress" && v !== null && v !== "") { const p = progressOf(v); return <span className="dt-progress" role="progressbar" aria-valuenow={Math.round(p * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={col.label}><i style={{ width: `${p * 100}%` }} />{`${Math.round(p * 100)}%`}</span>; }
  const n = num(v);
  if (col.type === "number" && Number.isFinite(n) && col.color === "bar") return <span className="dt-bar-cell"><i style={{ width: `${Math.max(0, Math.min(1, (n - range.lo) / (range.hi - range.lo))) * 100}%` }} aria-hidden="true" />{t}</span>;
  if (col.type === "number" && Number.isFinite(n) && col.color === "scale") {
    const neg = n < 0, mag = neg ? Math.min(1, -n / (Math.abs(range.lo) || 1)) : Math.min(1, n / (range.hi || 1));
    return <span className="dt-scale-cell" style={{ background: seqColor(mag * 0.6, neg ? 2 : 1) }}>{t}</span>;
  }
  return <>{t}</>;
}

export default function DataTable({ table, name = "", plain = false, opts = {} }: { table: Table; name?: string; plain?: boolean; opts?: TableOpts }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [q, setQ] = useState("");
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [dense, setDense] = useState(opts.density === "compact");
  const [copied, setCopied] = useState(false);
  const root = useRef<HTMLElement>(null);
  const title = table.title || name || "table";
  const cols = useMemo(() => table.columns.map((c, i) => ({ ...c, i })).filter((c) => !hidden.has(c.i)), [table.columns, hidden]);
  const ranges = useMemo(() => table.columns.map((c, i) => (c.color ? columnRange(c, table.rows.map((r) => r[i])) : { lo: 0, hi: 1 })), [table]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let r = table.rows.map((cells, idx) => ({ cells, idx }));
    if (needle) r = r.filter((x) => cols.some((c) => cellText(x.cells[c.i]).toLowerCase().includes(needle)));
    if (sort) { const t = table.columns[sort.col].type; r = [...r].sort((a, b) => sort.dir * compareCells(a.cells[sort.col], b.cells[sort.col], t) || a.idx - b.idx); }
    return r;
  }, [table, q, sort, cols]);

  // grouping flattens the rows into group headers, rows and subtotals so paging works on what is shown
  const g = opts.groupBy !== undefined && opts.groupBy >= 0 && opts.groupBy < table.columns.length ? opts.groupBy : null;
  const flat: View[] = useMemo(() => {
    if (g === null) return rows.map((x) => ({ kind: "row", idx: x.idx, cells: x.cells }));
    const order: string[] = [], by = new Map<string, typeof rows>();
    for (const x of rows) { const k = cellText(x.cells[g]) || "(none)"; if (!by.has(k)) { by.set(k, []); order.push(k); } by.get(k)!.push(x); }
    const out: View[] = [];
    for (const k of order) {
      const items = by.get(k)!;
      out.push({ kind: "group", label: k, count: items.length });
      items.forEach((x) => out.push({ kind: "row", idx: x.idx, cells: x.cells }));
      out.push({ kind: "sub", label: `Subtotal · ${k}`, sums: table.columns.map((c, i) => (c.type === "number" && i !== g ? items.reduce((a, x) => a + (num(x.cells[i], 0)), 0) : null)) });
    }
    return out;
  }, [rows, g, table.columns]);

  const pages = Math.max(1, Math.ceil(flat.length / size)), at = Math.min(page, pages - 1);
  const shown = flat.slice(at * size, at * size + size);
  const view = (): { c: Col[]; r: Cell[][] } => ({ c: cols, r: rows.map((x) => cols.map((c) => x.cells[c.i])) });
  const file = fileSafe(title, "table");
  const exportAs = (kind: "csv" | "json" | "xlsx" | "md" | "html") => {
    const { c, r } = view();
    if (kind === "csv") downloadFile(`${file}.csv`, toCsv(c, r), "text/csv;charset=utf-8");
    else if (kind === "json") downloadFile(`${file}.json`, toJson(c, r), "application/json");
    else if (kind === "md") downloadFile(`${file}.md`, toMarkdown(c, r), "text/markdown;charset=utf-8");
    else if (kind === "html") downloadFile(fileName(title, "html"), htmlPage(title, { table: { head: c.map((x) => x.label), rows: r.map((row) => row.map(cellText)) } }), "text/html;charset=utf-8");
    else downloadFile(`${file}.xlsx`, toXlsx(c, r, title) as BlobPart, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  };
  const copy = () => {
    const { c, r } = view();
    const sel = picked.size ? rows.filter((x) => picked.has(x.idx)).map((x) => cols.map((cc) => x.cells[cc.i])) : r;
    navigator.clipboard?.writeText(toTsv(c, sel)).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); });
  };
  const toggleSort = (col: number) => { setSort((s) => (!s || s.col !== col ? { col, dir: 1 } : s.dir === 1 ? { col, dir: -1 } : null)); setPage(0); };
  const toggleCol = (i: number) => setHidden((h) => { const n = new Set(h); if (n.has(i)) n.delete(i); else if (n.size < table.columns.length - 1) n.add(i); return n; });
  const toggleCell = (k: string) => setOpen((o) => { const n = new Set(o); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const togglePick = (idx: number) => setPicked((p) => { const n = new Set(p); if (n.has(idx)) n.delete(idx); else n.add(idx); return n; });
  const rowCount = rows.length;

  return (
    <figure className={`data-table${dense ? " dense" : ""}${opts.pinFirst ? " pin-first" : ""}`} data-testid="data-table" ref={root}>
      {table.title && <figcaption><b>{table.title}</b></figcaption>}
      <div className="dt-bar">
        <input type="search" aria-label="Filter rows" placeholder="Filter rows" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
        <details className="dt-menu"><summary>Columns</summary>
          <div className="dt-pop" role="group" aria-label="Show or hide columns">
            {table.columns.map((c, i) => <label key={i} className="row"><input type="checkbox" checked={!hidden.has(i)} onChange={() => toggleCol(i)} /> {c.label}</label>)}
          </div></details>
        <button className="ghost small" onClick={copy}>{copied ? "Copied" : picked.size ? `Copy ${picked.size} selected` : "Copy"}</button>
        <button className="ghost small" aria-pressed={dense} onClick={() => setDense((d) => !d)}>{dense ? "Comfortable" : "Compact"}</button>
        <details className="dt-menu"><summary>Export</summary>
          <div className="dt-pop" role="group" aria-label="Export the rows and columns shown">
            <div className="small mut">Exports the rows and columns you are showing.</div>
            <button className="ghost small" onClick={() => exportAs("csv")}>CSV</button>
            <button className="ghost small" onClick={() => exportAs("json")}>JSON</button>
            <button className="ghost small" onClick={() => exportAs("xlsx")}>Excel (.xlsx)</button>
            <button className="ghost small" onClick={() => exportAs("md")}>Markdown</button>
            <button className="ghost small" onClick={() => exportAs("html")}>Web page (HTML)</button>
            <button className="ghost small" onClick={() => { if (root.current) printElement(root.current); }}>PDF (print)</button>
          </div></details>
      </div>
      <div className="rich-scroll">
        <table>
          <thead><tr>{opts.select && <th scope="col" className="dt-sel"><span className="sr-only">Select</span></th>}{cols.map((c) => (
            <th key={c.i} scope="col" className={c.type === "number" ? "num" : ""} aria-sort={sort?.col === c.i ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
              <button className="th-sort" onClick={() => toggleSort(c.i)} title={`Sort by ${c.label}`}>{c.label}<span aria-hidden="true">{sort?.col === c.i ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</span></button></th>))}</tr></thead>
          <tbody>
            {shown.map((x, n) => {
              const span = cols.length + (opts.select ? 1 : 0);
              if (x.kind === "group") return <tr key={`g${at}-${n}`} className="dt-group"><th colSpan={span} scope="colgroup">{x.label} <span className="mut small">· {x.count} row{x.count === 1 ? "" : "s"}</span></th></tr>;
              if (x.kind === "sub") return <tr key={`s${at}-${n}`} className="dt-sub">{opts.select && <td />}{cols.map((c, ci) => <td key={c.i} className={c.type === "number" ? "num" : ""}>{ci === 0 ? x.label : x.sums[c.i] === null ? "" : showCell(c, x.sums[c.i])}</td>)}</tr>;
              return (
                <tr key={x.idx} className={picked.has(x.idx) ? "picked" : ""}>{opts.select && <td className="dt-sel"><input type="checkbox" aria-label="Select row" checked={picked.has(x.idx)} onChange={() => togglePick(x.idx)} /></td>}
                  {cols.map((c) => {
                    const t = showCell(c, x.cells[c.i]), k = `${x.idx}:${c.i}`, long = t.length > LONG && !c.format, full = open.has(k);
                    return <td key={c.i} className={c.type === "number" ? "num" : ""}>
                      {long && !full ? t.slice(0, LONG) + "…" : <CellView col={c} v={x.cells[c.i]} range={ranges[c.i]} />}
                      {long && <button className="link small" aria-expanded={full} onClick={() => toggleCell(k)}>{full ? " less" : " more"}</button>}</td>;
                  })}</tr>);
            })}
            {!shown.length && <tr><td colSpan={Math.max(1, cols.length)} className="mut">No rows match this filter.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="dt-foot small">
        <span className="mut">{rowCount === table.rows.length ? `${rowCount} row${rowCount === 1 ? "" : "s"}` : `${rowCount} of ${table.rows.length} rows`}{table.truncated ? ` (the first ${table.rows.length} only)` : ""}</span>
        {flat.length > SIZES[0] && <span className="dt-pager">
          <label>Rows per page <select value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(0); }}>{SIZES.map((s) => <option key={s}>{s}</option>)}</select></label>
          <button className="ghost small" disabled={at === 0} onClick={() => setPage(at - 1)}>Previous</button>
          <span>Page {at + 1} of {pages}</span>
          <button className="ghost small" disabled={at >= pages - 1} onClick={() => setPage(at + 1)}>Next</button></span>}
        {!plain && <BlockActions canvas={{ type: "table" }} label="table" name={`${file}.csv`} content={() => { const { c, r } = view(); return toCsv(c, r); }} />}
      </div>
    </figure>
  );
}
