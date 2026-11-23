"use client";
// Rich answers: charts, diffs, tables, forms, images and file cards. Everything here is a fixed, safe set of pieces built as React
// elements from validated data: a model can ask for a chart or a form, but it can never inject HTML, script or a remote image.
import { createContext, useContext, useState } from "react";
import Link from "next/link";
import { Artifact, artifactUrl, agoText, copyState, fmtBytes, isOurArtifactUrl, TYPE_LABEL } from "@/lib/artifacts";
import { IDownload, IFile } from "./Icons";
import BlockActions, { KeepContext } from "./rich/BlockActions";
import DataTable from "./rich/DataTable";
import HtmlPreview from "./rich/HtmlPreview";
import JsonViewer from "./rich/JsonViewer";
import MediaBlock, { parseMedia } from "./rich/MediaBlock";
import UiBlock from "./uikit/UiBlock";
import { ReplyContext } from "./rich/ReplyContext";
import { parseTable, splitLine, tableFromDelimited } from "@/lib/tables";

export { KeepContext, splitLine };

/** The chat provides this so a form in an answer can send the user's reply as their next message. */
export { ReplyContext };

const clip = (v: unknown, n: number) => String(v ?? "").slice(0, n);

// ---------------------------------------------------------------------------------------------------- images
const DATA_IMG = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/;

/** Only images that cannot report back to anyone are drawn: pictures embedded in the answer, and Agent Studio's own artifacts. Remote images are
 *  how a hidden instruction can leak what you typed (the address carries the data), so they are shown as a note, not loaded. */
export function SafeImage({ alt, url }: { alt: string; url: string }) {
  if (DATA_IMG.test(url) || isOurArtifactUrl(url)) return <img className="md-img" src={url} alt={alt} loading="lazy" />;
  let host = "";
  try { host = new URL(url).hostname; } catch { /* not a URL: show no host */ }
  return <span className="img-blocked" title="Remote images are not loaded, so an answer cannot send your data to another site.">🖼 {alt || "image"} · remote image not loaded{host ? ` (${host})` : ""}</span>;
}

// ---------------------------------------------------------------------------------------------------- charts
export type ChartSpec = { type: "bar" | "line"; title: string; unit: string; labels: string[]; series: { name: string; values: number[] }[] };
const MAX_SERIES = 6, MAX_POINTS = 200;

/** Check a chart the model asked for. Anything unexpected is refused with a reason (and shown as code instead). */
export function parseChart(text: string): { spec?: ChartSpec; error?: string } {
  let raw: any;
  try { raw = JSON.parse(text); } catch { return { error: "the chart is not valid JSON" }; }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { error: "a chart is a JSON object" };
  const type = raw.type === undefined ? "bar" : raw.type;
  if (type !== "bar" && type !== "line") return { error: "type must be bar or line" };
  let labels: string[], series: { name: string; values: number[] }[];
  if (Array.isArray(raw.data)) {
    labels = raw.data.map((d: any) => clip(d?.label, 40));
    series = [{ name: clip(raw.name ?? raw.title ?? "Value", 40), values: raw.data.map((d: any) => d?.value) }];
  } else if (Array.isArray(raw.x) && Array.isArray(raw.series)) {
    labels = raw.x.map((l: unknown) => clip(l, 40));
    series = raw.series.map((s: any) => ({ name: clip(s?.name, 40), values: Array.isArray(s?.values) ? s.values : [] }));
  } else return { error: "give either data: [{label, value}] or x: [...] with series: [{name, values}]" };
  if (!labels.length || labels.length > MAX_POINTS) return { error: `a chart needs 1 to ${MAX_POINTS} points` };
  if (!series.length || series.length > MAX_SERIES) return { error: `a chart needs 1 to ${MAX_SERIES} series` };
  for (const s of series) if (s.values.length !== labels.length || s.values.some((v) => typeof v !== "number" || !Number.isFinite(v))) return { error: `series "${s.name}" must have one number for each of the ${labels.length} points` };
  return { spec: { type, title: clip(raw.title, 80), unit: clip(raw.unit, 12), labels, series } };
}

const niceMax = (v: number) => {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
};
const fmtNum = (n: number) => (Math.abs(n) >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : Math.abs(n) >= 1e4 ? `${+(n / 1e3).toFixed(1)}k` : `${+n.toFixed(2)}`);
const W = 480, H = 220, L = 44, R = 8, T = 10, B = 28;

