// Turning the links written in the documentation into real addresses, and finding the page that explains a screen.
import type { DocPage } from "./types";

/** doc:slug#anchor -> /docs/slug/#anchor ; app:/path -> /path ; anything else is left alone. */
export function docHref(href: string): string {
  const d = href.match(/^doc:([\w-]+)(?:#([\w-]+))?$/);
  if (d) return `/docs/${d[1]}/${d[2] ? `#${d[2]}` : ""}`;
  const a = href.match(/^app:(\/\S*)$/);
  return a ? a[1] : href;
}

const norm = (p: string) => {
  const [path, q] = (p || "/").split("#")[0].split("?");
  return (path.length > 1 ? path.replace(/\/+$/, "") : "/") + (q ? `?${q}` : "");
};

/** The documentation page for the screen the user is on (the longest matching "routes:" entry wins). */
export function pageForRoute(pages: DocPage[], route: string): DocPage | null {
  const r = norm(route), rp = r.split("?")[0];
  let best: { p: DocPage; n: number } | null = null;
  for (const p of pages) {
    for (const raw of p.routes) {
      const t = norm(raw), tp = t.split("?")[0];
      const hit = r === t || (tp === "/" ? rp === "/" : (rp + "/").startsWith(tp + "/"));
      if (hit && (!best || t.length > best.n)) best = { p, n: t.length };
    }
  }
  return best?.p ?? null;
}

export function pageBySlug(pages: DocPage[], slug: string): DocPage | undefined {
  return pages.find((p) => p.slug === slug);
}

/** Finds a page by its slug, its exact title, or a title that contains the words ("agents", "live mode"). */
export function findPage(pages: DocPage[], what: string): DocPage | undefined {
  const w = what.trim().toLowerCase().replace(/^\/?docs\/?/, "").replace(/\/+$/, "");
  return pages.find((p) => p.slug === w) ?? pages.find((p) => p.title.toLowerCase() === w) ?? (w.length > 2 ? pages.find((p) => p.title.toLowerCase().includes(w)) : undefined);
}
