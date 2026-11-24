import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ROUTES, runTool, TOOLS, type ToolEnv } from "@/lib/assistantTools";
import { refOf } from "@/lib/pagectx";
import type { DocPage } from "@/lib/docs/types";

const page = (slug: string, title: string, body: string, sections: [string, string][] = []): DocPage => ({
  slug, title, section: "Build", order: 1, summary: `About ${title}`, routes: [], tags: [], intro: body, shots: [], words: 5,
  sections: sections.map(([h, b]) => ({ id: h.toLowerCase(), level: 2 as const, heading: h, body: b })),
});
const DOCS = [page("agents", "Agents", "Create agents", [["Wizard", "The wizard asks for a name."]]), page("live-mode", "Live mode", "Talk out loud with an avatar")];
const env = (over: Partial<ToolEnv> = {}): ToolEnv => ({ docs: async () => DOCS, ...over });
const screenOf = (html: string) => { document.body.innerHTML = `<div class="app"><main>${html}</main></div>`; };

beforeEach(() => screenOf(""));
afterEach(() => { document.body.innerHTML = ""; });

describe("tool declarations", () => {
  it("declare every tool with a description and a JSON schema, and the routes include docs and live", () => {
    for (const t of TOOLS) {
      expect(t.description.length).toBeGreaterThan(20);
      expect(t.parameters).toMatchObject({ type: "object" });
    }
    expect(ROUTES.map((r) => r.path)).toEqual(expect.arrayContaining(["/docs/", "/live/", "/science/", "/apps/", "/workflows/"]));
  });
});

describe("navigate", () => {
  it("opens a page, with an optional query or anchor, and waits for the screen when it can", async () => {
    const go = vi.fn();
    expect(runTool("navigate", { page: "/mcp/", target: "?server=playwright" }, go)).toBe("opened /mcp/?server=playwright");
    expect(go).toHaveBeenCalledWith("/mcp/?server=playwright");
    const settle = vi.fn(async () => {});
    expect(await runTool("navigate", { page: "/settings/", target: "#providers" }, go, [], env({ settle }))).toBe("opened /settings/#providers. Call read_page to see it.");
    expect(settle).toHaveBeenCalledWith("/settings/");
  });
  it("refuses a bad page or a target that is not a plain query or anchor", () => {
    const go = vi.fn();
    expect(runTool("navigate", { page: "/evil/" }, go)).toBe("unknown page /evil/");
    expect(runTool("navigate", { page: "/mcp/", target: "//evil.com" }, go)).toBe("bad target //evil.com");
    expect(runTool("navigate", { page: "/mcp/", target: "?a=<script>" }, go)).toMatch(/^bad target/);
    expect(go).not.toHaveBeenCalled();
  });
});

describe("read_page", () => {
  it("returns the screen as JSON without secrets", () => {
    screenOf(`<h1>Settings</h1><label>Name<input value="x"></label><label>API key<input value="sk-secret-value-1234"></label>`);
    const out = runTool("read_page", {}, vi.fn()) as string;
    expect(JSON.parse(out).title).toBe("Settings");
    expect(out).toContain('"label":"Name"');
    expect(out).not.toContain("sk-secret-value");
  });
});

describe("explain and guide_me", () => {
  it("explain can point at a field by reference", () => {
    screenOf(`<label>Name<input id="n"></label>`);
    const r = refOf(document.querySelector("#n")!);
    expect(runTool("explain", { target: r, text: "Type the name here" }, vi.fn())).toBe(`showed the card for ${r}`);
    expect(runTool("explain", { target: "f99999", text: "x" }, vi.fn())).toBe("f99999 is not on this page");
  });

  it("guide_me walks through several things, skipping what is not on the page", () => {
    screenOf(`<label>A<input id="a"></label><label>B<input id="b"></label><div data-tour="composer">c</div>`);
    const a = refOf(document.querySelector("#a")!), b = refOf(document.querySelector("#b")!);
    const out = runTool("guide_me", { steps: [{ target: a, text: "First" }, { target: b, title: "Second", text: "Then" }, { target: "composer", text: "Finally" }, { target: "nope", text: "x" }, { target: "a b", text: "y" }] }, vi.fn());
    expect(out).toBe("started a 3-step guide; not on this page: nope, a b");
    document.querySelectorAll(".driver-popover, .driver-overlay").forEach((e) => e.remove());
  });

  it("guide_me says so when nothing is there, caps the steps and tolerates bad input", () => {
    expect(runTool("guide_me", { steps: [{ target: "nope", text: "x" }] }, vi.fn())).toBe("none of those things are on this page (nope)");
    expect(runTool("guide_me", { steps: [] }, vi.fn())).toBe("none of those things are on this page");
    expect(runTool("guide_me", {}, vi.fn())).toBe("none of those things are on this page");
    screenOf(Array.from({ length: 12 }, (_, i) => `<input id="i${i}" aria-label="L${i}">`).join(""));
    const steps = Array.from({ length: 12 }, (_, i) => ({ target: refOf(document.querySelector(`#i${i}`)!), text: "t" }));
    expect(runTool("guide_me", { steps }, vi.fn())).toBe("started a 8-step guide");
    document.querySelectorAll(".driver-popover, .driver-overlay").forEach((e) => e.remove());
    expect(runTool("guide_me", { steps: [null, { target: refOf(document.querySelector("#i0")!) }] }, vi.fn())).toBe("started a 1-step guide; not on this page: ");
    document.querySelectorAll(".driver-popover, .driver-overlay").forEach((e) => e.remove());
  });
});

