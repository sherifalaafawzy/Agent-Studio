"use client";
// Cards, first half: weather, forecast, quote, KPIs, comparison, people and things, sources, files, code, timelines, calendars, tasks, boards, steps, alerts, trips and places.
import { useState } from "react";
import { mkTable } from "./chartkit";
import { arr, finite, fmtBytes, fmtNum, num, str } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import UiBlock from "./UiBlock";
import { Card, CardImage, CopyButton, Facts, Links, Pill, SafeLink, Spark, rec, toneOf, useReply, type Tone } from "./cardkit";

type P = { spec: Spec };

// ---------------------------------------------------------------------------------------------------- weather
const ICONS: [RegExp, string][] = [[/storm|thunder/i, "⛈"], [/snow|sleet|ice/i, "❄"], [/rain|shower|drizzle/i, "🌧"], [/fog|mist|haze/i, "🌫"], [/wind/i, "💨"], [/cloud|overcast/i, "☁"], [/part|few/i, "⛅"], [/sun|clear|fair/i, "☀"]];
const icon = (c: unknown) => ICONS.find(([r]) => r.test(String(c ?? "")))?.[1] ?? "🌡";

function useUnits(spec: Spec) {
  const base = String(spec.units).toUpperCase() === "F" ? "F" : "C", [u, setU] = useState(base);
  const conv = (c: unknown) => { const n = num(c); return finite(n) ? Math.round(u === base ? n : base === "C" ? (n * 9) / 5 + 32 : ((n - 32) * 5) / 9) : NaN; };
  return { u, conv, toggle: () => setU(u === "C" ? "F" : "C") };
}

export function Weather({ spec }: P) {
  const { u, conv, toggle } = useUnits(spec), cur = rec(spec.current), hourly = arr(spec.hourly, 24), daily = arr(spec.daily, 14), alerts = arr(spec.alerts, 5);
  const lo = Math.min(...daily.map((d) => conv(d?.low)).filter(finite)), hi = Math.max(...daily.map((d) => conv(d?.high)).filter(finite));
  const place = str(spec.place, 60), temp = conv(cur.temp);
  const details: [string, unknown][] = [["Feels like", finite(num(cur.feels_like)) ? `${conv(cur.feels_like)}°${u}` : ""], ["Humidity", cur.humidity !== undefined ? `${num(cur.humidity)}%` : ""], ["Wind", cur.wind !== undefined ? `${num(cur.wind)} km/h` : ""], ["UV", cur.uv], ["Air quality", cur.aqi], ["Sunrise", cur.sunrise], ["Sunset", cur.sunset]];
  return (
    <Card spec={spec} title={place} summary={`${place || "Weather"}: ${finite(temp) ? `${temp}°${u}` : "no temperature"}, ${str(cur.condition, 40)}${daily.length ? `; ${daily.length}-day forecast` : ""}${alerts.length ? `; ${alerts.length} alert${alerts.length === 1 ? "" : "s"}` : ""}`}
      table={daily.length ? mkTable(place || "Weather", ["Day", "High", "Low", "Condition", "Precipitation %"], daily.map((d) => [str(d?.day, 20), conv(d?.high), conv(d?.low), str(d?.condition, 40), d?.precip ?? null])) : null}>
      <div className="uc-wx-now"><span className="uc-wx-ic" aria-hidden="true">{icon(cur.condition)}</span><span className="uc-wx-t">{finite(temp) ? `${temp}°` : "–"}</span><span>{str(cur.condition, 40)}</span>
        <button className="ghost small" onClick={toggle} aria-label={`Show in °${u === "C" ? "F" : "C"}`}>°{u}</button></div>
      <div className="uc-wx-det">{details.filter(([, v]) => v !== "" && v !== undefined && v !== null).map(([k, v]) => <span key={k}><span className="mut">{k}</span> {str(v, 30)}</span>)}</div>
      {alerts.map((a, i) => <div key={i} className="uc-alert warning" role="alert"><span aria-hidden="true">⚠ </span>{str(a, 160)}</div>)}
      {hourly.length > 0 && <div className="uc-wx-hourly" role="list" aria-label="Hourly">{hourly.map((h, i) => <div key={i} role="listitem"><span className="mut">{str(h?.t, 8)}</span><span aria-hidden="true">{icon(h?.condition)}</span><b>{finite(conv(h?.temp)) ? `${conv(h?.temp)}°` : "–"}</b></div>)}</div>}
      {daily.length > 0 && <div className="uc-wx-days">{daily.map((d, i) => { const l = conv(d?.low), h = conv(d?.high), span = hi - lo || 1; return (
        <div key={i} className="uc-wx-day"><span>{str(d?.day, 12)}</span><span aria-hidden="true">{icon(d?.condition)}</span><span className="mut">{str(d?.condition, 20)}</span>
          <span className="uc-wx-range" aria-label={`${l} to ${h}`}><i style={{ left: `${((l - lo) / span) * 100}%`, width: `${Math.max(4, ((h - l) / span) * 100)}%` }} /></span><span>{finite(l) ? l : "–"}° / {finite(h) ? h : "–"}°</span>
          <span className="mut">{finite(num(d?.precip)) ? `💧${num(d?.precip)}%` : ""}</span></div>); })}</div>}
    </Card>
  );
}

