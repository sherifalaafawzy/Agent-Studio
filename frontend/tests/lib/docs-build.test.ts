import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { buildDocs, loadShots, parsePage, render, ROOT, SECTIONS, slugify, validate } from "../../scripts/build-docs.mjs";
import { DOC_PAGES, DOC_SHOTS } from "@/lib/docs/generated";

const page = (extra = "", body = "Intro text.\n\n## First\nBody one.\n\n### Deeper\nBody two.\n") =>
  `---\ntitle: T\nsection: Build\norder: 5\nsummary: S\ntags: a, b\nroutes: /x/, /y/\n${extra}---\n${body}`;

describe("parsePage", () => {
  it("reads the header, the intro and every heading with an address", () => {
    const p = parsePage("thing.md", page());
    expect(p).toMatchObject({ slug: "thing", title: "T", section: "Build", order: 5, summary: "S", tags: ["a", "b"], routes: ["/x/", "/y/"], intro: "Intro text." });
    expect(p.sections.map((s: { id: string; level: number }) => [s.id, s.level])).toEqual([["first", 2], ["deeper", 3]]);
    expect(p.sections[0].body).toBe("Body one.");
    expect(p.words).toBeGreaterThan(5);
  });

  it("gives repeated headings different addresses and ignores headings inside code fences", () => {
    const p = parsePage("a.md", page("", "## Same\nx\n## Same\ny\n```\n## not a heading\n```\n"));
    expect(p.sections.map((s: { id: string }) => s.id)).toEqual(["same", "same-2"]);
    expect(p.sections[1].body).toContain("## not a heading");
  });

  it("collects the screenshots a page uses and defaults order and lists", () => {
    const p = parsePage("a.md", "---\ntitle: T\nsection: Reference\nsummary: S\n---\n![x](shot:one)\n\n## H\n![y](shot:two)\n");
    expect(p.shots).toEqual(["one", "two"]);
    expect(p.order).toBe(100);
    expect(p.tags).toEqual([]);
    expect(p.routes).toEqual([]);
  });

  it("refuses a page with no header, a missing field or an unknown section", () => {
    expect(() => parsePage("a.md", "no header")).toThrow(/must start with a ---/);
    expect(() => parsePage("a.md", "---\ntitle: T\nsection: Build\n---\nx")).toThrow(/missing "summary:"/);
    expect(() => parsePage("a.md", "---\ntitle: T\nsection: Nope\nsummary: S\n---\nx")).toThrow(/unknown section "Nope"/);
  });

  it("slugifies headings", () => {
    expect(slugify("Live profile")).toBe("live-profile");
    expect(slugify("Guardrails & identity")).toBe("guardrails-and-identity");
    expect(slugify("`code` thing!")).toBe("code-thing");
    expect(slugify("???")).toBe("section");
  });
});

describe("validate", () => {
  const a = parsePage("a.md", page("", "Intro [b](doc:b#deeper) and [app](app:/agents/?agent=x#ag-live) ![s](shot:one)\n\n## H\nx\n"));
  const b = parsePage("b.md", page());
  it("accepts links to real pages, anchors, screens and screenshots", () => {
    expect(validate([a, b], new Set(["one"]))).toEqual([]);
  });
  it("reports every promise the pages cannot keep", () => {
    const bad = parsePage("c.md", page("", "[x](doc:nope) [y](doc:b#missing) [z](app:agents) ![s](shot:gone)\n"));
    const empty = parsePage("d.md", "---\ntitle: T\nsection: Build\nsummary: S\n---\n");
    const problems = validate([bad, b, empty, parsePage("b.md", page())], new Set());
    expect(problems.join("\n")).toMatch(/unknown page "nope"/);
    expect(problems.join("\n")).toMatch(/unknown anchor b#missing/);
    expect(problems.join("\n")).toMatch(/bad app link agents/);
    expect(problems.join("\n")).toMatch(/screenshot "gone"/);
    expect(problems.join("\n")).toMatch(/d: the page is empty/);
    expect(problems.join("\n")).toMatch(/duplicate page b/);
  });
});

describe("buildDocs", () => {
  it("builds from a folder, ordering by section then order, and fails loudly on a broken link", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "docs-"));
    fs.writeFileSync(path.join(dir, "b.md"), page("", "Hello [a](doc:a)\n").replace("order: 5", "order: 2").replace("section: Build", "section: Reference"));
    fs.writeFileSync(path.join(dir, "a.md"), page());
    fs.writeFileSync(path.join(dir, "shots.json"), JSON.stringify({ shots: [{ id: "s", alt: "A shot", mobile: true }] }));
    const d = buildDocs(dir);
    expect(d.pages.map((p: { slug: string }) => p.slug)).toEqual(["a", "b"]);                 // Build comes before Reference
    expect(d.shots).toEqual([{ id: "s", alt: "A shot", file: "s.webp", mobile: true }]);
    expect(render(d)).toContain("export const DOC_PAGES");
    fs.writeFileSync(path.join(dir, "c.md"), page("", "[x](doc:zzz)\n"));
    expect(() => buildDocs(dir)).toThrow(/documentation has problems/);
  });

  it("treats a folder with no shots.json as having no screenshots", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "docs-"));
    expect(loadShots(dir)).toEqual({ shots: [] });
  });
});

describe("the real documentation", () => {
  it("is up to date: lib/docs/generated.ts is exactly what docs-src builds", () => {
    const now = fs.readFileSync(path.join(ROOT, "lib", "docs", "generated.ts"), "utf8");
    expect(now).toBe(render(buildDocs()));
  });

  it("covers every part of the app", () => {
    const slugs = DOC_PAGES.map((p) => p.slug);
    for (const s of ["welcome", "first-steps", "concepts", "chat", "canvas", "live-mode", "agents", "agent-editor", "workflows", "workflow-boxes", "apps", "science-lab", "skills", "knowledge", "mcp-servers", "catalog", "memory",
      "rich-answers", "telemetry", "security", "guardrails", "red-team", "evaluations", "ai-bom", "settings", "providers", "assistant-and-docs", "api-and-a2a", "troubleshooting", "glossary", "billing-and-limits", "runs", "artifacts", "external-agents"]) {
      expect(slugs, s).toContain(s);
    }
    expect(DOC_PAGES.filter((p) => p.section === "How-to").length).toBeGreaterThanOrEqual(8);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("uses only known sections, real routes and gives every page a summary", () => {
    for (const p of DOC_PAGES) {
      expect(SECTIONS).toContain(p.section);
      expect(p.summary.length).toBeGreaterThan(20);
      for (const r of p.routes) expect(r).toMatch(/^\//);
    }
  });

  it("has a screenshot file for every screenshot it shows, desktop and phone", () => {
    const missing = DOC_SHOTS.filter((s) => !fs.existsSync(path.join(ROOT, "public", "docs", "shots", s.file))).map((s) => s.id);
    expect(missing).toEqual([]);
    expect(DOC_SHOTS.some((s) => s.mobile)).toBe(true);
    expect(DOC_SHOTS.length).toBeGreaterThanOrEqual(25);
  });

  it("never mentions a key-shaped value", () => {
    const all = DOC_PAGES.map((p) => [p.intro, ...p.sections.map((s) => s.body)].join("\n")).join("\n");
    expect(all).not.toMatch(/sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}/);
  });
});
