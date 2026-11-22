"use client";
// JSON as a collapsible tree: search (opens the branches that match), copy a path or a value, download the document. Big documents stay responsive:
// a branch only draws its children when open, and long lists are shown 100 at a time.
import { useMemo, useState } from "react";
import { downloadFile, fileSafe } from "@/lib/tables";
import BlockActions from "./BlockActions";

const CHUNK = 100, MAX_WALK = 20000, MAX_TEXT = 300;
type J = unknown;
const isBranch = (v: J): v is Record<string, J> | J[] => v !== null && typeof v === "object";
const entries = (v: Record<string, J> | J[]): [string, J][] => (Array.isArray(v) ? v.map((x, i) => [String(i), x] as [string, J]) : Object.entries(v));
const pathOf = (parent: string, key: string, inArray: boolean) => (inArray ? `${parent}[${key}]` : /^[A-Za-z_$][\w$]*$/.test(key) ? (parent ? `${parent}.${key}` : key) : `${parent}[${JSON.stringify(key)}]`);
const preview = (v: J) => (typeof v === "string" ? JSON.stringify(v.length > MAX_TEXT ? v.slice(0, MAX_TEXT) + "…" : v) : String(v));

/** The paths that match the search, and every ancestor of a match (so those branches open). Walks at most MAX_WALK nodes. */
export function findMatches(root: J, q: string): { hits: Set<string>; open: Set<string>; cut: boolean } {
  const hits = new Set<string>(), open = new Set<string>();
  const needle = q.trim().toLowerCase();
  let n = 0, cut = false;
  const walk = (v: J, path: string, key: string): boolean => {
    if (++n > MAX_WALK) { cut = true; return false; }
    let found = (key + " " + (isBranch(v) ? "" : String(v))).toLowerCase().includes(needle);
    if (found) hits.add(path);
    if (isBranch(v)) { const arr = Array.isArray(v); for (const [k, c] of entries(v)) if (walk(c, pathOf(path, k, arr), arr ? "" : k)) found = true; if (found) open.add(path); }
    return found;
  };
  if (needle) walk(root, "", "");
  return { hits, open, cut };
}

function Node({ name, label, value, path, depth, ctx }: { name: string; label: string; value: J; path: string; depth: number; ctx: Ctx }) {
  const [more, setMore] = useState(CHUNK);
  const branch = isBranch(value);
  const isOpen = ctx.toggled[path] ?? (depth < 1 || ctx.match.open.has(path));
  const copy = (text: string, what: string) => navigator.clipboard?.writeText(text).then(() => ctx.say(`${what} copied`));
  const kids = branch && isOpen ? entries(value) : [];
  const arr = Array.isArray(value);
  return (
    <li className={ctx.match.hits.has(path) ? "jv-hit" : ""}>
      <span className="jv-row">
        {branch
          ? <button className="jv-toggle" aria-expanded={isOpen} onClick={() => ctx.toggle(path, !isOpen)}>{isOpen ? "▾" : "▸"} {name && <b>{name}</b>}<span className="mut"> {arr ? `[${(value as J[]).length}]` : `{${Object.keys(value as object).length}}`}</span></button>
          : <span className="jv-leaf">{name && <b>{name}: </b>}<span className={`jv-${value === null ? "null" : typeof value}`}>{preview(value)}</span></span>}
        <span className="jv-acts">
          <button className="link small" aria-label={`Copy path of ${label || "root"}`} onClick={() => copy(path || "$", "Path")}>path</button>
          <button className="link small" aria-label={`Copy value of ${label || "root"}`} onClick={() => copy(typeof value === "string" ? value : JSON.stringify(value, null, 2), "Value")}>value</button>
        </span>
      </span>
      {kids.length > 0 && (
        <ul>
          {kids.slice(0, more).map(([k, v]) => <Node key={k} name={arr ? "" : k} label={k} value={v} path={pathOf(path, k, arr)} depth={depth + 1} ctx={ctx} />)}
          {kids.length > more && <li><button className="link small" onClick={() => setMore(more + CHUNK)}>Show {Math.min(CHUNK, kids.length - more)} more of {kids.length - more} left</button></li>}
        </ul>)}
    </li>
  );
}
type Ctx = { toggled: Record<string, boolean>; toggle: (p: string, v: boolean) => void; match: ReturnType<typeof findMatches>; say: (s: string) => void };

export default function JsonViewer({ value, text, title = "", plain = false }: { value?: J; text?: string; title?: string; plain?: boolean }) {
  const [q, setQ] = useState("");
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const [note, setNote] = useState("");
  const match = useMemo(() => (value === undefined ? { hits: new Set<string>(), open: new Set<string>(), cut: false } : findMatches(value, q)), [value, q]);
  const say = (s: string) => { setNote(s); setTimeout(() => setNote(""), 1200); };
  const doc = () => (text !== undefined ? text : JSON.stringify(value, null, 2));
  const file = `${fileSafe(title, "data")}.json`;
  const ctx: Ctx = { toggled, toggle: (p, v) => setToggled((t) => ({ ...t, [p]: v })), match, say };
  return (
    <figure className="json-viewer" data-testid="json-viewer">
      {title && <figcaption><b>{title}</b></figcaption>}
      <div className="dt-bar">
        {value !== undefined && <input type="search" aria-label="Search the JSON" placeholder="Search keys and values" value={q} onChange={(e) => setQ(e.target.value)} />}
        <button className="ghost small" onClick={() => downloadFile(file, doc(), "application/json")}>Download</button>
        {note && <span className="small mut" role="status">{note}</span>}
      </div>
      {value === undefined
        ? <div><div className="small mut" role="note">This document is too large to explore here, so the start of it is shown as text.</div><pre className="json-raw">{text}</pre></div>
        : <ul className="jv-tree" aria-label={title || "JSON"}><Node name="" label="" value={value} path="" depth={0} ctx={ctx} /></ul>}
      {match.cut && <div className="small mut">Search stopped after {MAX_WALK} items.</div>}
      <div className="dt-foot small"><span className="mut" />{!plain && <BlockActions canvas={{ type: "code", language: "json" }} label="JSON" name={file} content={doc} />}</div>
    </figure>
  );
}
