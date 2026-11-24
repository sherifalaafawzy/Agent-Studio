"use client";
// The documentation inside the assistant panel: opens beside whatever the user is doing, so nothing has to be left to read it.
import { useEffect, useRef, useState } from "react";
import { DOC_PAGES } from "@/lib/docs/generated";
import { docHref, pageBySlug, pageForRoute } from "@/lib/docs/links";
import { DocBody, DocSearch, groupBySection } from "./DocContent";

export type DocsRequest = { slug: string; anchor?: string; n: number };
type View = { kind: "home" } | { kind: "page"; slug: string; anchor?: string };

export default function DocsPanel({ route, request, onApp, onFull }: { route: string; request: DocsRequest | null; onApp: (path: string) => void; onFull: (path: string) => void }) {
  const [view, setView] = useState<View>({ kind: "home" });
  const [q, setQ] = useState("");
  const body = useRef<HTMLDivElement>(null);
  const here = pageForRoute(DOC_PAGES, route);

  useEffect(() => {                                           // the assistant (or a "?" button) asked for a page
    if (request && pageBySlug(DOC_PAGES, request.slug)) { setView({ kind: "page", slug: request.slug, anchor: request.anchor }); setQ(""); }
  }, [request]);

  useEffect(() => {                                           // scroll to the heading once the page is drawn
    if (view.kind !== "page") return;
    const t = setTimeout(() => {
      const el = view.anchor ? body.current?.querySelector(`[id="${CSS.escape(view.anchor)}"]`) : null;
      if (el) el.scrollIntoView({ block: "start" }); else body.current?.scrollTo?.({ top: 0 });
    }, 30);
    return () => clearTimeout(t);
  }, [view]);

  const open = (slug: string, anchor?: string) => { setQ(""); setView({ kind: "page", slug, anchor: anchor || undefined }); };
  const onNav = (href: string) => {
    const h = docHref(href);
    const d = h.match(/^\/docs\/([\w-]+)\/(?:#([\w-]+))?$/);
    if (d && pageBySlug(DOC_PAGES, d[1])) open(d[1], d[2]); else onApp(h);
  };
  const page = view.kind === "page" ? pageBySlug(DOC_PAGES, view.slug) : undefined;

  return (
    <div className="dp" data-testid="docs-panel">
      <div className="dp-top">
        {view.kind === "page" && <button className="ghost small" onClick={() => { setView({ kind: "home" }); setQ(""); }}>← All pages</button>}
        {page && <button className="ghost small" onClick={() => onFull(`/docs/${page.slug}/${view.kind === "page" && view.anchor ? `#${view.anchor}` : ""}`)}>Open full page</button>}
      </div>
      <DocSearch query={q} onQuery={setQ} onOpen={open} />
      <div className="dp-body" ref={body}>
        {!q.trim() && view.kind === "home" && (
          <>
            {here && (
              <button className="dp-here" onClick={() => open(here.slug)} data-testid="docs-here">
                <span className="small mut">Help for this screen</span><b>{here.title}</b><span className="small mut">{here.summary}</span>
              </button>
            )}
            {groupBySection().map(([section, pages]) => (
              <div key={section} className="dp-group">
                <div className="doc-tree-h">{section}</div>
                {pages.map((p) => <button key={p.slug} className="dp-link" onClick={() => open(p.slug)}>{p.title}</button>)}
              </div>
            ))}
          </>
        )}
        {!q.trim() && page && <DocBody page={page} onNav={onNav} compact />}
        {!q.trim() && view.kind === "page" && !page && <p className="small mut">That page does not exist.</p>}
      </div>
    </div>
  );
}
