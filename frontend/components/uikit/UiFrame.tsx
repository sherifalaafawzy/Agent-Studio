"use client";
// The frame every component sits in: a title, an accessible summary, a Visual | Table switch (a chart or map is never the only way to read its numbers), an Export menu
// and the Keep / Open in Artifacts / Open in canvas buttons.
import { ReactNode, useRef, useState } from "react";
import { cellText, downloadFile, fileSafe, toCsv, toJson, toMarkdown, toXlsx, type Table } from "@/lib/tables";
import { exportPng, exportSvg, fileName, htmlPage, inlineSvg, printElement } from "@/lib/uikit/exporters";
import type { Spec } from "@/lib/uikit/safe";
import BlockActions from "../rich/BlockActions";
import DataTable from "../rich/DataTable";

export type Exporter = { label: string; run: () => void | Promise<void> };

type Props = {
  spec: Spec; title?: string; summary: string; children: ReactNode;
  table?: Table | null;                 // the same data as a table (adds the Table view and the data exports)
  svgRef?: React.RefObject<SVGSVGElement | null>;   // the picture (adds SVG and PNG exports)
  exporters?: Exporter[];               // extra exports (GeoJSON...)
  toolbar?: ReactNode;                  // controls shown above the visual
  foot?: ReactNode;                     // legend and notes below it
  bare?: boolean;                       // a small card: no export menu, only the visual
  testId?: string;
};

export default function UiFrame({ spec, title, summary, children, table, svgRef, exporters = [], toolbar, foot, bare, testId }: Props) {
  const [view, setView] = useState<"visual" | "table">("visual");
  const [err, setErr] = useState("");
  const root = useRef<HTMLElement>(null);
  const caption = title || spec.title || "", name = caption || spec.type;
  const file = fileSafe(String(name), "component");
  const guard = (fn: () => void | Promise<void>) => async () => { try { setErr(""); await fn(); } catch (e) { setErr((e as Error).message); } };
  const specJson = () => JSON.stringify(spec, null, 2);

  const items: Exporter[] = [];
  if (table) items.push(
    { label: "Table as CSV", run: () => downloadFile(`${file}.csv`, toCsv(table.columns, table.rows), "text/csv;charset=utf-8") },
    { label: "Table as JSON", run: () => downloadFile(`${file}.json`, toJson(table.columns, table.rows), "application/json") },
    { label: "Table as Excel (.xlsx)", run: () => downloadFile(`${file}.xlsx`, toXlsx(table.columns, table.rows, String(name)) as BlobPart, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") },
    { label: "Table as Markdown", run: () => downloadFile(`${file}.md`, toMarkdown(table.columns, table.rows), "text/markdown;charset=utf-8") });
  if (svgRef) items.push(
    { label: "Picture (SVG)", run: () => { if (svgRef.current) exportSvg(svgRef.current, String(name)); } },
    { label: "Picture (PNG)", run: () => (svgRef.current ? exportPng(svgRef.current, String(name)) : undefined) });
  items.push(...exporters,
    { label: "Web page (HTML)", run: () => downloadFile(fileName(String(name), "html"), htmlPage(String(name), {
      svg: svgRef?.current ? inlineSvg(svgRef.current).text : undefined,
      table: table ? { head: table.columns.map((c) => c.label), rows: table.rows.map((r) => r.map(cellText)) } : undefined, note: summary }), "text/html;charset=utf-8") },
    { label: "PDF (print)", run: () => { if (root.current) printElement(root.current); } },
    { label: "Component (JSON)", run: () => downloadFile(`${file}.component.json`, specJson(), "application/json") });

  return (
    <figure className="ui-frame" data-testid={testId ?? "ui-frame"} data-type={spec.type} ref={root}>
      {(caption && spec.type !== "table") || toolbar || table ? (
        <div className="ui-head">
          {caption && <figcaption><b>{String(caption)}</b>{spec.description ? <span className="mut small"> · {String(spec.description)}</span> : null}</figcaption>}
          {table && <button className="ghost small" aria-pressed={view === "table"} onClick={() => setView(view === "table" ? "visual" : "table")}>{view === "table" ? "Show visual" : "Show as table"}</button>}
        </div>
      ) : null}
      <p className="sr-only">{summary}</p>
      {toolbar && view === "visual" && <div className="ui-toolbar">{toolbar}</div>}
      {view === "table" && table ? <DataTable table={table} plain /> : children}
      {foot && view === "visual" && <div className="ui-foot">{foot}</div>}
      {!bare && (
        <div className="ui-actions">
          <details className="dt-menu"><summary>Export</summary>
            <div className="dt-pop" role="group" aria-label="Export">{items.map((x) => <button key={x.label} className="ghost small" onClick={guard(x.run)}>{x.label}</button>)}</div></details>
          <BlockActions canvas={{ type: "code", language: "json" }} label="component" name={`${file}.json`} content={specJson} />
          {err && <span className="small err" role="alert">{err}</span>}
        </div>)}
    </figure>
  );
}
