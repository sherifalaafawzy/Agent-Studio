// The tools the browser offers to the side assistant (AG-UI "frontend tools"). The model asks; these run here, on this screen.
// Looking and pointing are free. Changing anything (a form field) is only ever done after the person approves a before/after card.
import { driver } from "driver.js";
import { TOURS, TOUR_NAMES } from "@/lib/tour";
import { collectContext, contextText, elementFor, previewChange, applyChange, type FieldChange, type FieldPreview } from "@/lib/pagectx";
import { searchDocs } from "@/lib/docs/search";
import { findPage } from "@/lib/docs/links";
import type { DocPage } from "@/lib/docs/types";
import { startTour } from "@/components/TourRunner";

/** Pages the assistant may open. Keep in step with the sidebar. */
export const ROUTES: { path: string; label: string }[] = [
  { path: "/", label: "Chat" }, { path: "/dashboard/", label: "Dashboard" }, { path: "/live/", label: "Live" }, { path: "/runs/", label: "Runs" },
  { path: "/external/", label: "External agents" }, { path: "/agents/", label: "Agent Studio" }, { path: "/skills/", label: "Skills" },
  { path: "/knowledge/", label: "Knowledge" }, { path: "/mcp/", label: "MCP servers" }, { path: "/memory/", label: "Memory" }, { path: "/catalog/", label: "Catalog" }, { path: "/security/", label: "Security" }, { path: "/evals/", label: "Evaluations" }, { path: "/artifacts/", label: "Artifacts" }, { path: "/components/", label: "Component gallery" }, { path: "/workflows/", label: "Workflows" }, { path: "/apps/", label: "Apps" }, { path: "/science/", label: "Science" }, { path: "/telemetry/", label: "Telemetry" }, { path: "/discovery/", label: "Discovery & AI-BOM" },
  { path: "/billing/", label: "Billing" }, { path: "/settings/", label: "Settings" }, { path: "/setup/", label: "Set-up guide" }, { path: "/docs/", label: "Documentation" },
];

/** Things on screen that carry data-tour="..." and may be pointed at. */
export const TARGETS: string[] = Array.from(new Set(
  Object.values(TOURS).flatMap((t) => t.steps.map((s) => s.element)).filter((x): x is string => !!x)));

const obj = (properties: Record<string, unknown>, required: string[] = []) => ({ type: "object", properties, required });
const POINT = "A name from the list, or a field reference (f1, f2...) from read_page.";

export const TOOLS = [
  { name: "navigate", description: "Open a page of Agent Studio. 'target' may add a query or anchor such as ?server=playwright or #providers.",
    parameters: obj({ page: { type: "string", enum: ROUTES.map((r) => r.path) }, target: { type: "string" } }, ["page"]) },
  { name: "start_tour", description: "Start a guided tour that walks the user through part of the app with highlighted cards.",
    parameters: obj({ tour: { type: "string", enum: TOUR_NAMES } }, ["tour"]) },
  { name: "explain", description: "Highlight one part of the current screen with a card explaining it. " + POINT,
    parameters: obj({ target: { type: "string" }, title: { type: "string" }, text: { type: "string" } }, ["target", "text"]) },
  { name: "guide_me", description: "Walk the user through several things on the current screen, one highlighted card after another (at most 8). Use it for 'show me how to...'. " + POINT,
    parameters: obj({ steps: { type: "array", items: obj({ target: { type: "string" }, title: { type: "string" }, text: { type: "string" } }, ["target", "text"]) } }, ["steps"]) },
  { name: "read_page", description: "Read what is on the screen now: its title, headings, form fields (with references like f3), buttons, tabs, selected items and error messages. Secrets are never included. Call it again after the screen changes.",
    parameters: obj({}) },
  { name: "open_docs", description: "Open a documentation page in the side panel, without leaving the screen. 'page' is a page name or address; 'anchor' is a heading id.",
    parameters: obj({ page: { type: "string" }, anchor: { type: "string" } }, ["page"]) },
  { name: "search_docs", description: "Search the documentation. Returns the best matching pages and headings, with the text around the match.",
    parameters: obj({ query: { type: "string" } }, ["query"]) },
  { name: "set_field", description: "Propose a new value for one form field on the screen (reference from read_page). The user sees a before/after card and must approve; fields that hold secrets can never be changed.",
    parameters: obj({ ref: { type: "string" }, value: { type: ["string", "boolean"] }, reason: { type: "string" } }, ["ref", "value"]) },
  { name: "fill_form", description: "Propose values for several form fields at once. One before/after card, one approval for all.",
    parameters: obj({ changes: { type: "array", items: obj({ ref: { type: "string" }, value: { type: ["string", "boolean"] } }, ["ref", "value"]) }, reason: { type: "string" } }, ["changes"]) },
  { name: "open_canvas", description: "Open a new canvas (a document, code file, table or web page that you and the user edit side by side next to the chat).",
    parameters: obj({ title: { type: "string" }, type: { type: "string", enum: ["markdown", "code", "table", "html", "text"] }, language: { type: "string" }, content: { type: "string" } }, ["title"]) },
];

