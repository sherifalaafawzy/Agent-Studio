import { describe, expect, it } from "vitest";
import { buildIndex, search, searchDocs, stem, tokens } from "@/lib/docs/search";
import { docHref, findPage, pageBySlug, pageForRoute } from "@/lib/docs/links";
import { DOC_PAGES } from "@/lib/docs/generated";
import type { DocPage } from "@/lib/docs/types";

const mk = (slug: string, title: string, summary: string, intro: string, sections: [string, string][] = [], over: Partial<DocPage> = {}): DocPage => ({
  slug, title, section: "Build", order: 1, summary, routes: [], tags: [], intro, shots: [], words: 10,
  sections: sections.map(([heading, body]) => ({ id: heading.toLowerCase().replace(/\W+/g, "-"), level: 2 as const, heading, body })), ...over,
});

describe("tokens and stemming", () => {
  it("lowercases, drops stop words and short words, and stems plurals and endings", () => {
    expect(tokens("How do I set up the Agents?")).toEqual(["set", "up", "agent"]);
    expect(stem("queries")).toBe("query");
    expect(stem("boxes")).toBe("box");
    expect(stem("matches")).toBe("match");
    expect(stem("running")).toBe("runn");
    expect(stem("tested")).toBe("test");
    expect(stem("glass")).toBe("glass");
    expect(stem("go")).toBe("go");
  });
});

describe("search over a small set", () => {
  const pages = [
    mk("agents", "Agents", "Create agents with a wizard.", "An agent has instructions.", [["Wizard", "The wizard asks for a purpose and a name."], ["Sub-agents", "Hand work to helpers."]], { tags: ["wizard", "create"] }),
    mk("live", "Live mode", "Talk to a model out loud.", "Voice with an avatar.", [["Latency", "The first sound after you stop talking."], ["Cost", "A meter shows the cost per minute."]]),
    mk("mcp", "MCP servers", "Add-ons for agents.", "Servers give agents new abilities, such as browsing.", [["Credentials", "Credentials are write-only."]]),
  ];
  const idx = buildIndex(pages);

  it("finds a page by its title, its tags and its words", () => {
    expect(search(idx, "live mode")[0].slug).toBe("live");
    expect(search(idx, "wizard")[0]).toMatchObject({ slug: "agents" });
    expect(search(idx, "browsing servers")[0].slug).toBe("mcp");
  });

  it("lands on the heading that matches and gives a snippet around the words", () => {
    const hit = search(idx, "latency first sound")[0];
    expect(hit).toMatchObject({ slug: "live", heading: "Latency", anchor: "latency" });
    expect(hit.snippet.toLowerCase()).toContain("first sound");
    expect(hit.terms).toEqual(["latency", "first", "sound"]);
  });

  it("understands plurals, prefixes and a single typo", () => {
    expect(search(idx, "credential")[0].slug).toBe("mcp");
    expect(search(idx, "avat")[0].slug).toBe("live");
    expect(search(idx, "credentals")[0].slug).toBe("mcp");
    expect(search(idx, "latncy")[0].slug).toBe("live");
  });

  it("ranks entries that match all the words above entries that match only some", () => {
    const r = search(idx, "cost minute wizard");
    expect(r.length).toBeGreaterThan(1);
    expect(r[0].slug).toBe("live");
  });

  it("returns nothing for an empty or meaningless query and nothing for words that are not there", () => {
    expect(search(idx, "")).toEqual([]);
    expect(search(idx, "the a an")).toEqual([]);
    expect(search(idx, "zzzzzzqqq")).toEqual([]);
  });

  it("shows at most three hits per page and respects the limit", () => {
    const many = [mk("big", "Big", "s", "agent", [["a1", "agent one"], ["a2", "agent two"], ["a3", "agent three"], ["a4", "agent four"]]), mk("two", "Two", "s", "agent")];
    const r = search(buildIndex(many), "agent");
    expect(r.filter((h) => h.slug === "big")).toHaveLength(3);
    expect(search(buildIndex(many), "agent", 2)).toHaveLength(2);
  });

  it("falls back to the summary when a page has no body", () => {
    const r = search(buildIndex([mk("e", "Empty", "Summary words here", "")]), "summary");
    expect(r[0].snippet).toBe("Summary words here");
  });

  it("shortens long snippets with ellipses on both ends", () => {
    const long = "x ".repeat(80) + "needle " + "y ".repeat(120);
    const r = search(buildIndex([mk("l", "L", "s", long)]), "needle")[0];
    expect(r.snippet.startsWith("…")).toBe(true);
    expect(r.snippet.endsWith("…")).toBe(true);
  });

  it("strips markdown from what it indexes", () => {
    const r = search(buildIndex([mk("m", "M", "s", "Use [the link](doc:x) and `code` and ![img](shot:a)\n```\nsecretcodeword\n```")]), "secretcodeword");
    expect(r).toEqual([]);
    expect(search(buildIndex([mk("m", "M", "s", "Use [the link](doc:x)")]), "link")).toHaveLength(1);
  });

  it("caches the index per set of pages", () => {
    expect(searchDocs(pages, "wizard")[0].slug).toBe("agents");
    expect(searchDocs(pages, "avatar", 1)).toHaveLength(1);
    expect(searchDocs(DOC_PAGES, "avatar").length).toBeGreaterThan(0);
  });
});