describe("documentation tools", () => {
  it("searches the documentation and lists what it found", async () => {
    const out = await runTool("search_docs", { query: "avatar" }, vi.fn(), [], env());
    expect(out).toMatch(/^- live-mode: Live mode - /);
    expect(await runTool("search_docs", { query: "zzzzqqq" }, vi.fn(), [], env())).toMatch(/^no documentation matched/);
    expect(await runTool("search_docs", {}, vi.fn(), [], env())).toMatch(/^no documentation matched/);
    expect(await runTool("search_docs", { query: "wizard name" }, vi.fn(), [], env())).toContain("agents#wizard: Agents > Wizard");
  });

  it("opens a page at a heading, and says when the heading or page does not exist", async () => {
    const openDocs = vi.fn();
    expect(await runTool("open_docs", { page: "agents", anchor: "wizard" }, vi.fn(), [], env({ openDocs }))).toBe('opened the documentation page "Agents" at wizard');
    expect(openDocs).toHaveBeenCalledWith("agents", "wizard");
    expect(await runTool("open_docs", { page: "Live mode", anchor: "#nothing" }, vi.fn(), [], env({ openDocs }))).toContain('(there is no heading "nothing", so it opened at the top)');
    expect(openDocs).toHaveBeenLastCalledWith("live-mode", undefined);
    expect(await runTool("open_docs", { page: "zzz" }, vi.fn(), [], env({ openDocs }))).toBe('no documentation page called "zzz". Pages: agents, live-mode');
    expect(await runTool("open_docs", {}, vi.fn(), [], env({ openDocs }))).toContain('no documentation page called ""');
    expect(await runTool("open_docs", { page: "agents" }, vi.fn(), [], env())).toContain("opened");          // no panel to open: still answers
  });

  it("loads the real documentation when none is given", async () => {
    expect(String(await runTool("search_docs", { query: "spend limit" }, vi.fn()))).toMatch(/billing-and-limits/);
  });
});

