"use client";
// The full Documentation page: page tree and search on the left, the page in the middle, "on this page" on the right.
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DOC_PAGES } from "@/lib/docs/generated";
import { docHref } from "@/lib/docs/links";
import { DocBody, DocSearch, DocTree, groupBySection, neighbours } from "./DocContent";

export default function DocsApp({ slug }: { slug?: string }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [treeOpen, setTreeOpen] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  const page = slug ? DOC_PAGES.find((p) => p.slug === slug) : undefined;

  useEffect(() => { if (window.matchMedia?.("(max-width: 820px)").matches) setTreeOpen(false); }, []);   // on a phone the page comes first and the list of pages is one tap away

  const open = (path: string) => { setQ(""); router.push(path); };
  const onNav = (href: string) => open(docHref(href));
  const openHit = (s: string, a: string) => open(`/docs/${s}/${a ? `#${a}` : ""}`);

  // Jump to the heading named in the address (#anchor) once the page is drawn, and again when only the anchor changes.
  useEffect(() => {
    const go = () => {
      const id = decodeURIComponent(location.hash.replace(/^#/, ""));
      const el = id ? root.current?.querySelector(`[id="${CSS.escape(id)}"]`) : null;
      if (el) el.scrollIntoView({ block: "start" }); else if (!id) window.scrollTo?.({ top: 0 });
    };
    const t = setTimeout(go, 30);
    window.addEventListener("hashchange", go);
    return () => { clearTimeout(t); window.removeEventListener("hashchange", go); };
  }, [slug]);

  const { prev, next } = slug ? neighbours(slug) : {};
  return (
    <div className="docs-layout" ref={root}>
      <aside className="docs-side">
        <DocSearch query={q} onQuery={setQ} onOpen={openHit} />
        <details className="docs-tree-wrap" open={treeOpen} onToggle={(e) => setTreeOpen((e.currentTarget as HTMLDetailsElement).open)}>
          <summary>All pages</summary>
          <DocTree current={slug} onOpen={(s) => open(`/docs/${s}/`)} />
        </details>
      </aside>
      <main className="docs-main" id="docs-main">
        {!slug && (
          <>
            <h1 className="doc-title">Documentation</h1>
            <p className="doc-summary">How to use Agent Studio, and how to build things with it. Search above, pick a page, or press <kbd>Ctrl</kbd> <kbd>/</kbd> anywhere to open this beside what you are doing.</p>
            {groupBySection().map(([section, pages]) => (
              <section key={section} className="doc-home-sec">
                <h2>{section}</h2>
                <div className="doc-cards">
                  {pages.map((p) => (
                    <a key={p.slug} className="doc-card" href={`/docs/${p.slug}/`} onClick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); open(`/docs/${p.slug}/`); }}>
                      <b>{p.title}</b><span className="small mut">{p.summary}</span>
                    </a>
                  ))}
                </div>
              </section>
            ))}
          </>
        )}
        {slug && !page && (
          <div className="empty" data-testid="doc-missing"><p>There is no documentation page called <code>{slug}</code>.</p><a href="/docs/">Back to the documentation</a></div>
        )}
        {page && (
          <>
            <DocBody page={page} onNav={onNav} />
            <div className="doc-pn">
              {prev ? <a href={`/docs/${prev.slug}/`} onClick={(e) => { e.preventDefault(); open(`/docs/${prev.slug}/`); }}>← {prev.title}</a> : <span />}
              {next ? <a href={`/docs/${next.slug}/`} onClick={(e) => { e.preventDefault(); open(`/docs/${next.slug}/`); }}>{next.title} →</a> : <span />}
            </div>
          </>
        )}
      </main>
      {page && page.sections.length > 1 && (
        <nav className="docs-toc" aria-label="On this page">
          <div className="doc-tree-h">On this page</div>
          {page.sections.map((s) => <a key={s.id} href={`#${s.id}`} className={s.level === 3 ? "sub" : ""}>{s.heading}</a>)}
        </nav>
      )}
    </div>
  );
}
