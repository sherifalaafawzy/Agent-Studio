// Offline full-text search over the documentation: no service, no network. The index is built once from the generated pages.
import type { DocPage } from "./types";

export type Hit = { slug: string; anchor: string; title: string; heading: string; section: string; snippet: string; score: number; terms: string[] };
type Entry = {
  slug: string; anchor: string; page: DocPage; heading: string; title: string; summary: string; body: string;
  tokens: Map<string, number>; titleT: Set<string>; headT: Set<string>; tagT: Set<string>; sumT: Set<string>;
};
export type Index = { entries: Entry[]; vocab: string[] };

const STOP = new Set(["the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "it", "my", "i", "do", "how", "what", "can", "with", "me", "this", "that", "be", "at", "by", "as", "are", "you", "your", "from"]);

/** Lowercase words, light stemming (plural / -ing / -ed), no stop words. */
export function tokens(s: string): string[] {
  return (s.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((w) => w.length > 1 && !STOP.has(w)).map(stem);
}

export function stem(w: string): string {
  if (w.length > 5 && w.endsWith("ing")) return w.slice(0, -3);
  if (w.length > 4 && w.endsWith("ies")) return w.slice(0, -3) + "y";
  if (w.length > 4 && w.endsWith("ed")) return w.slice(0, -2);
  if (w.length > 3 && /(s|x|z|ch|sh)es$/.test(w)) return w.slice(0, -2);
  if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) return w.slice(0, -1);
  return w;
}

const strip = (md: string) => md.replace(/```[\s\S]*?```/g, " ").replace(/!\[[^\]]*\]\([^)]*\)/g, " ").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[`*_>#|]/g, " ").replace(/\s+/g, " ").trim();

function entry(page: DocPage, anchor: string, heading: string, body: string): Entry {
  const clean = strip(body);
  const tk = new Map<string, number>();
  for (const t of tokens(clean)) tk.set(t, (tk.get(t) ?? 0) + 1);
  return {
    slug: page.slug, anchor, page, heading, title: page.title, summary: page.summary, body: clean, tokens: tk,
    titleT: new Set(tokens(page.title)), headT: new Set(tokens(heading)), tagT: new Set(tokens(page.tags.join(" "))), sumT: new Set(tokens(page.summary)),
  };
}

export function buildIndex(pages: DocPage[]): Index {
  const entries: Entry[] = [];
  for (const p of pages) {
    entries.push(entry(p, "", "", p.intro));
    for (const s of p.sections) entries.push(entry(p, s.id, s.heading, s.body));
  }
  const vocab = new Set<string>();
  for (const e of entries) for (const t of e.tokens.keys()) vocab.add(t);
  for (const e of entries) for (const set of [e.titleT, e.headT, e.tagT, e.sumT]) for (const t of set) vocab.add(t);
  return { entries, vocab: [...vocab] };
}

function within1(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0, j = 0, edits = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { i++; j++; continue; }
    if (++edits > 1) return false;
    if (a.length > b.length) i++;
    else if (a.length < b.length) j++;
    else { i++; j++; }
  }
  return edits + (a.length - i) + (b.length - j) <= 1;
}

/** Which words of the index a query word stands for: itself, words that start with it, or (for longer words) one typo away. */
function expand(q: string, vocab: string[]): { w: string; k: number }[] {
  const out: { w: string; k: number }[] = [];
  for (const v of vocab) {
    if (v === q) out.push({ w: v, k: 1 });
    else if (q.length >= 3 && v.startsWith(q)) out.push({ w: v, k: 0.6 });
    else if (q.length >= 5 && within1(q, v)) out.push({ w: v, k: 0.4 });
  }
  return out;
}

function snippetOf(body: string, terms: string[]): string {
  const low = body.toLowerCase();
  let at = -1;
  for (const t of terms) {
    const i = low.indexOf(t);
    if (i >= 0 && (at < 0 || i < at)) at = i;
  }
  if (at < 0) return body.slice(0, 160);
  const from = Math.max(0, at - 50);
  return (from > 0 ? "…" : "") + body.slice(from, from + 170) + (from + 170 < body.length ? "…" : "");
}

export function search(index: Index, query: string, limit = 12): Hit[] {
  const q = [...new Set(tokens(query))];
  if (!q.length) return [];
  const exp = q.map((t) => expand(t, index.vocab));
  const raw = query.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const scored: Hit[] = [];
  for (const e of index.entries) {
    let score = 0, matched = 0;
    for (let qi = 0; qi < q.length; qi++) {
      let best = 0;
      for (const { w, k } of exp[qi]) {
        let s = 0;
        const tf = e.tokens.get(w) ?? 0;
        if (tf) s += Math.min(3, 1 + Math.log2(tf));
        if (e.titleT.has(w)) s += 10;
        if (e.headT.has(w)) s += 6;
        if (e.tagT.has(w)) s += 5;
        if (e.sumT.has(w)) s += 3;
        best = Math.max(best, s * k);
      }
      if (best > 0) { score += best; matched++; }
    }
    if (!matched) continue;
    if (matched < q.length) score *= matched / q.length / 2;           // a missing word counts against an entry but does not hide it
    if (!e.anchor) score *= 1.15;                                       // the start of a page is a good place to land
    scored.push({ slug: e.slug, anchor: e.anchor, title: e.title, heading: e.heading, section: e.page.section, score, snippet: snippetOf(e.body || e.summary, raw), terms: raw });
  }
  scored.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  const perPage = new Map<string, number>();
  const out: Hit[] = [];
  for (const h of scored) {
    const n = perPage.get(h.slug) ?? 0;
    if (n >= 3) continue;
    perPage.set(h.slug, n + 1);
    out.push(h);
    if (out.length >= limit) break;
  }
  return out;
}

let cached: { pages: DocPage[]; index: Index } | null = null;
/** The shared index for the real documentation (built on first use). */
export function searchDocs(pages: DocPage[], query: string, limit?: number): Hit[] {
  if (!cached || cached.pages !== pages) cached = { pages, index: buildIndex(pages) };
  return search(cached.index, query, limit);
}
