"use client";
// Cards, second half: recipes, receipts, polls, glossary, FAQ, outlines, scores, countdowns, equations, molecules, API calls, logs, findings, usage, runs, approvals, email drafts,
// colour palettes, QR codes and sports.
import { useEffect, useMemo, useState } from "react";
import { mkTable } from "./chartkit";
import { arr, finite, fmtMoney, fmtNum, num, str } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import JsonViewer from "../rich/JsonViewer";
import { Card, CopyButton, Facts, Pill, SafeLink, rec, toneOf, useReply, type Tone } from "./cardkit";

type P = { spec: Spec };

export function Recipe({ spec }: P) {
  const base = Math.max(1, num(spec.servings, 1)), [n, setN] = useState(base), k = n / base, ing = arr(spec.ingredients, 60), steps = arr(spec.steps, 40), nut = rec(spec.nutrition);
  const qty = (q: unknown) => { const v = num(q); return finite(v) ? `${+(v * k).toFixed(2)}` : str(q, 12); };
  return (
    <Card spec={spec} title={str(spec.title, 80)} summary={`Recipe for ${n} servings with ${ing.length} ingredients and ${steps.length} steps${spec.time ? `, ${str(spec.time, 20)}` : ""}`} table={mkTable("Ingredients", ["Ingredient", "Amount", "Unit"], ing.map((i) => [str(i?.item, 60), finite(num(i?.qty)) ? +(num(i.qty) * k).toFixed(2) : str(i?.qty, 12), str(i?.unit, 12)]))}>
      <div className="row"><span className="mut">{spec.time ? `${str(spec.time, 20)} · ` : ""}Servings</span> <button className="ghost small" aria-label="Fewer servings" onClick={() => setN(Math.max(1, n - 1))}>−</button><b aria-live="polite">{n}</b><button className="ghost small" aria-label="More servings" onClick={() => setN(n + 1)}>+</button></div>
      <h4>Ingredients</h4><ul>{ing.map((i, x) => <li key={x}>{qty(i?.qty)} {str(i?.unit, 12)} {str(i?.item, 60)}</li>)}</ul>
      <h4>Steps</h4><ol>{steps.map((s, i) => <li key={i}>{str(s, 300)}</li>)}</ol>
      {Object.keys(nut).length > 0 && <div className="small mut">Per serving: {Object.entries(nut).slice(0, 8).map(([a, b]) => `${a.replace(/_/g, " ")} ${str(b, 12)}`).join(" · ")}</div>}
    </Card>
  );
}