export function Chart({ spec }: { spec: ChartSpec }) {
  const [table, setTable] = useState(false);
  const all = spec.series.flatMap((s) => s.values);
  const lo = Math.min(0, ...all), hi = niceMax(Math.max(...all, 0)), range = hi - lo;
  const pw = W - L - R, ph = H - T - B, n = spec.labels.length;
  const y = (v: number) => T + ph - ((v - lo) / range) * ph;
  const group = pw / n, k = spec.series.length;
  const bw = Math.max(2, Math.min(28, (group * 0.8) / k - 2));
  const cx = (i: number) => L + group * (i + 0.5);
  const step = Math.ceil(n / 8);
  const ticks = [0, 1, 2, 3, 4].map((t) => lo + (range * t) / 4);
  const summary = `${spec.title || "Chart"}: ${spec.series.map((s) => s.name).join(", ")} across ${n} points`;
  return (
    <figure className="chart" data-testid="chart">
      {spec.title && <figcaption><b>{spec.title}</b>{spec.unit && <span className="mut"> · {spec.unit}</span>}</figcaption>}
      {table ? (
        <div className="rich-scroll"><table><thead><tr><th /> {spec.series.map((s) => <th key={s.name}>{s.name}</th>)}</tr></thead>
          <tbody>{spec.labels.map((l, i) => <tr key={i}><th>{l}</th>{spec.series.map((s) => <td key={s.name}>{fmtNum(s.values[i])}</td>)}</tr>)}</tbody></table></div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} className="chart-svg">
          {ticks.map((t, i) => <g key={i}><line className="viz-grid" x1={L} x2={W - R} y1={y(t)} y2={y(t)} /><text className="chart-tick" x={L - 6} y={y(t) + 3} textAnchor="end">{fmtNum(t)}</text></g>)}
          <line className="viz-axis" x1={L} x2={W - R} y1={y(0)} y2={y(0)} />
          {spec.labels.map((l, i) => i % step === 0 && <text key={i} className="chart-tick" x={cx(i)} y={H - 10} textAnchor="middle">{l.length > 10 ? l.slice(0, 9) + "…" : l}</text>)}
          {spec.type === "bar" && spec.series.map((s, si) => s.values.map((v, i) => {
            const x = cx(i) - (k * (bw + 2)) / 2 + si * (bw + 2) + 1, top = Math.min(y(v), y(0)), h = Math.max(1, Math.abs(y(v) - y(0)));
            return <rect key={`${si}-${i}`} x={x} y={top} width={bw} height={h} rx={2} style={{ fill: `var(--viz-${si + 1})` }}><title>{`${spec.labels[i]} · ${s.name}: ${fmtNum(v)}${spec.unit ? " " + spec.unit : ""}`}</title></rect>;
          }))}
          {spec.type === "line" && spec.series.map((s, si) => (
            <g key={si}>
              <polyline fill="none" strokeWidth={2} strokeLinejoin="round" style={{ stroke: `var(--viz-${si + 1})` }} points={s.values.map((v, i) => `${cx(i)},${y(v)}`).join(" ")} />
              {s.values.map((v, i) => <circle key={i} cx={cx(i)} cy={y(v)} r={n > 40 ? 2.5 : 4} className="chart-dot" style={{ fill: `var(--viz-${si + 1})` }}><title>{`${spec.labels[i]} · ${s.name}: ${fmtNum(v)}${spec.unit ? " " + spec.unit : ""}`}</title></circle>)}
            </g>))}
        </svg>
      )}
      <div className="chart-foot">
        {k > 1 && <span className="chart-legend">{spec.series.map((s, si) => <span key={s.name}><i className="chart-swatch" style={{ background: `var(--viz-${si + 1})` }} />{s.name}</span>)}</span>}
        <button className="ghost small" aria-pressed={table} onClick={() => setTable((v) => !v)}>{table ? "Show chart" : "Show as table"}</button>
        <BlockActions canvas={{ type: "code", language: "json" }} label="chart" name={`${(spec.title || "chart").replace(/[^\w .-]+/g, "").trim().slice(0, 50) || "chart"}.json`} content={() => JSON.stringify({ type: spec.type, title: spec.title, unit: spec.unit, x: spec.labels, series: spec.series }, null, 2)} />
      </div>
    </figure>
  );
}