describe("changing form fields", () => {
  const form = () => screenOf(`<label>Name<input id="n" value="old"></label><label>Tier<select id="s"><option value="pro">Strong</option><option value="flash">Fast</option></select></label><label>Secret key<input id="k" value="x"></label>`);

  it("asks for approval with the before and after, then applies and reports it", async () => {
    form();
    const r = refOf(document.querySelector("#n")!);
    const approve = vi.fn(async () => true);
    const out = await runTool("set_field", { ref: r, value: "new", reason: "you asked" }, vi.fn(), [], env({ approve }));
    expect(approve).toHaveBeenCalledWith([{ ref: r, label: "Name", before: "old", after: "new", ok: true }], "you asked");
    expect(out).toBe("done: Name: old -> new");
    expect((document.querySelector("#n") as HTMLInputElement).value).toBe("new");
  });

  it("changes nothing when the person says no", async () => {
    form();
    const r = refOf(document.querySelector("#n")!);
    expect(await runTool("set_field", { ref: r, value: "new" }, vi.fn(), [], env({ approve: async () => false }))).toBe("the user declined the change; nothing was changed");
    expect((document.querySelector("#n") as HTMLInputElement).value).toBe("old");
  });

  it("does not even ask when it cannot be approved here or when nothing can change", async () => {
    form();
    const n = refOf(document.querySelector("#n")!), k = refOf(document.querySelector("#k")!);
    expect(await runTool("set_field", { ref: n, value: "z" }, vi.fn(), [], env())).toBe("this assistant cannot ask for approval here, so nothing was changed");
    const approve = vi.fn(async () => true);
    expect(await runTool("set_field", { ref: k, value: "z" }, vi.fn(), [], env({ approve }))).toBe("nothing to change. Secret key: this field holds a secret, so only you can fill it in");
    expect(approve).not.toHaveBeenCalled();
    expect(await runTool("fill_form", { changes: [] }, vi.fn(), [], env({ approve }))).toBe("no changes were given");
    expect(await runTool("fill_form", {}, vi.fn(), [], env({ approve }))).toBe("no changes were given");
    expect(await runTool("set_field", {}, vi.fn(), [], env({ approve }))).toMatch(/^nothing to change/);
  });

  it("fills several fields with one approval, applying the good ones and naming the skipped ones", async () => {
    form();
    const n = refOf(document.querySelector("#n")!), s = refOf(document.querySelector("#s")!), k = refOf(document.querySelector("#k")!);
    const approve = vi.fn(async () => true);
    const out = await runTool("fill_form", { changes: [{ ref: n, value: "A" }, { ref: s, value: "fast" }, { ref: k, value: "leak" }, null, { ref: "f777777" }], reason: "setup" }, vi.fn(), [], env({ approve }));
    expect(approve).toHaveBeenCalledTimes(1);
    expect((approve.mock.calls[0] as unknown[])[0]).toHaveLength(2);
    expect(out).toContain("done: Name: old -> A; done: Tier: Strong -> Fast");
    expect(out).toContain("skipped: Secret key: this field holds a secret");
    expect(out).toContain("f777777: that field is not on this screen any more");
    expect((document.querySelector("#k") as HTMLInputElement).value).toBe("x");
  });

  it("reports a field that went away between the approval and the change", async () => {
    form();
    const n = refOf(document.querySelector("#n")!);
    const out = await runTool("set_field", { ref: n, value: "z" }, vi.fn(), [], env({ approve: async () => { document.querySelector("#n")!.remove(); return true; } }));
    expect(out).toBe("failed: f" + n.slice(1) + ": that field is not on this screen any more");
  });

  it("handles at most twenty changes at once", async () => {
    screenOf(Array.from({ length: 25 }, (_, i) => `<label>F${i}<input id="i${i}" value="0"></label>`).join(""));
    const changes = Array.from({ length: 25 }, (_, i) => ({ ref: refOf(document.querySelector(`#i${i}`)!), value: "1" }));
    const approve = vi.fn(async () => true);
    await runTool("fill_form", { changes }, vi.fn(), [], env({ approve }));
    expect((approve.mock.calls[0] as unknown[])[0]).toHaveLength(20);
  });
});

describe("open_canvas", () => {
  it("opens a canvas with a safe type and a bounded size", async () => {
    const openCanvas = vi.fn(async (a: { title: string }) => `opened ${a.title}`);
    expect(await runTool("open_canvas", { title: "Notes", type: "html", content: "<p>x</p>", language: "x" }, vi.fn(), [], env({ openCanvas }))).toBe("opened Notes");
    expect(openCanvas).toHaveBeenLastCalledWith({ title: "Notes", type: "html", language: "x", content: "<p>x</p>" });
    await runTool("open_canvas", { type: "weird", title: "", content: "c".repeat(300000) }, vi.fn(), [], env({ openCanvas }));
    const last = openCanvas.mock.calls.at(-1)![0] as { title: string; type: string; content: string };
    expect(last).toMatchObject({ title: "Untitled", type: "markdown" });
    expect(last.content).toHaveLength(200000);
    expect(runTool("open_canvas", { title: "x" }, vi.fn())).toBe("the canvas cannot be opened from here");
  });
});

describe("switches and failures", () => {
  it("refuses every new tool when it is switched off", () => {
    for (const t of ["guide_me", "read_page", "open_docs", "search_docs", "set_field", "fill_form", "open_canvas"]) {
      expect(runTool(t, {}, vi.fn(), [t])).toBe(`${t} is switched off in Settings`);
    }
  });
  it("turns an unexpected error into text for the model", () => {
    expect(runTool("explain", { target: 'a"b', text: "x" }, vi.fn())).toMatch(/^failed: /);
    expect(runTool("nope", {}, vi.fn())).toBe("unknown tool nope");
  });
});
