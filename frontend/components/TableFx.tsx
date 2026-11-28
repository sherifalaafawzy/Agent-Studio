"use client";
// One behaviour for the app's own data tables when they do not fit: the table becomes a list of small cards (one per row, each
// value beside its column name) instead of scrolling sideways inside its card. That happens
//   - on a phone (640px and narrower) for every table with three or more columns, and
//   - at any width for a table that is wider than the room it has (a nine-column matrix on a tablet, say).
// The stylesheet does the layout (globals.css, 'table[data-stack="1"]'); it needs each cell to know its column's name, so this
// copies the header text onto the cells as data-label. It only adds data- attributes React never sets, on tables that are drawn
// after the page has its data, and leaves alone tables in answers, documents and the canvas (they keep their own scrolling) and
// tables that already label their cells.
import { useEffect } from "react";
import { usePathname } from "next/navigation";

const SKIP = ".md, .turn-bot, .rich-scroll, .doc-body, .canvas, .wf-table, .dt, [data-table-keep]";
export const MIN_COLUMNS = 3;
export const PHONE = 640;

const needs = new WeakMap<HTMLTableElement, number>();                  // the width a stacked table needed when it last overflowed

/** Should this table be drawn as cards? Pure apart from the remembered width, so it can be tested. */
export function shouldStack(table: HTMLTableElement, columns: number, viewport: number): boolean {
  if (columns >= MIN_COLUMNS && viewport <= PHONE) return true;
  const room = table.parentElement?.clientWidth ?? 0;
  if (room <= 0) return false;                                           // hidden, or not laid out yet
  if (table.dataset.stack === "1") return room < (needs.get(table) ?? 0);   // stays stacked until there is room for what it needed
  const wide = Math.max(table.offsetWidth, table.scrollWidth);        // some tables scroll inside themselves: then only scrollWidth shows the overflow
  if (wide > room + 2) { needs.set(table, wide); return true; }
  return false;
}

/** Label one table's cells from its header row and mark whether it is stacked. Returns true when it is. */
export function labelTable(table: HTMLTableElement, viewport = typeof window === "undefined" ? 1024 : window.innerWidth): boolean {
  if (table.closest(SKIP)) return false;
  const heads = [...table.querySelectorAll<HTMLTableCellElement>(":scope > thead > tr:first-child > th")];
  if (heads.length < 2) return false;
  const names = heads.map((h) => (h.textContent ?? "").replace(/\s+/g, " ").trim());
  for (const row of table.querySelectorAll<HTMLTableRowElement>(":scope > tbody > tr")) {
    const cells = [...row.children] as HTMLElement[];
    if (cells.length !== names.length) continue;                       // a spanning row (details, "nothing here") keeps its own layout
    cells.forEach((c, i) => { if (c.dataset.label !== names[i]) c.dataset.label = names[i]; });
  }
  const stack = shouldStack(table, heads.length, viewport) ? "1" : "0";
  if (table.dataset.stack !== stack) table.dataset.stack = stack;
  return stack === "1";
}

export default function TableFx() {
  const path = usePathname();
  useEffect(() => {
    let frame = 0;
    const sweep = () => document.querySelectorAll<HTMLTableElement>(".main table").forEach((t) => labelTable(t));
    const soon = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(sweep); };
    const seen = new MutationObserver(soon);                            // rows arrive after data loads
    seen.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", soon);
    soon();
    return () => { cancelAnimationFrame(frame); seen.disconnect(); window.removeEventListener("resize", soon); };
  }, [path]);
  return null;
}
