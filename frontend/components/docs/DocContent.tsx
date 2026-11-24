"use client";
// Pieces shared by the full Documentation page and the slide-in panel: a page body, its screenshots, the page tree and the search box.
import { useMemo, useState } from "react";
import Markdown, { DocNavContext } from "@/components/Markdown";
import { DOC_PAGES, DOC_SHOTS } from "@/lib/docs/generated";
import { searchDocs, type Hit } from "@/lib/docs/search";
import { docHref } from "@/lib/docs/links";
import type { DocPage } from "@/lib/docs/types";

const SHOT = /^!\[([^\]]*)\]\(shot:([\w-]+)\)\s*$/;

function Shot({ id, alt }: { id: string; alt: string }) {
  const [missing, setMissing] = useState(false);
  const shot = DOC_SHOTS.find((s) => s.id === id);
  if (!shot) return null;
  return (
    <figure className={`doc-shot${shot.mobile ? " phone" : ""}`} data-testid={`doc-shot-${id}`}>
      {missing
        ? <div className="doc-shot-miss small mut">Screenshot not captured yet. Run <code>npm run docs:shots</code>.</div>
        : <img src={`/docs/shots/${shot.file}`} alt={alt || shot.alt} loading="lazy" onError={() => setMissing(true)} />}
      <figcaption>{alt || shot.alt}</figcaption>
    </figure>
  );
}

/** Markdown with screenshots: a line that is just ![alt](shot:id) becomes a figure, everything else is rendered as Markdown. */
export function Blocks({ md }: { md: string }) {
  const parts = useMemo(() => {
    const out: ({ md: string } | { shot: string; alt: string })[] = [];
    let buf: string[] = [], fence = false;
    const flush = () => { if (buf.join("").trim()) out.push({ md: buf.join("\n") }); buf = []; };
    for (const line of md.split("\n")) {
      if (/^\s*(```|~~~)/.test(line)) fence = !fence;
      const m = !fence && line.match(SHOT);
      if (m) { flush(); out.push({ shot: m[2], alt: m[1] }); } else buf.push(line);
    }
    flush();
    return out;
  }, [md]);
  return <>{parts.map((p, i) => ("shot" in p ? <Shot key={i} id={p.shot} alt={p.alt} /> : <Markdown key={i} text={p.md} />))}</>;
}

/** One documentation page. `onNav` receives doc:/app: links so the host decides how to open them. */
export function DocBody({ page, onNav, compact = false }: { page: DocPage; onNav: (href: string) => void; compact?: boolean }) {
  return (
    <DocNavContext.Provider value={onNav}>
      <article className={`doc-body${compact ? " compact" : ""}`} data-testid={`doc-${page.slug}`}>
        <h1 className="doc-title">{page.title}</h1>
        <p className="doc-summary">{page.summary}</p>
        {page.intro && <Blocks md={page.intro} />}
        {page.sections.map((s) => {
          const H = (s.level === 2 ? "h2" : "h3") as "h2";
          return (
            <section key={s.id} className="doc-sec">
              <H id={s.id}>{s.heading}<a className="doc-anchor" href={`#${s.id}`} aria-label={`Link to ${s.heading}`} onClick={(e) => { e.preventDefault(); onNav(`doc:${page.slug}#${s.id}`); }}>#</a></H>
              <Blocks md={s.body} />
            </section>
          );
        })}
      </article>
    </DocNavContext.Provider>
  );
}

/** Where a link written in the docs leads, as a path the router can open (doc:/app: links), or null for a link that stays on the page. */
export function hrefOf(href: string): string | null {
  const h = docHref(href);
  return h === href ? null : h;
}

export function groupBySection(pages: DocPage[] = DOC_PAGES): [string, DocPage[]][] {
  const out: [string, DocPage[]][] = [];
  for (const p of pages) {
    const last = out[out.length - 1];
    if (last && last[0] === p.section) last[1].push(p); else out.push([p.section, [p]]);
  }
  return out;
}

/** The page tree: sections with their pages. */
export function DocTree({ current, onOpen }: { current?: string; onOpen: (slug: string) => void }) {
  return (
    <nav className="doc-tree" aria-label="Documentation pages">
      {groupBySection().map(([section, pages]) => (
        <div key={section}>
          <div className="doc-tree-h">{section}</div>
          {pages.map((p) => (
            <a key={p.slug} href={`/docs/${p.slug}/`} className={p.slug === current ? "on" : ""} aria-current={p.slug === current ? "page" : undefined}
              onClick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); onOpen(p.slug); }}>{p.title}</a>
          ))}
        </div>
      ))}
    </nav>
  );
}

function mark(text: string, terms: string[]): React.ReactNode[] {
  const t = terms.filter((x) => x.length > 1);
  if (!t.length) return [text];
  const re = new RegExp(`(${t.map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

/** The results of a search, as a list of links. */
export function Results({ hits, onOpen }: { hits: Hit[]; onOpen: (slug: string, anchor: string) => void }) {
  if (!hits.length) return <p className="small mut" data-testid="doc-no-results">Nothing matched. Try fewer or different words.</p>;
  return (
    <ul className="doc-hits" data-testid="doc-results">
      {hits.map((h, i) => (
        <li key={`${h.slug}#${h.anchor}${i}`}>
          <a href={`/docs/${h.slug}/${h.anchor ? `#${h.anchor}` : ""}`} onClick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); onOpen(h.slug, h.anchor); }}>
            <span className="t">{h.title}{h.heading ? <span className="mut"> › {h.heading}</span> : null}</span>
            <span className="s small mut">{mark(h.snippet, h.terms)}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

/** A search box that shows its results under it. */
export function DocSearch({ query, onQuery, onOpen, autoFocus = false }: { query: string; onQuery: (q: string) => void; onOpen: (slug: string, anchor: string) => void; autoFocus?: boolean }) {
  return (
    <div className="doc-search" role="search">
      <input type="search" value={query} placeholder="Search the documentation…" aria-label="Search the documentation" autoFocus={autoFocus} onChange={(e) => onQuery(e.target.value)} />
      {query.trim() && <Results hits={searchDocs(DOC_PAGES, query, 10)} onOpen={onOpen} />}
    </div>
  );
}

export function neighbours(slug: string): { prev?: DocPage; next?: DocPage } {
  const i = DOC_PAGES.findIndex((p) => p.slug === slug);
  return i < 0 ? {} : { prev: DOC_PAGES[i - 1], next: DOC_PAGES[i + 1] };
}