export function Receipt({ spec }: P) {
  const items = arr(spec.items, 100), cur = str(spec.currency, 3) || "USD", sub = items.reduce((a, i) => a + num(i?.qty, 1) * num(i?.price, 0), 0), tax = num(spec.tax, 0), total = finite(num(spec.total)) ? num(spec.total) : sub + tax;
  return (
    <Card spec={spec} title={str(spec.title, 80)} summary={`${items.length} line items, total ${fmtMoney(total, cur)}`} table={mkTable("Receipt", ["Item", "Qty", "Price", "Amount"], items.map((i) => [str(i?.name, 80), num(i?.qty, 1), num(i?.price, 0), num(i?.qty, 1) * num(i?.price, 0)]))}>
      <div className="rich-scroll"><table className="uc-rc"><thead><tr><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Amount</th></tr></thead>
        <tbody>{items.map((i, k) => <tr key={k}><td>{str(i?.name, 80)}</td><td className="num">{num(i?.qty, 1)}</td><td className="num">{fmtMoney(num(i?.price, 0), cur)}</td><td className="num">{fmtMoney(num(i?.qty, 1) * num(i?.price, 0), cur)}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={3}>Subtotal</td><td className="num">{fmtMoney(sub, cur)}</td></tr>{tax > 0 && <tr><td colSpan={3}>Tax</td><td className="num">{fmtMoney(tax, cur)}</td></tr>}<tr><th colSpan={3}>Total</th><th className="num">{fmtMoney(total, cur)}</th></tr></tfoot></table></div>
      <Facts items={spec.meta} />
    </Card>
  );
}

export function Poll({ spec }: P) {
  const opts = arr(spec.options, 12), reply = useReply(), [picked, setPicked] = useState<number | null>(null);
  return (
    <Card spec={spec} summary={`Question: ${str(spec.question, 160)}; ${opts.length} options`}>
      <b id="poll-q">{str(spec.question, 200)}</b>
      <div role="group" aria-labelledby="poll-q" className="uc-poll">{opts.map((o, i) => <button key={i} className={picked === i ? "acc" : "ghost"} aria-pressed={picked === i} disabled={!reply || picked !== null} onClick={() => { setPicked(i); reply?.(`${str(spec.question, 200)}\n- Answer: ${str(o, 100)}`); }}>{str(o, 100)}</button>)}</div>
      {!reply && <div className="small mut">Answer in the chat box.</div>}{picked !== null && <div className="small mut">Sent.</div>}
    </Card>
  );
}

export const Definition = ({ spec }: P) => <Card spec={spec} summary={`${str(spec.term, 60)}: ${str(spec.meaning, 200)}`}><dl className="uc-def"><dt>{str(spec.term, 80)}</dt><dd>{str(spec.meaning, 600)}</dd></dl>{spec.source && <div className="small mut">Source: {str(spec.source, 100)}</div>}</Card>;

export function Faq({ spec }: P) {
  const items = arr(spec.items, 40);
  return <Card spec={spec} summary={`${items.length} questions`} table={mkTable("FAQ", ["Question", "Answer"], items.map((i) => [str(i?.q, 200), str(i?.a, 600)]))}>{items.map((i, k) => <details key={k} className="uc-faq"><summary>{str(i?.q, 200)}</summary><p>{str(i?.a, 800)}</p></details>)}</Card>;
}

export function Toc({ spec }: P) {
  const items = arr(spec.items, 40);
  return <Card spec={spec} title={str(spec.title, 80)} summary={`Outline with ${items.length} sections`} table={mkTable("Outline", ["Section", "Summary"], items.map((i) => [str(i?.title, 100), str(i?.summary, 300)]))}><ol className="uc-toc">{items.map((i, k) => <li key={k}><b>{str(i?.title, 100)}</b>{i?.summary && <div className="small mut">{str(i.summary, 300)}</div>}</li>)}</ol></Card>;
}

export function Scorecard({ spec }: P) {
  const s = num(spec.score, 0), parts = arr(spec.parts, 12), band = s >= 80 ? "strong" : s >= 60 ? "fair" : "weak";
  return (
    <Card spec={spec} title={str(spec.title, 80)} summary={`Score ${fmtNum(s)} out of 100 (${band})`} table={mkTable("Score", ["Part", "Score"], [["Overall", s], ...parts.map((p) => [str(p?.label, 60), num(p?.score, 0)])])}>
      <div className="uc-score"><div className="uc-big">{fmtNum(s)}<span className="mut small"> / 100</span></div><Pill tone={s >= 80 ? "ok" : s >= 60 ? "warn" : "bad"}>{band}</Pill></div>
      {parts.map((p, i) => <div key={i} className="uc-part"><span>{str(p?.label, 60)}</span><span className="uc-target"><i style={{ width: `${Math.max(0, Math.min(100, num(p?.score, 0)))}%` }} /></span><b>{fmtNum(num(p?.score, 0))}</b></div>)}
      {spec.note && <div className="small mut">{str(spec.note, 200)}</div>}
    </Card>
  );
}

export function Countdown({ spec }: P) {
  const fixed = num(spec.seconds), target = Date.parse(String(spec.target ?? ""));
  const [left, setLeft] = useState(() => (finite(fixed) ? fixed : Math.max(0, Math.round((target - Date.now()) / 1000))));
  const [run, setRun] = useState(false);
  useEffect(() => {
    const tick = finite(fixed) ? run : true;
    if (!tick) return;
    const h = setInterval(() => setLeft((l) => (finite(fixed) ? Math.max(0, l - 1) : Math.max(0, Math.round((target - Date.now()) / 1000)))), 1000);
    return () => clearInterval(h);
  }, [run, fixed, target]);
  const d = Math.floor(left / 86400), h = Math.floor((left % 86400) / 3600), m = Math.floor((left % 3600) / 60), s = left % 60, p = (n: number) => String(n).padStart(2, "0");
  return (
    <Card spec={spec} summary={`${str(spec.label, 60) || "Countdown"}: ${left} seconds left`}>
      <div className="mut">{str(spec.label, 60)}</div><div className="uc-big" role="timer" aria-live="off">{d > 0 ? `${d}d ` : ""}{p(h)}:{p(m)}:{p(s)}</div>
      {finite(fixed) && <div className="row"><button className="ghost small" onClick={() => setRun((r) => !r)}>{run ? "Pause" : "Start"}</button><button className="ghost small" onClick={() => { setRun(false); setLeft(fixed); }}>Reset</button></div>}
    </Card>
  );
}

export function Equation({ spec }: P) {
  const latex = str(spec.latex, 2000), [html, setHtml] = useState("");
  useEffect(() => {
    let live = true;
    Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([k]) => { if (live) setHtml(k.default.renderToString(latex, { throwOnError: false, trust: false, strict: "ignore", output: "htmlAndMathml", maxSize: 20, maxExpand: 200 })); }).catch(() => { if (live) setHtml(""); });
    return () => { live = false; };
  }, [latex]);
  return (
    <Card spec={spec} summary={`Equation: ${latex}${spec.caption ? `. ${str(spec.caption, 120)}` : ""}`}>
      {html ? <div className="uc-eq" dangerouslySetInnerHTML={{ __html: html }} /> : <code className="uc-eq-raw">{latex}</code>}
      {spec.caption && <div className="small mut">{str(spec.caption, 160)}</div>}<CopyButton text={latex} label="Copy LaTeX" />
    </Card>
  );
}

export function Molecule({ spec }: P) {
  return (
    <Card spec={spec} summary={`${str(spec.name, 80)}${spec.formula ? `, formula ${str(spec.formula, 60)}` : ""}`}>
      <b>{str(spec.name, 80)}</b> {spec.formula && <span className="uc-formula">{str(spec.formula, 60)}</span>}<Facts items={spec.properties} />
      {spec.smiles && <div className="row"><code className="uc-smiles">{str(spec.smiles, 400)}</code><CopyButton text={str(spec.smiles, 400)} label="Copy SMILES" /></div>}
      <div className="small mut">The structure is shown as text; a 2D and 3D viewer arrives with the Science Lab.</div>
    </Card>
  );
}

export function Request({ spec }: P) {
  const status = num(spec.status), tone: Tone = status >= 500 ? "bad" : status >= 400 ? "warn" : status >= 200 ? "ok" : "";
  return (
    <Card spec={spec} summary={`${str(spec.method, 8)} ${str(spec.url, 120)}${finite(status) ? ` returned ${status}` : ""}`}>
      <div><b className="uc-method">{str(spec.method, 8)}</b> <code>{str(spec.url, 300)}</code> {finite(status) && <Pill tone={tone}>{status}</Pill>} {finite(num(spec.ms)) && <span className="mut small">{num(spec.ms)} ms</span>}</div>
      {Object.keys(rec(spec.headers)).length > 0 && <details><summary className="small">Headers (secrets hidden)</summary><JsonViewer value={rec(spec.headers)} /></details>}
      {spec.request !== undefined && spec.request !== null && <details><summary className="small">Request</summary><JsonViewer value={spec.request} /></details>}
      {spec.response !== undefined && spec.response !== null && <details open><summary className="small">Response</summary><JsonViewer value={spec.response} /></details>}
    </Card>
  );
}

export function Log({ spec }: P) {
  const lines = useMemo(() => (Array.isArray(spec.lines) ? arr(spec.lines, 5000).map((l) => str(l, 400)) : str(spec.text, 200000).split("\n")), [spec]), [q, setQ] = useState(""), shown = q.trim() ? lines.filter((l) => l.toLowerCase().includes(q.trim().toLowerCase())) : lines;
  const cls = (l: string) => (/\b(error|fatal|exception)\b/i.test(l) ? "bad" : /\bwarn(ing)?\b/i.test(l) ? "warn" : "");
  return (
    <Card spec={spec} summary={`Log with ${lines.length} lines, ${lines.filter((l) => cls(l) === "bad").length} errors`}>
      <div className="row"><input type="search" aria-label="Search the log" placeholder="Search the log" value={q} onChange={(e) => setQ(e.target.value)} /><CopyButton text={lines.join("\n")} /><span className="mut small">{shown.length} of {lines.length} lines</span></div>
      <pre className="uc-log"><code>{shown.slice(0, 2000).map((l, i) => <span key={i} className={cls(l)}>{cls(l) === "bad" ? "✕ " : cls(l) === "warn" ? "! " : "  "}{l + "\n"}</span>)}</code></pre>
    </Card>
  );
}

export function Finding({ spec }: P) {
  const sev = String(spec.severity), tone: Tone = /crit|high/i.test(sev) ? "bad" : /med/i.test(sev) ? "warn" : "info", refs = arr(spec.refs, 8);
  return (
    <Card spec={spec} summary={`${str(spec.severity, 12)} finding: ${str(spec.title, 120)}`}>
      <div><Pill tone={tone}>{str(spec.severity, 12)}</Pill> <b>{str(spec.title, 160)}</b></div>
      {spec.evidence && <p><span className="mut">Evidence: </span>{str(spec.evidence, 500)}</p>}{spec.fix && <p><span className="mut">Fix: </span>{str(spec.fix, 500)}</p>}
      {refs.length > 0 && <div className="small">{refs.map((r, i) => <span key={i} className="pill">{str(r, 20)}</span>)}</div>}
    </Card>
  );
}

export function Usage({ spec }: P) {
  const spend = num(spec.spend, 0), cap = num(spec.cap), by = arr(spec.by_model, 20), pct = finite(cap) && cap > 0 ? spend / cap : NaN;
  return (
    <Card spec={spec} summary={`Spend ${fmtMoney(spend)}${finite(cap) ? ` of ${fmtMoney(cap)}` : ""}${finite(num(spec.tokens)) ? `, ${fmtNum(num(spec.tokens))} tokens` : ""}`} table={by.length ? mkTable("Usage", ["Model", "Spend"], by.map((b) => [str(b?.model, 60), num(b?.spend, 0)])) : null}>
      <div className="uc-big">{fmtMoney(spend)}{finite(cap) && <span className="mut small"> of {fmtMoney(cap)}</span>}</div>
      {finite(pct) && <div className="uc-target" role="progressbar" aria-valuenow={Math.round(pct * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Spend against the cap"><i className={pct >= 0.9 ? "bad" : pct >= 0.7 ? "warn" : ""} style={{ width: `${Math.min(100, pct * 100)}%` }} /></div>}
      {finite(pct) && pct >= 0.9 && <Pill tone="bad">close to the cap</Pill>}{finite(num(spec.tokens)) && <div className="small mut">{fmtNum(num(spec.tokens))} tokens</div>}
      {by.map((b, i) => <div key={i} className="uc-part"><span>{str(b?.model, 60)}</span><span /><b>{fmtMoney(num(b?.spend, 0))}</b></div>)}
    </Card>
  );
}

export function Run({ spec }: P) {
  const tone = toneOf(spec.status === "ok" ? "ok" : spec.status === "failed" ? "bad" : spec.status === "waiting" ? "warn" : "info");
  return (
    <Card spec={spec} summary={`${str(spec.name, 80)}: ${str(spec.status, 12)}${finite(num(spec.steps)) ? `, ${num(spec.steps)} steps` : ""}`}>
      <div><b>{str(spec.name, 80)}</b> <Pill tone={tone}>{str(spec.status, 12)}</Pill></div>
      <div className="small mut">{[finite(num(spec.steps)) ? `${num(spec.steps)} steps` : "", finite(num(spec.duration_s)) ? `${num(spec.duration_s)} s` : "", finite(num(spec.cost_usd)) ? fmtMoney(num(spec.cost_usd)) : ""].filter(Boolean).join(" · ")}</div>
      {spec.url && <SafeLink url={spec.url}>Open the run</SafeLink>}
    </Card>
  );
}

export function Approval({ spec }: P) {
  const reply = useReply(), [done, setDone] = useState<"" | "approved" | "rejected">(""), risk = String(spec.risk);
  const send = (what: "approved" | "rejected") => { setDone(what); reply?.(`${what === "approved" ? "Approved" : "Rejected"}: ${str(spec.action, 200)}`); };
  return (
    <Card spec={spec} summary={`Approval needed: ${str(spec.action, 160)} (risk ${risk || "unknown"})`}>
      <div><Pill tone={risk === "high" ? "bad" : risk === "medium" ? "warn" : "info"}>{risk || "review"} risk</Pill> <b>{str(spec.action, 200)}</b></div><Facts items={spec.details} />
      <div className="row"><button className="acc" disabled={!reply || !!done} onClick={() => send("approved")}>{str(spec.approve, 30) || "Approve"}</button><button className="ghost" disabled={!reply || !!done} onClick={() => send("rejected")}>{str(spec.reject, 30) || "Reject"}</button>
        {done && <span className="small mut" role="status">{done === "approved" ? "Approved" : "Rejected"}; sent.</span>}{!reply && <span className="small mut">Answer in the chat box.</span>}</div>
    </Card>
  );
}

export function Email({ spec }: P) {
  const to = arr(spec.to, 20).map((x) => str(x, 120)), cc = arr(spec.cc, 20).map((x) => str(x, 120)), text = `To: ${to.join(", ")}\n${cc.length ? `Cc: ${cc.join(", ")}\n` : ""}Subject: ${str(spec.subject, 200)}\n\n${str(spec.body, 20000)}`;
  return (
    <Card spec={spec} summary={`Email draft to ${to.join(", ")}: ${str(spec.subject, 120)}`}>
      <div className="uc-mail"><div><span className="mut">To</span> {to.join(", ")}</div>{cc.length > 0 && <div><span className="mut">Cc</span> {cc.join(", ")}</div>}<div><span className="mut">Subject</span> <b>{str(spec.subject, 200)}</b></div><pre className="uc-mail-body">{str(spec.body, 20000)}</pre></div>
      <div className="small mut">A draft only: nothing is sent.</div><CopyButton text={text} label="Copy draft" />
    </Card>
  );
}

const lum = (hex: string) => { const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim()); if (!m) return null; const c = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
export const contrast = (a: string, b: string) => { const x = lum(a), y = lum(b); return x === null || y === null ? NaN : (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

export function Palette({ spec }: P) {
  const colors = arr(spec.colors, 24).map((c) => ({ name: str(c?.name, 30), hex: str(c?.hex, 9) })).filter((c) => lum(c.hex) !== null);
  return (
    <Card spec={spec} summary={`${colors.length} colours: ${colors.map((c) => `${c.name || c.hex}`).join(", ")}`} table={mkTable("Palette", ["Name", "Hex", "Contrast on white", "Contrast on black"], colors.map((c) => [c.name, c.hex, +contrast(c.hex, "#ffffff").toFixed(2), +contrast(c.hex, "#000000").toFixed(2)]))}>
      <div className="uc-pal">{colors.map((c, i) => <div key={i} className="uc-sw"><span className="uc-sw-c" style={{ background: c.hex }} role="img" aria-label={`${c.name || "colour"} ${c.hex}`} /><b>{c.name}</b><code>{c.hex}</code><span className="small mut">{contrast(c.hex, "#ffffff").toFixed(1)}:1 on white</span><CopyButton text={c.hex} /></div>)}</div>
    </Card>
  );
}

export function Qr({ spec }: P) {
  const text = str(spec.text, 1000), [cells, setCells] = useState<boolean[][] | null>(null);
  useEffect(() => {
    let live = true;
    import("qrcode-generator").then((m) => { const qr = m.default(0, "M"); qr.addData(text); qr.make(); const n = qr.getModuleCount(); if (live) setCells(Array.from({ length: n }, (_, r) => Array.from({ length: n }, (__, c) => qr.isDark(r, c)))); }).catch(() => { if (live) setCells(null); });
    return () => { live = false; };
  }, [text]);
  return (
    <Card spec={spec} summary={`QR code for ${text.slice(0, 80)}`}>
      {cells ? <svg className="uc-qr" viewBox={`-2 -2 ${cells.length + 4} ${cells.length + 4}`} role="img" aria-label={`QR code for ${text.slice(0, 80)}`}><rect x={-2} y={-2} width={cells.length + 4} height={cells.length + 4} fill="#fff" />{cells.flatMap((row, r) => row.map((d, c) => (d ? <rect key={`${r}-${c}`} x={c} y={r} width={1.02} height={1.02} fill="#000" /> : null)))}</svg> : <div className="mut small">Making the code…</div>}
      {spec.caption && <div className="small">{str(spec.caption, 120)}</div>}<code className="small">{text}</code>
    </Card>
  );
}

export function Sports({ spec }: P) {
  const home = rec(spec.home), away = rec(spec.away), table = arr(spec.table, 40);
  return table.length ? (
    <Card spec={spec} summary={`League table with ${table.length} teams`} table={mkTable("Table", ["Team", "P", "W", "D", "L", "Pts"], table.map((t) => [str(t?.team, 40), num(t?.p, 0), num(t?.w, 0), num(t?.d, 0), num(t?.l, 0), num(t?.pts, 0)]))}>
      <div className="rich-scroll"><table><thead><tr><th>#</th><th>Team</th><th className="num">P</th><th className="num">W</th><th className="num">D</th><th className="num">L</th><th className="num">Pts</th></tr></thead><tbody>{table.map((t, i) => <tr key={i}><td>{i + 1}</td><td>{str(t?.team, 40)}</td><td className="num">{num(t?.p, 0)}</td><td className="num">{num(t?.w, 0)}</td><td className="num">{num(t?.d, 0)}</td><td className="num">{num(t?.l, 0)}</td><th className="num">{num(t?.pts, 0)}</th></tr>)}</tbody></table></div>
    </Card>
  ) : (
    <Card spec={spec} summary={`${str(home.name, 40)} ${str(home.score, 4)}, ${str(away.name, 40)} ${str(away.score, 4)}${spec.status ? ` (${str(spec.status, 20)})` : ""}`}>
      <div className="uc-score-line"><span>{str(home.name, 40)}</span><b className="uc-big">{str(home.score, 4)} – {str(away.score, 4)}</b><span>{str(away.name, 40)}</span></div><div className="small mut">{[spec.status, spec.when].filter(Boolean).map((x) => str(x, 30)).join(" · ")}</div>
    </Card>
  );
}