export function Forecast({ spec }: P) {
  const { u, conv, toggle } = useUnits(spec), days = arr(spec.days, 14);
  const hi = Math.max(...days.map((d) => conv(d?.high)).filter(finite), 1), lo = Math.min(...days.map((d) => conv(d?.low)).filter(finite), 0);
  return (
    <Card spec={spec} title={str(spec.place, 60)} summary={`${days.length}-day forecast${spec.place ? ` for ${str(spec.place, 60)}` : ""}`}
      table={mkTable(str(spec.place, 60) || "Forecast", ["Day", "High", "Low", "Precipitation %"], days.map((d) => [str(d?.day, 20), conv(d?.high), conv(d?.low), d?.precip ?? null]))}>
      <button className="ghost small" onClick={toggle}>°{u}</button>
      <div className="uc-fc">{days.map((d, i) => <div key={i}><span className="uc-fc-bar" style={{ height: `${Math.max(6, ((conv(d?.high) - lo) / (hi - lo || 1)) * 70)}px` }} title={`${conv(d?.low)}° to ${conv(d?.high)}°`} /><b>{conv(d?.high)}°</b><span className="mut">{conv(d?.low)}°</span><span>{str(d?.day, 8)}</span><span className="mut">{finite(num(d?.precip)) ? `💧${num(d?.precip)}%` : ""}</span></div>)}</div>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------------- numbers
const delta = (n: number, suffix = "%") => <span className={`uc-delta ${n > 0 ? "up" : n < 0 ? "down" : ""}`}><span aria-hidden="true">{n > 0 ? "▲" : n < 0 ? "▼" : "–"}</span> {n > 0 ? "+" : ""}{fmtNum(n)}{suffix}<span className="sr-only">{n > 0 ? " up" : n < 0 ? " down" : " no change"}</span></span>;

export function Quote({ spec }: P) {
  const price = num(spec.price), ch = num(spec.change), pct = num(spec.change_pct), cur = str(spec.currency, 6);
  return (
    <Card spec={spec} summary={`${str(spec.symbol, 12)}${spec.name ? ` (${str(spec.name, 40)})` : ""} at ${fmtNum(price)} ${cur}${finite(pct) ? `, ${pct >= 0 ? "up" : "down"} ${Math.abs(pct)}%` : ""}`}>
      <div className="uc-q"><div><b className="uc-sym">{str(spec.symbol, 12)}</b> <span className="mut">{str(spec.name, 40)}</span></div><div className="uc-big">{fmtNum(price)} <span className="mut small">{cur}</span></div>
        <div>{finite(ch) && delta(ch, "")} {finite(pct) && delta(pct)}</div><Spark values={spec.spark} w={140} h={34} /></div>
      <div className="small mut">{spec.as_of ? `As of ${str(spec.as_of, 40)}. ` : ""}{spec.delayed ? "Prices may be delayed. " : ""}This is data, not advice.</div>
    </Card>
  );
}

export function Kpi({ spec }: P) {
  const v = num(spec.value), d = num(spec.delta), t = num(spec.target), tone = (["good", "warn", "bad"].includes(String(spec.status)) ? ({ good: "ok", warn: "warn", bad: "bad" } as Record<string, Tone>)[String(spec.status)] : "") as Tone;
  return (
    <Card spec={spec} summary={`${str(spec.label, 60)}: ${finite(v) ? fmtNum(v) : str(spec.value, 40)} ${str(spec.unit, 12)}${finite(d) ? `, ${d >= 0 ? "up" : "down"} ${Math.abs(d)}% ${str(spec.delta_label, 40)}` : ""}`}>
      <div className="uc-kpi"><div className="mut">{str(spec.label, 60)} {tone && <Pill tone={tone}>{str(spec.status, 10)}</Pill>}</div>
        <div className="uc-big">{finite(v) ? fmtNum(v) : str(spec.value, 40)}{spec.unit ? <span className="mut small"> {str(spec.unit, 12)}</span> : null}</div>
        <div>{finite(d) && delta(d)} <span className="mut small">{str(spec.delta_label, 40)}</span></div>
        {finite(t) && finite(v) && <div className="uc-target" aria-label={`${Math.round((v / t) * 100)}% of target`}><i style={{ width: `${Math.min(100, (v / t) * 100)}%` }} /><span className="small mut">target {fmtNum(t)}</span></div>}
        <Spark values={spec.spark} w={140} h={32} /></div>
    </Card>
  );
}

export function Kpis({ spec }: P) {
  const items = arr(spec.items, 12);
  return (
    <Card spec={spec} summary={`${items.length} figures: ${items.map((i) => `${str(i?.label, 30)} ${fmtNum(num(i?.value))}`).join(", ")}`} table={mkTable("", ["Measure", "Value", "Change"], items.map((i) => [str(i?.label, 40), i?.value ?? null, i?.delta ?? null]))}>
      <div className="uc-kpis">{items.map((i, k) => <div key={k}><div className="mut small">{str(i?.label, 40)}</div><div className="uc-big sm">{fmtNum(num(i?.value))}{i?.unit ? <span className="mut small"> {str(i.unit, 8)}</span> : null}</div>{finite(num(i?.delta)) && delta(num(i.delta))}</div>)}</div>
    </Card>
  );
}

export function Compare({ spec }: P) {
  const items = arr(spec.items, 4).map((x) => str(x, 40)), rows = arr(spec.rows, 40);
  return (
    <Card spec={spec} summary={`Comparing ${items.join(" and ")} on ${rows.length} points`} table={mkTable("", ["", ...items], rows.map((r) => [str(r?.label, 40), ...items.map((_, i) => str(arr(r?.values)[i], 60))]))}>
      <div className="rich-scroll"><table className="uc-cmp"><thead><tr><th />{items.map((it, i) => <th key={i}>{it}</th>)}</tr></thead>
        <tbody>{rows.map((r, k) => <tr key={k}><th scope="row">{str(r?.label, 40)}</th>{items.map((_, i) => <td key={i} className={num(r?.best) === i ? "best" : ""}>{str(arr(r?.values)[i], 60)}{num(r?.best) === i && <span className="sr-only"> (better)</span>}{num(r?.best) === i && <span aria-hidden="true"> ★</span>}</td>)}</tr>)}</tbody></table></div>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------------- people, things, sources
export function Person({ spec }: P) {
  return (
    <Card spec={spec} summary={`${str(spec.name, 60)}${spec.role ? `, ${str(spec.role, 60)}` : ""}${spec.org ? ` at ${str(spec.org, 60)}` : ""}`}>
      <div className="uc-person"><CardImage src={spec.image ?? spec.avatar} alt={str(spec.name, 60)} className="uc-avatar" />
        <div><b>{str(spec.name, 60)}</b><div className="mut">{[spec.role, spec.org].filter(Boolean).map((x) => str(x, 60)).join(" · ")}</div>
          {spec.email && <div><SafeLink url={`mailto:${str(spec.email, 120)}`}>{str(spec.email, 120)}</SafeLink></div>}{spec.phone && <div><SafeLink url={`tel:${str(spec.phone, 30).replace(/[^\d+]/g, "")}`}>{str(spec.phone, 30)}</SafeLink></div>}<Links items={spec.links} /></div></div>
    </Card>
  );
}

export function Company({ spec }: P) {
  return <Card spec={spec} summary={`${str(spec.name, 60)}: ${str(spec.about, 120)}`}><b>{str(spec.name, 60)}</b><p>{str(spec.about, 600)}</p><Facts items={spec.facts} /><Links items={spec.links} /></Card>;
}

export function Product({ spec }: P) {
  const pros = arr(spec.pros, 8), cons = arr(spec.cons, 8);
  return (
    <Card spec={spec} summary={`${str(spec.name, 60)}${spec.price ? `, ${str(spec.price, 30)}` : ""}`}>
      <div className="uc-product"><CardImage src={spec.image} alt={str(spec.name, 60)} /><div><b>{str(spec.name, 60)}</b> {spec.price && <span className="uc-price">{str(spec.price, 30)}</span>}<Facts items={spec.specs} />
        <div className="uc-pc">{pros.length > 0 && <ul aria-label="Pros">{pros.map((p, i) => <li key={i}><span aria-hidden="true">+ </span>{str(p, 100)}</li>)}</ul>}{cons.length > 0 && <ul aria-label="Cons">{cons.map((p, i) => <li key={i}><span aria-hidden="true">− </span>{str(p, 100)}</li>)}</ul>}</div>
        {spec.url && <SafeLink url={spec.url}>View product</SafeLink>}</div></div>
    </Card>
  );
}

export function Source({ spec }: P) {
  return (
    <Card spec={spec} summary={`Source: ${str(spec.title, 100)}${spec.publisher ? `, ${str(spec.publisher, 60)}` : ""}`}>
      <div className="uc-src"><b>{spec.url ? <SafeLink url={spec.url}>{str(spec.title, 120)}</SafeLink> : str(spec.title, 120)}</b>
        <div className="mut small">{[spec.publisher, spec.date].filter(Boolean).map((x) => str(x, 60)).join(" · ")}</div>{spec.snippet && <blockquote>{str(spec.snippet, 400)}</blockquote>}{spec.note && <div className="small">{str(spec.note, 200)}</div>}</div>
    </Card>
  );
}

export function Results({ spec }: P) {
  const items = arr(spec.items, 20);
  return (
    <Card spec={spec} title={spec.query ? `Results for “${str(spec.query, 80)}”` : ""} summary={`${items.length} results${spec.query ? ` for ${str(spec.query, 80)}` : ""}`} table={mkTable("Results", ["Title", "Source", "Link", "Snippet"], items.map((i) => [str(i?.title, 120), str(i?.source, 60), str(i?.url, 200), str(i?.snippet, 300)]))}>
      <ol className="uc-results">{items.map((i, k) => <li key={k}><SafeLink url={i?.url}>{str(i?.title, 120)}</SafeLink> <span className="mut small">{str(i?.source, 60)}</span><div className="small">{str(i?.snippet, 300)}</div></li>)}</ol>
    </Card>
  );
}

export function News({ spec }: P) {
  const b = arr(spec.bullets, 8);
  return (
    <Card spec={spec} summary={`News: ${str(spec.headline, 120)}`}><b>{spec.url ? <SafeLink url={spec.url}>{str(spec.headline, 160)}</SafeLink> : str(spec.headline, 160)}</b>
      <ul>{b.map((x, i) => <li key={i}>{str(x, 200)}</li>)}</ul><div className="mut small">{[spec.source, spec.time].filter(Boolean).map((x) => str(x, 60)).join(" · ")}</div></Card>
  );
}

export function FileCard({ spec }: P) {
  const id = /^[0-9a-f]{12}$/.test(String(spec.artifact)) ? String(spec.artifact) : "";
  return (
    <Card spec={spec} summary={`File ${str(spec.name, 80)}${finite(num(spec.size)) ? `, ${fmtBytes(num(spec.size))}` : ""}`}>
      <div className="uc-file"><span aria-hidden="true" className="uc-file-ic">📄</span><div><b>{str(spec.name, 100)}</b><div className="mut small">{[spec.kind, finite(num(spec.size)) ? fmtBytes(num(spec.size)) : ""].filter(Boolean).map((x) => str(x, 20)).join(" · ")}</div>{spec.note && <div className="small">{str(spec.note, 200)}</div>}</div>
        {id && <a className="ghost small button" href={`/artifacts/?id=${id}`}>Open</a>}</div>
    </Card>
  );
}

export function Code({ spec }: P) {
  const code = str(spec.code, 20000), lines = code.split("\n"), hl = new Set(arr(spec.highlight, 100).map((n) => num(n)));
  return (
    <Card spec={spec} title={str(spec.filename, 80)} summary={`Code${spec.language ? ` in ${str(spec.language, 20)}` : ""}, ${lines.length} lines`}>
      <div className="codeblock"><div className="cb-head"><span>{str(spec.language, 20) || "code"}</span><CopyButton text={code} /></div>
        <pre><code>{lines.map((l, i) => <span key={i} className={hl.has(i + 1) ? "uc-hl" : ""}><span className="uc-ln" aria-hidden="true">{i + 1}</span>{l + "\n"}</span>)}</code></pre></div>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------------- time and work
export function Timeline({ spec }: P) {
  const ev = arr(spec.events, 40);
  return (
    <Card spec={spec} summary={`Timeline of ${ev.length} events`} table={mkTable("Timeline", ["When", "Event", "Detail", "Status"], ev.map((e) => [str(e?.t, 30), str(e?.title, 100), str(e?.detail, 200), str(e?.status, 12)]))}>
      <ol className="uc-tl">{ev.map((e, i) => <li key={i} className={str(e?.status, 10)}><span className="mut small">{str(e?.t, 30)}</span> <b>{str(e?.title, 100)}</b> {e?.status && <Pill tone={toneOf(e.status)}>{str(e.status, 12)}</Pill>}<div className="small">{str(e?.detail, 200)}</div></li>)}</ol>
    </Card>
  );
}

export function EventCard({ spec }: P) {
  const items = arr(spec.items, 30), single = !items.length && spec.title ? [{ title: spec.title, start: spec.start, end: spec.end, where: spec.where }] : items;
  return (
    <Card spec={spec} title={str(spec.date, 40)} summary={`${single.length} event${single.length === 1 ? "" : "s"}${single.some((e) => e?.conflict) ? ", with a conflict" : ""}`} table={mkTable("Agenda", ["Title", "Start", "End", "Where", "Conflict"], single.map((e) => [str(e?.title, 100), str(e?.start, 30), str(e?.end, 30), str(e?.where, 60), e?.conflict ? "yes" : ""]))}>
      <ul className="uc-ev">{single.map((e, i) => <li key={i} className={e?.conflict ? "conflict" : ""}><b>{str(e?.start, 30)}{e?.end ? `–${str(e.end, 30)}` : ""}</b> {str(e?.title, 100)} {e?.where && <span className="mut">· {str(e.where, 60)}</span>} {e?.conflict && <Pill tone="warn">conflict</Pill>}</li>)}</ul>
    </Card>
  );
}

export function Tasks({ spec }: P) {
  const items = arr(spec.items, 40), reply = useReply(), [done, setDone] = useState<Set<number>>(new Set(items.map((t, i) => (t?.done ? i : -1)).filter((i) => i >= 0)));
  const toggle = (i: number) => { const on = !done.has(i); setDone((d) => { const n = new Set(d); if (on) n.add(i); else n.delete(i); return n; }); reply?.(`${on ? "Done" : "Not done"}: ${str(items[i]?.text, 120)}`); };
  return (
    <Card spec={spec} title={str(spec.title, 80)} summary={`${done.size} of ${items.length} tasks done`} table={mkTable("Tasks", ["Task", "Done", "Due"], items.map((t, i) => [str(t?.text, 120), done.has(i) ? "yes" : "no", str(t?.due, 30)]))}>
      <div className="uc-prog" role="progressbar" aria-valuenow={done.size} aria-valuemax={items.length} aria-label="Progress"><i style={{ width: `${items.length ? (done.size / items.length) * 100 : 0}%` }} /></div>
      <ul className="uc-tasks">{items.map((t, i) => <li key={i}><label className="check"><input type="checkbox" checked={done.has(i)} disabled={!reply} onChange={() => toggle(i)} /> <span className={done.has(i) ? "done" : ""}>{str(t?.text, 120)}</span></label>{t?.due && <span className="mut small"> due {str(t.due, 30)}</span>}</li>)}</ul>
      {!reply && <div className="small mut">Ticking a task works in the chat.</div>}
    </Card>
  );
}

export function Kanban({ spec }: P) {
  const cols = arr(spec.columns, 8);
  return (
    <Card spec={spec} summary={`Board with ${cols.map((c) => `${str(c?.name, 30)} (${arr(c?.cards).length})`).join(", ")}`} table={mkTable("Board", ["Column", "Card", "Tag"], cols.flatMap((c) => arr(c?.cards, 50).map((k) => [str(c?.name, 30), str(k?.title, 100), str(k?.tag, 20)])))}>
      <div className="uc-kb">{cols.map((c, i) => <div key={i} className="uc-kb-col"><div className="mut small"><b>{str(c?.name, 30)}</b> · {arr(c?.cards).length}</div>{arr(c?.cards, 50).map((k, j) => <div key={j} className="uc-kb-card">{str(k?.title, 100)} {k?.tag && <span className="pill">{str(k.tag, 20)}</span>}</div>)}</div>)}</div>
    </Card>
  );
}

export function Steps({ spec }: P) {
  const steps = arr(spec.steps, 30);
  return (
    <Card spec={spec} summary={`${steps.filter((s) => s?.status === "done").length} of ${steps.length} steps done`} table={mkTable("Steps", ["Step", "Status", "Detail"], steps.map((s) => [str(s?.name, 80), str(s?.status, 12), str(s?.detail, 160)]))}>
      <ol className="uc-steps">{steps.map((s, i) => <li key={i} className={str(s?.status, 10)}><Pill tone={s?.status === "done" ? "ok" : s?.status === "failed" ? "bad" : s?.status === "active" ? "info" : ""}>{str(s?.status, 12) || "todo"}</Pill> <b>{str(s?.name, 80)}</b><div className="small mut">{str(s?.detail, 160)}</div></li>)}</ol>
    </Card>
  );
}

const LEVEL: Record<string, [Tone, string]> = { info: ["info", "Note"], success: ["ok", "Done"], warning: ["warn", "Warning"], error: ["bad", "Error"] };
export function Alert({ spec }: P) {
  const [tone, label] = LEVEL[String(spec.level)] ?? LEVEL.info;
  return <Card spec={spec} summary={`${label}: ${str(spec.title, 80)} ${str(spec.text, 200)}`}><div className={`uc-alert ${tone}`} role={tone === "bad" ? "alert" : "note"}><Pill tone={tone}>{label}</Pill> {spec.title && <b>{str(spec.title, 80)} </b>}{str(spec.text, 400)}</div></Card>;
}

const LEG: Record<string, string> = { flight: "✈", hotel: "🏨", train: "🚆", car: "🚗" };
export function Itinerary({ spec }: P) {
  const legs = arr(spec.legs, 30);
  return (
    <Card spec={spec} title={str(spec.title, 80)} summary={`Trip with ${legs.length} legs`} table={mkTable("Trip", ["Type", "From", "To", "Start", "End", "Reference"], legs.map((l) => [str(l?.kind, 12), str(l?.from, 60), str(l?.to, 60), str(l?.start, 30), str(l?.end, 30), str(l?.ref, 30)]))}>
      <ol className="uc-trip">{legs.map((l, i) => <li key={i}><span aria-hidden="true">{LEG[String(l?.kind)] ?? "•"}</span> <b>{str(l?.from, 60)}{l?.to ? ` → ${str(l.to, 60)}` : ""}</b> <span className="mut small">{str(l?.kind, 12)}</span><div className="small">{[l?.start, l?.end].filter(Boolean).map((x) => str(x, 30)).join(" – ")}{l?.ref ? ` · ${str(l.ref, 30)}` : ""}</div>{l?.note && <div className="small mut">{str(l.note, 160)}</div>}</li>)}</ol>
    </Card>
  );
}

export function Place({ spec }: P) {
  const [map, setMap] = useState(false), lat = num(spec.lat), lon = num(spec.lon), rating = num(spec.rating);
  return (
    <Card spec={spec} summary={`${str(spec.name, 80)}${spec.address ? `, ${str(spec.address, 120)}` : ""}`}>
      <b>{str(spec.name, 80)}</b> {finite(rating) && <span aria-label={`Rated ${rating} out of 5`}>★ {rating}</span>}<div className="mut">{str(spec.address, 160)}</div>{spec.hours && <div className="small">{str(spec.hours, 120)}</div>}
      {spec.url && <div><SafeLink url={spec.url}>Website</SafeLink></div>}
      {finite(lat) && finite(lon) && (map ? <UiBlock spec={{ type: "map", title: str(spec.name, 60), center: [lon, lat], zoom: 12, layers: [{ kind: "points", name: "Place", points: [{ lat, lon, label: str(spec.name, 60) }] }] }} /> : <button className="ghost small" onClick={() => setMap(true)}>Show on a map</button>)}
    </Card>
  );
}

