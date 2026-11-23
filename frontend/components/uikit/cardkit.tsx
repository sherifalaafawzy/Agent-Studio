"use client";
// Shared parts of the cards: the frame, safe links and images, a tiny sparkline, a status pill and a copy button. Cards render text as text (never markdown or HTML), links only
// through safeUrl, and images only from this app (an artifact or an embedded picture): a remote image would let an answer report where you are.
import { ReactNode, useContext, useState } from "react";
import { artifactUrl } from "@/lib/artifacts";
import type { Table } from "@/lib/tables";
import { Any, arr, finite, num, str } from "@/lib/uikit/data";
import { safeUrl, type Spec } from "@/lib/uikit/safe";
import { ReplyContext } from "../rich/ReplyContext";
import UiFrame from "./UiFrame";

export const useReply = () => useContext(ReplyContext);

export function Card({ spec, summary, table, children, title }: { spec: Spec; summary: string; table?: Table | null; children: ReactNode; title?: string }) {
  return <UiFrame spec={spec} title={title} summary={summary} table={table} testId="card"><div className={`uc uc-${spec.type.split(".")[1]}`}>{children}</div></UiFrame>;
}

/** A link that is safe to follow: https, mailto, tel or a page of this app. Otherwise plain text. */
export function SafeLink({ url, children }: { url: unknown; children?: ReactNode }) {
  const href = safeUrl(url);
  if (!href) return <>{children ?? str(url, 80)}</>;
  const external = /^https:/i.test(href);
  return <a href={href} {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{children ?? href}</a>;
}

/** An image from this app only (an artifact or an embedded picture). */
export function CardImage({ src, alt, className = "uc-img" }: { src: unknown; alt: string; className?: string }) {
  const raw = String(src ?? ""), s = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(raw) || /^[0-9a-f]{12}$/.test(raw) ? raw : null;       // only pictures that cannot report back: embedded ones and our own artifacts
  if (!s) return src ? <span className="img-blocked" title="Remote images are not loaded, so an answer cannot send your data to another site.">🖼 {alt || "image"} · remote image not loaded</span> : null;
  return <img className={className} src={/^[0-9a-f]{12}$/.test(s) ? artifactUrl(s) : s} alt={alt} loading="lazy" />;
}

export function Spark({ values, w = 90, h = 26 }: { values: unknown; w?: number; h?: number }) {
  const v = arr(values, 200).map((x) => num(x)).filter(finite);
  if (v.length < 2) return null;
  const lo = Math.min(...v), hi = Math.max(...v), y = (n: number) => h - 3 - ((n - lo) / (hi - lo || 1)) * (h - 6);
  return <svg className="uc-spark" viewBox={`0 0 ${w} ${h}`} width={w} height={h} role="img" aria-label={`Trend over ${v.length} points, from ${v[0]} to ${v[v.length - 1]}`}><polyline fill="none" strokeWidth={2} strokeLinejoin="round" style={{ stroke: "var(--viz-1)" }} points={v.map((n, i) => `${(i / (v.length - 1)) * (w - 4) + 2},${y(n)}`).join(" ")} /></svg>;
}

export type Tone = "ok" | "warn" | "bad" | "info" | "";
const ICON: Record<string, string> = { ok: "✓", warn: "!", bad: "✕", info: "i", "": "•" };
export const Pill = ({ tone = "", children }: { tone?: Tone; children: ReactNode }) => <span className={`pill ${tone === "info" ? "acc" : tone}`}><span aria-hidden="true">{ICON[tone]} </span>{children}</span>;
export const toneOf = (s: unknown): Tone => { const t = String(s ?? "").toLowerCase(); return /^(ok|done|good|success|healthy|passed|complete|active|approved)/.test(t) ? "ok" : /^(warn|medium|waiting|pending|slow)/.test(t) ? "warn" : /^(bad|error|fail|critical|high|blocked|overdue|rejected)/.test(t) ? "bad" : /^(info|low|todo)/.test(t) ? "info" : ""; };

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <button className="ghost small" onClick={() => { navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); }); }}>{done ? "Copied" : label}</button>;
}

/** label: value pairs. */
export function Facts({ items }: { items: unknown }) {
  const rows = arr(items, 30).filter((r) => r && (r.label || r.value));
  return rows.length ? <dl className="uc-facts">{rows.map((r, i) => <div key={i}><dt>{str(r.label, 40)}</dt><dd>{str(r.value, 120)}</dd></div>)}</dl> : null;
}

export const Links = ({ items }: { items: unknown }) => { const l = arr(items, 10).filter((x) => x?.url); return l.length ? <div className="uc-links">{l.map((x, i) => <SafeLink key={i} url={x.url}>{str(x.label || x.url, 60)}</SafeLink>)}</div> : null; };
export const rec = (v: unknown): Any => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