// ---------------------------------------------------------------------------------------------------- diffs and tables
export function DiffBlock({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n").slice(0, 2000);
  return (
    <div className="codeblock diff" data-testid="diff"><div className="cb-head"><span>diff</span><BlockActions canvas={{ type: "code" }} label="diff" name="changes.diff" content={() => text} /></div>
      <pre><code>{lines.map((l, i) => <span key={i} className={l.startsWith("+++") || l.startsWith("---") ? "d-file" : l.startsWith("@@") ? "d-hunk" : l.startsWith("+") ? "d-add" : l.startsWith("-") ? "d-del" : ""}>{l + "\n"}</span>)}</code></pre>
    </div>
  );
}

export function CsvTable({ text, sep = ",", plain = false }: { text: string; sep?: string; plain?: boolean }) {
  const table = tableFromDelimited(text, sep);
  if (!table) return <div className="small mut">(empty table)</div>;
  return <DataTable table={table} name={sep === "	" ? "table.tsv" : "table"} plain={plain} />;
}

// ---------------------------------------------------------------------------------------------------- forms
type Field = { name: string; label: string; type: "text" | "select" | "checkbox" | "number"; options: string[]; required: boolean };

export function parseForm(text: string): { form?: { title: string; submit: string; fields: Field[] }; error?: string } {
  let raw: any;
  try { raw = JSON.parse(text); } catch { return { error: "the form is not valid JSON" }; }
  if (!raw || !Array.isArray(raw.fields) || !raw.fields.length || raw.fields.length > 12) return { error: "a form needs 1 to 12 fields" };
  const fields: Field[] = [];
  for (const f of raw.fields) {
    const type = f?.type ?? "text";
    if (!["text", "select", "checkbox", "number"].includes(type)) return { error: "field type must be text, select, checkbox or number" };
    const options = Array.isArray(f.options) ? f.options.slice(0, 20).map((o: unknown) => clip(o, 60)) : [];
    if (type === "select" && !options.length) return { error: "a select field needs options" };
    fields.push({ name: clip(f.name || f.label, 40), label: clip(f.label || f.name, 80), type, options, required: !!f.required });
  }
  return { form: { title: clip(raw.title, 100), submit: clip(raw.submit || "Send", 30), fields } };
}

export function FormBlock({ form }: { form: NonNullable<ReturnType<typeof parseForm>["form"]> }) {
  const reply = useContext(ReplyContext);
  const [vals, setVals] = useState<Record<string, string | boolean>>({});
  const [sent, setSent] = useState(false);
  const ok = form.fields.every((f) => !f.required || (f.type === "checkbox" ? vals[f.name] === true : String(vals[f.name] ?? "").trim() !== ""));
  const submit = () => {
    const lines = form.fields.map((f) => `- ${f.label}: ${f.type === "checkbox" ? (vals[f.name] ? "yes" : "no") : String(vals[f.name] ?? "").trim() || "(not answered)"}`);
    reply?.(`${form.title ? form.title + "\n" : ""}${lines.join("\n")}`);
    setSent(true);
  };
  return (
    <form className="rich-form" data-testid="form" onSubmit={(e) => { e.preventDefault(); if (ok && reply && !sent) submit(); }}>
      {form.title && <b>{form.title}</b>}
      {form.fields.map((f) => (
        <label key={f.name} className={f.type === "checkbox" ? "row" : ""}><span>{f.label}{f.required ? " *" : ""}</span>
          {f.type === "select" ? <select disabled={sent || !reply} value={String(vals[f.name] ?? "")} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}><option value="">Choose…</option>{f.options.map((o) => <option key={o}>{o}</option>)}</select>
            : f.type === "checkbox" ? <input type="checkbox" disabled={sent || !reply} checked={vals[f.name] === true} onChange={(e) => setVals({ ...vals, [f.name]: e.target.checked })} />
            : <input type={f.type} disabled={sent || !reply} value={String(vals[f.name] ?? "")} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />}
        </label>))}
      <div className="row"><button className="acc" type="submit" disabled={!ok || !reply || sent}>{sent ? "Sent" : form.submit}</button>
        {!reply && <span className="small mut">Answer in the chat box.</span>}</div>
    </form>
  );
}

// ---------------------------------------------------------------------------------------------------- the code-fence dispatcher
const RICH = ["chart", "diff", "patch", "csv", "tsv", "form", "table", "jsonview", "html-preview", "audio", "video", "ui"];
export const isRichLang = (lang: string) => RICH.includes(lang.toLowerCase());

