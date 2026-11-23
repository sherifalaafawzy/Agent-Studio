"use client";
// Small, safe Markdown renderer: builds React elements only (never injects HTML), so model output can't run script.
// Supports headings, paragraphs, fenced code (with copy), lists, quotes, rules, tables, inline code / bold / italic / links.
import { Fragment, useContext, useState } from "react";
import { ICheck, ICopy } from "./Icons";
import { PlainRichContext } from "./rich/PlainContext";
import { isRichLang, RichFence, SafeImage } from "./RichBlocks";
import { CanvasContext } from "./rich/BlockActions";
import { canvasKindFor } from "@/lib/canvas";

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [ok, setOk] = useState(false);
  const openCanvas = useContext(CanvasContext);
  return (
    <div className="codeblock">
      <div className="cb-head"><span>{lang || "text"}</span>
        {openCanvas && <button className="ghost small" title="Open this in the canvas to edit it with the agent" onClick={() => { const k = canvasKindFor(lang); void openCanvas({ title: lang ? `${lang} snippet` : "Snippet", type: k.type, language: k.language, content: code }); }}>Open in canvas</button>}
        <button className="icon-btn" style={{ width: 26, height: 26 }} title="Copy"
          onClick={() => { navigator.clipboard?.writeText(code).then(() => { setOk(true); setTimeout(() => setOk(false), 1200); }); }}>
          {ok ? <ICheck size={14} /> : <ICopy size={14} />}</button></div>
      <pre><code>{code}</code></pre>
    </div>
  );
}

const SAFE_URL = /^(https?:\/\/|mailto:)/i;

// Light inline-math support for what models commonly emit ($17 \times 23 = 391$): symbols, not a full TeX renderer.
const TEX: [RegExp, string][] = [[/\\times/g, "×"], [/\\cdot/g, "·"], [/\\div/g, "÷"], [/\\pm/g, "±"], [/\\leq?/g, "≤"], [/\\geq?/g, "≥"],
  [/\\neq?/g, "≠"], [/\\approx/g, "≈"], [/\\infty/g, "∞"], [/\\rightarrow|\\to/g, "→"], [/\\Rightarrow/g, "⇒"], [/\\sqrt\{([^}]*)\}/g, "√($1)"],
  [/\\frac\{([^}]*)\}\{([^}]*)\}/g, "($1)/($2)"], [/\\pi/g, "π"], [/\\alpha/g, "α"], [/\\beta/g, "β"], [/\\theta/g, "θ"], [/\\sum/g, "Σ"],
  [/\^\{?2\}?/g, "²"], [/\^\{?3\}?/g, "³"], [/\\[,;!]/g, " "], [/\\text\{([^}]*)\}/g, "$1"], [/[{}]/g, ""]];
const tex = (s: string) => TEX.reduce((acc, [re, to]) => acc.replace(re, to), s);

function inline(text: string, key = 0): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  // order matters: code first (its content is literal), then links, bold, italic
  // underscores only emphasise at word boundaries, so snake_case identifiers stay intact
  const re = /(`[^`\n]+`)|(?:\$(?!\s)([^$\n]*?\\[a-zA-Z][^$\n]*?|[^$\n]*?[\^_][^$\n]*?)(?<!\s)\$)|(\[([^\]\n]+)\]\(([^)\s]+)\))|(\*\*([^*\n]+)\*\*)|((?<![\w])__([^_\n]+)__(?![\w]))|(\*([^*\s][^*\n]*)\*)|((?<![\w])_([^_\n]+)_(?![\w]))|(!\[([^\]\n]*)\]\(([^)\s]+)\))/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${i++}`;
    // groups: 1 code | 2 inline math | 3-5 link | 6-9 bold | 10-13 italic | 14-16 image
    if (m[14]) out.push(<SafeImage key={k} alt={m[15]} url={m[16]} />);
    else if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2] !== undefined) out.push(<span key={k} className="math">{tex(m[2])}</span>);
    else if (m[3]) out.push(SAFE_URL.test(m[5]) ? <a key={k} href={m[5]} target="_blank" rel="noreferrer noopener">{inline(m[4], i)}</a> : <Fragment key={k}>{m[4]}</Fragment>);
    else if (m[6] || m[8]) out.push(<strong key={k}>{inline(m[7] ?? m[9], i)}</strong>);
    else if (m[10] || m[12]) out.push(<em key={k}>{inline(m[11] ?? m[13], i)}</em>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const isTableSep = (l: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export default function Markdown({ text }: { text: string }) {
  const plain = useContext(PlainRichContext);                 // AG-UI components are off for this agent: a rich fence is just code
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0, k = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(```|~~~)\s*([\w+#.-]*)\s*$/);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      blocks.push(isRichLang(fence[2]) && !plain ? <RichFence key={k++} lang={fence[2]} code={body.join("\n")} /> : <CodeBlock key={k++} lang={fence[2]} code={body.join("\n")} />);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { const T = (`h${h[1].length}`) as "h1"; blocks.push(<T key={k++}>{inline(h[2])}</T>); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { blocks.push(<hr key={k++} />); i++; continue; }
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line); i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push(<table key={k++}><thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
        <tbody>{rows.map((r, a) => <tr key={a}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody></table>);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const q: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(<blockquote key={k++}><Markdown text={q.join("\n")} /></blockquote>);
      continue;
    }
    const li = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (li) {
      const ordered = /\d/.test(li[2]);
      const items: string[] = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (m && /\d/.test(m[2]) === ordered && m[1].length <= li[1].length + 1) { items.push(m[3]); i++; }
        else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) { items[items.length - 1] += "\n" + lines[i].trim(); i++; }
        else break;
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={k++}>{items.map((t, j) => <li key={j}>{t.split("\n").map((s, x) => <Fragment key={x}>{x > 0 && <br />}{inline(s)}</Fragment>)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(\s*(```|~~~)|#{1,4}\s|\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) { para.push(lines[i++]); }
    blocks.push(<p key={k++}>{para.map((s, x) => <Fragment key={x}>{x > 0 && <br />}{inline(s, x)}</Fragment>)}</p>);
  }
  return <div className="md">{blocks}</div>;
}