export type ToolEnv = {
  openDocs?: (slug: string, anchor?: string) => void;
  docs?: () => Promise<DocPage[]>;
  approve?: (changes: FieldPreview[], reason: string) => Promise<boolean>;
  settle?: (path: string) => Promise<void>;
  openCanvas?: (a: { title: string; type: string; language: string; content: string }) => Promise<string>;
};

const defaultDocs = () => import("@/lib/docs/generated").then((m) => m.DOC_PAGES);
let highlighter: ReturnType<typeof driver> | null = null;
const SUFFIX = /^(\?[\w=&%.\-]*)?(#[\w-]*)?$/;
const MAX_STEPS = 8;

function pop(el: Element, title: string | undefined, text: string) {
  return { element: el, popover: { title: title || undefined, description: text, side: "left" as const, align: "center" as const } };
}

function review(changes: FieldChange[]): FieldPreview[] {
  return changes.slice(0, 20).map(previewChange);
}

/** Asks for approval of the checked changes, then applies them. Every refusal says why, so the model can tell the user. */
async function proposeChanges(changes: FieldChange[], reason: string, env: ToolEnv): Promise<string> {
  if (!changes.length) return "no changes were given";
  const items = review(changes);
  const good = items.filter((i) => i.ok);
  const bad = items.filter((i) => !i.ok).map((i) => `${i.label}: ${i.why}`);
  if (!good.length) return `nothing to change. ${bad.join("; ")}`;
  if (!env.approve) return "this assistant cannot ask for approval here, so nothing was changed";
  const yes = await env.approve(good, reason);
  if (!yes) return "the user declined the change; nothing was changed";
  const done = changes.filter((c) => good.some((g) => g.ref === c.ref)).map((c) => applyChange(c));
  const text = done.map((d) => (d.ok ? `done: ${d.text}` : `failed: ${d.text}`)).join("; ");
  return bad.length ? `${text}; skipped: ${bad.join("; ")}` : text;
}

/** Runs one browser tool. The returned text goes back to the model as the tool result. */
export function runTool(name: string, args: Record<string, unknown>, go: (path: string) => void, off: string[] = [], env: ToolEnv = {}): string | Promise<string> {
  if (off.includes(name)) return `${name} is switched off in Settings`;
  try {
    if (name === "navigate") {
      const page = String(args.page ?? "");
      if (!ROUTES.some((r) => r.path === page)) return `unknown page ${page}`;
      const target = String(args.target ?? "");
      if (!SUFFIX.test(target)) return `bad target ${target}`;
      go(page + target);
      const said = `opened ${page}${target}`;
      return env.settle ? env.settle(page).then(() => `${said}. Call read_page to see it.`) : said;
    }
    if (name === "start_tour") {
      const tour = String(args.tour ?? "");
      if (!TOURS[tour]) return `unknown tour ${tour}`;
      startTour(tour);
      return `started the ${tour} tour`;
    }
    if (name === "explain") {
      const target = String(args.target ?? "");
      const el = /^f\d+$/.test(target) ? elementFor(target) : document.querySelector(`[data-tour="${target}"]`);
      if (!el) return `${target} is not on this page`;
      highlighter?.destroy();
      highlighter = driver({ allowClose: true, popoverClass: "as-tour-pop", stagePadding: 6, stageRadius: 12 });
      highlighter.highlight({ element: el, popover: { title: String(args.title ?? "") || undefined, description: String(args.text ?? ""), side: "left", align: "center" } });
      return `showed the card for ${target}`;
    }
    if (name === "guide_me") {
      const list = Array.isArray(args.steps) ? (args.steps as Record<string, unknown>[]).slice(0, MAX_STEPS) : [];
      const steps: ReturnType<typeof pop>[] = [], missing: string[] = [];
      for (const s of list) {
        const t = String(s?.target ?? "");
        const el = /^f\d+$/.test(t) ? elementFor(t) : /^[\w-]+$/.test(t) ? document.querySelector(`[data-tour="${t}"]`) : null;
        if (el) steps.push(pop(el, String(s.title ?? ""), String(s.text ?? ""))); else missing.push(t);
      }
      if (!steps.length) return `none of those things are on this page${missing.length ? ` (${missing.join(", ")})` : ""}`;
      highlighter?.destroy();
      highlighter = driver({ steps, allowClose: true, showProgress: true, popoverClass: "as-tour-pop", stagePadding: 6, stageRadius: 12 });
      highlighter.drive();
      return `started a ${steps.length}-step guide${missing.length ? `; not on this page: ${missing.join(", ")}` : ""}`;
    }
    if (name === "read_page") return contextText(collectContext());
    if (name === "open_docs" || name === "search_docs") return (env.docs ?? defaultDocs)().then((pages) => docsTool(name, args, pages, env));
    if (name === "set_field") return proposeChanges([{ ref: String(args.ref ?? ""), value: args.value as string | boolean ?? "" }], String(args.reason ?? ""), env);
    if (name === "fill_form") {
      const ch = Array.isArray(args.changes) ? (args.changes as Record<string, unknown>[]).slice(0, 20).map((c) => ({ ref: String(c?.ref ?? ""), value: (c?.value ?? "") as string | boolean })) : [];
      return proposeChanges(ch, String(args.reason ?? ""), env);
    }
    if (name === "open_canvas") {
      if (!env.openCanvas) return "the canvas cannot be opened from here";
      const type = ["markdown", "code", "table", "html", "text"].includes(String(args.type)) ? String(args.type) : "markdown";
      return env.openCanvas({ title: String(args.title ?? "Untitled").slice(0, 120) || "Untitled", type, language: String(args.language ?? "").slice(0, 30), content: String(args.content ?? "").slice(0, 200000) });
    }
    return `unknown tool ${name}`;
  } catch (e) {
    return `failed: ${(e as Error).message}`;
  }
}

function docsTool(name: string, args: Record<string, unknown>, pages: DocPage[], env: ToolEnv): string {
  if (name === "search_docs") {
    const hits = searchDocs(pages, String(args.query ?? ""), 5);
    if (!hits.length) return "no documentation matched. Try other words, or open_docs with a page name.";
    return hits.map((h) => `- ${h.slug}${h.anchor ? `#${h.anchor}` : ""}: ${h.title}${h.heading ? ` > ${h.heading}` : ""} - ${h.snippet}`).join("\n");
  }
  const page = findPage(pages, String(args.page ?? ""));
  if (!page) return `no documentation page called "${String(args.page ?? "")}". Pages: ${pages.map((p) => p.slug).join(", ")}`;
  const anchor = String(args.anchor ?? "").replace(/^#/, "");
  const known = anchor && page.sections.some((s) => s.id === anchor) ? anchor : "";
  env.openDocs?.(page.slug, known || undefined);
  return `opened the documentation page "${page.title}"${known ? ` at ${known}` : ""}${anchor && !known ? ` (there is no heading "${anchor}", so it opened at the top)` : ""}`;
}