describe("search over the real documentation", () => {
  const top = (q: string) => searchDocs(DOC_PAGES, q, 5).map((h) => h.slug);
  it.each([
    ["install ollama", "first-steps"],
    ["gemini avatar cost", "live-mode"],
    ["webhook address", "workflows"],
    ["openapi import endpoints", "workflows"],
    ["write-only credentials mcp", "mcp-servers"],
    ["spend limit refused", "billing-and-limits"],
    ["red team promptfoo email", "red-team"],
    ["canvas versions", "canvas"],
    ["knowledge base embedding", "knowledge"],
    ["ctrl+/ shortcut documentation panel", "assistant-and-docs"],
    ["approve change field assistant", "assistant-and-docs"],
    ["app builder preview blank", "apps"],
    ["hazard chemical weapon screen", "science-lab"],
  ])("%s -> %s", (q, slug) => {
    expect(top(q)).toContain(slug);
  });
});

describe("links", () => {
  it("turns doc: and app: links into addresses and leaves others alone", () => {
    expect(docHref("doc:agents")).toBe("/docs/agents/");
    expect(docHref("doc:agents#wizard")).toBe("/docs/agents/#wizard");
    expect(docHref("app:/mcp/?server=x")).toBe("/mcp/?server=x");
    expect(docHref("https://example.com")).toBe("https://example.com");
  });

  it("finds the page that explains a screen, the most specific first", () => {
    const pages = [mk("chat", "Chat", "s", "x", [], { routes: ["/"] }), mk("wf", "Workflows", "s", "x", [], { routes: ["/workflows/"] }), mk("boxes", "Boxes", "s", "x", [], { routes: ["/workflows/edit/"] }),
      mk("settings", "Settings", "s", "x", [], { routes: ["/settings/"] })];
    expect(pageForRoute(pages, "/")?.slug).toBe("chat");
    expect(pageForRoute(pages, "/?s=abc")?.slug).toBe("chat");
    expect(pageForRoute(pages, "/workflows")?.slug).toBe("wf");
    expect(pageForRoute(pages, "/workflows/?tab=runs")?.slug).toBe("wf");
    expect(pageForRoute(pages, "/workflows/edit/?id=1")?.slug).toBe("boxes");
    expect(pageForRoute(pages, "/settings/#providers")?.slug).toBe("settings");
    expect(pageForRoute(pages, "/nowhere/")).toBeNull();
    expect(pageForRoute(pages, "")?.slug).toBe("chat");
    expect(pageForRoute(DOC_PAGES, "/live/")?.slug).toBe("live-mode");
    expect(pageForRoute(DOC_PAGES, "/mcp/?server=playwright")?.slug).toBe("mcp-servers");
  });

  it("finds pages by slug, title or part of a title", () => {
    const pages = [mk("live-mode", "Live mode", "s", "x"), mk("agents", "Agents", "s", "x")];
    expect(pageBySlug(pages, "agents")?.slug).toBe("agents");
    expect(pageBySlug(pages, "zzz")).toBeUndefined();
    expect(findPage(pages, "live-mode")?.slug).toBe("live-mode");
    expect(findPage(pages, "/docs/agents/")?.slug).toBe("agents");
    expect(findPage(pages, "Live Mode")?.slug).toBe("live-mode");
    expect(findPage(pages, "live")?.slug).toBe("live-mode");
    expect(findPage(pages, "li")).toBeUndefined();
    expect(findPage(pages, "nothing")).toBeUndefined();
  });
});