/** A fenced block that asks for one of the whitelisted pieces. Returns null when it is not one (or is invalid): the caller shows it as code. */
export function RichFence({ lang, code }: { lang: string; code: string }) {
  const l = lang.toLowerCase();
  if (l === "ui") return <UiBlock spec={code} />;
  if (l === "diff" || l === "patch") return <DiffBlock text={code} />;
  if (l === "table") {
    let raw: unknown = null;
    try { raw = JSON.parse(code); } catch { return <div className="small mut" role="note">Could not show this table: the table is not valid JSON.</div>; }
    const { table, error } = parseTable(raw);
    return table ? <DataTable table={table} /> : <div className="small mut" role="note">Could not show this table: {error}.</div>;
  }
  if (l === "jsonview") {
    if (code.length > 200_000) return <JsonViewer text={code.slice(0, 200_000)} />;
    try { return <JsonViewer value={JSON.parse(code)} />; } catch { return <div className="small mut" role="note">Could not show this JSON: it is not valid JSON.</div>; }
  }
  if (l === "html-preview") return <HtmlPreview html={code.slice(0, 200_000)} />;
  if (l === "audio" || l === "video") {
    const { media, error } = parseMedia(code);
    return media ? <MediaBlock kind={l} artifact={media.artifact} title={media.title} captions={media.captions} /> : <div className="small mut" role="note">Could not play this {l}: {error}.</div>;
  }
  if (l === "csv" || l === "tsv") return <CsvTable text={code} sep={l === "tsv" ? "\t" : ","} />;
  if (l === "chart") { const { spec, error } = parseChart(code); return spec ? <Chart spec={spec} /> : <div className="small mut" role="note">Could not draw this chart: {error}.</div>; }
  const { form, error } = parseForm(code);
  return form ? <FormBlock form={form} /> : <div className="small mut" role="note">Could not show this form: {error}.</div>;
}

// ---------------------------------------------------------------------------------------------------- files and state
export function ArtifactCard({ a }: { a: Artifact }) {
  const st = copyState(a);
  return (
    <div className="art-card" data-testid="artifact-card">
      {a.preview === "image" && <img className="art-thumb" src={artifactUrl(a.id)} alt={a.name} loading="lazy" />}
      <div className="art-main">
        <IFile size={16} />
        <div className="art-text"><Link href={`/artifacts/?id=${a.id}`} className="art-name">{a.name}</Link>
          <div className="small mut">{TYPE_LABEL[a.type] ?? a.type} · {fmtBytes(a.size)} · {agoText(a.created)} · <span className={`pill ${st.tone}`}>{st.text}</span></div></div>
        <a className="icon-btn" href={artifactUrl(a.id, true)} download={a.name} title="Download" aria-label={`Download ${a.name}`}><IDownload size={16} /></a>
      </div>
    </div>
  );
}

export function ArtifactList({ items }: { items: Artifact[] }) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 4);
  return (
    <div className="art-list" data-testid="artifact-list">
      <div className="small mut">{items.length} file{items.length === 1 ? "" : "s"} made in this turn · <Link href="/artifacts/">all artifacts</Link></div>
      {shown.map((a) => <ArtifactCard key={a.id} a={a} />)}
      {items.length > 4 && <button className="ghost small" onClick={() => setAll((v) => !v)}>{all ? "Show fewer" : `Show all ${items.length}`}</button>}
    </div>
  );
}

export type RunState = { agents: { id: string; name: string; status: string; parent: string | null; cost: number }[]; totals: { model_calls: number; tool_calls: number; cost_usd: number }; artifacts: number };

/** The shared state of a run (agents, totals, files) as a small panel: the same shape the AG-UI stream sends as STATE_SNAPSHOT / STATE_DELTA. */
export function StatePanel({ state }: { state: RunState }) {
  return (
    <details className="state-panel" data-testid="state-panel">
      <summary>Run state · {state.agents.length} agent{state.agents.length === 1 ? "" : "s"} · {state.totals.tool_calls} tool call{state.totals.tool_calls === 1 ? "" : "s"}</summary>
      <div className="state-kpis"><div><b>{state.totals.model_calls}</b><span className="small mut">model calls</span></div><div><b>{state.totals.tool_calls}</b><span className="small mut">tool calls</span></div>
        <div><b>${state.totals.cost_usd.toFixed(4)}</b><span className="small mut">cost</span></div><div><b>{state.artifacts}</b><span className="small mut">files</span></div></div>
      <ul className="state-agents">{state.agents.map((a) => <li key={a.id}><span className={`dot ${a.status === "done" || a.status === "ok" ? "ok" : a.status === "running" ? "warn" : "bad"}`} aria-hidden="true" /><b>{a.name}</b>
        <span className="mut small"> {a.parent ? "helper" : "lead"} · {a.status} · ${a.cost.toFixed(4)}</span></li>)}</ul>
    </details>
  );
}
