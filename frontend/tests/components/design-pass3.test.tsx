// Third design pass (November 2026): service tiles with bundled brand marks, tables that stack on a phone, one type scale,
// and the small layout rules that came with them.
import fs from "node:fs";
import path from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import BrandIcon, { markFor } from "@/components/BrandIcon";
import { BRAND_PATHS } from "@/lib/brandIcons";
import TableFx, { labelTable, MIN_COLUMNS, PHONE, shouldStack } from "@/components/TableFx";

vi.mock("next/navigation", () => ({ usePathname: () => "/runs/" }));

const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "globals.css"), "utf8");

function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contrast = (a: string, b: string) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

describe("which mark a service gets", () => {
  it("knows well-known servers, providers, agents and tools by their id", () => {
    for (const [id, label, slug] of [
      ["github", "GitHub", "github"], ["git", "Git", "git"], ["postgres", "PostgreSQL", "postgresql"], ["code-sandbox", "Code sandbox (Docker)", "docker"],
      ["chrome-devtools", "Chrome DevTools", "googlechrome"], ["google-analytics", "Google Analytics", "googleanalytics"], ["shopify-dev", "Shopify developer docs", "shopify"],
      ["vertex_ai", "Google Vertex AI (Gemini)", "googlecloud"], ["gemini", "Google Gemini API (AI Studio)", "googlegemini"], ["anthropic", "Anthropic", "anthropic"],
      ["ollama", "Ollama (local)", "ollama"], ["claude", "Claude Code", "claude"], ["cursor", "Cursor", "cursor"], ["claude_sdk", "Claude Agent SDK", "claude"],
      ["pydantic_ai", "Pydantic AI", "pydantic"], ["langgraph", "LangGraph", "langgraph"], ["garak", "garak", "nvidia"], ["gcp", "Google Vertex AI", "googlecloud"],
    ]) expect(markFor(id, label), id).toEqual({ kind: "brand", slug });
  });

  it("falls back to a word of the name for a server the user added under their own id", () => {
    expect(markFor("my-notes", "Team Notion workspace")).toEqual({ kind: "brand", slug: "notion" });
    expect(markFor("srv_1", "Brave Search")).toEqual({ kind: "brand", slug: "brave" });
    expect(markFor(undefined, "Jira")).toEqual({ kind: "brand", slug: "jira" });
  });

  it("gives built-in tools a plain glyph, and the id wins over words in the name", () => {
    expect(markFor("filesystem", "Filesystem")).toEqual({ kind: "glyph", name: "folder" });
    expect(markFor("memory", "Memory")).toEqual({ kind: "glyph", name: "brain" });
    expect(markFor("trainer", "Model trainer (Ludwig / PyTorch / Keras)")).toEqual({ kind: "glyph", name: "flask" });
    expect(markFor("cloudml", "Cloud ML (SageMaker, Vertex, Azure ML, DigitalOcean, RunPod, OCI)")).toEqual({ kind: "glyph", name: "cloud" });
    expect(markFor("mock", "Mock (no network, no spend)")).toEqual({ kind: "glyph", name: "flask" });
  });

  it("uses initials for anything else, including the brands whose marks are deliberately not bundled", () => {
    expect(markFor("openai", "OpenAI")).toEqual({ kind: "initials", text: "Op" });
    expect(markFor("bedrock", "Amazon Bedrock")).toEqual({ kind: "initials", text: "Am" });
    expect(markFor("xai", "xAI (Grok)")).toEqual({ kind: "initials", text: "xA" });
    expect(markFor("x1", "(beta)")).toEqual({ kind: "initials", text: "x1" });
    expect(markFor(undefined, undefined)).toEqual({ kind: "initials", text: "?" });
    expect(markFor("", "!!!")).toEqual({ kind: "initials", text: "?" });
    for (const gone of ["openai", "amazonaws", "microsoft", "slack", "oracle", "playwright"]) expect(BRAND_PATHS[gone]).toBeUndefined();
  });

  it("every mapped brand has bundled path data", () => {
    for (const id of ["github", "notion", "figma", "docker", "kubernetes", "mongodb", "sqlite", "huggingface", "openrouter", "mistral", "digitalocean", "redis", "stripe", "sentry", "linear"])
      { const m = markFor(id, id); expect(m.kind, id).toBe("brand"); expect(BRAND_PATHS[(m as { slug: string }).slug].d.length, id).toBeGreaterThan(20); }
  });
});

describe("the tile", () => {
  it("is decoration beside the name, the same square whatever is inside", () => {
    const { container } = render(<><BrandIcon id="github" label="GitHub" /><BrandIcon id="filesystem" label="Filesystem" size={40} /><BrandIcon id="zz" label="Zebra tools" className="x" /></>);
    const [brand, glyph, initials] = [...container.querySelectorAll<HTMLElement>(".brand-tile")];
    for (const t of [brand, glyph, initials]) expect(t.getAttribute("aria-hidden")).toBe("true");
    expect(brand.dataset.mark).toBe("github");
    expect(brand.style.width).toBe("32px");
    expect(brand.style.height).toBe("32px");
    expect(brand.querySelector("svg path")!.getAttribute("d")).toBe(BRAND_PATHS.github.d);
    expect(glyph.dataset.mark).toBe("folder");
    expect(glyph.style.width).toBe("40px");
    expect(glyph.querySelector("svg")!.getAttribute("fill")).toBe("none");
    expect(initials.dataset.mark).toBe("initials");
    expect(initials.textContent).toBe("Ze");
    expect(initials.className).toContain("x");
    expect(initials.querySelector("svg")).toBeNull();
  });

  it("uses a brand's own colour only where it is readable on the tile, per theme", () => {
    const tiles = { light: ["#f0eee6", "#fdfcf8"], dark: ["#33322f", "#2c2c2a"] };
    for (const [slug, b] of Object.entries(BRAND_PATHS)) {
      expect(b.hex, slug).toMatch(/^#[0-9a-f]{6}$/);
      for (const theme of ["light", "dark"] as const) {
        const worst = Math.min(...tiles[theme].map((bg) => contrast(b.hex, bg)));
        expect(b[theme], `${slug} ${theme} (${worst.toFixed(2)}:1)`).toBe(worst >= 3);
      }
    }
    const { container } = render(<><BrandIcon id="github" label="GitHub" /><BrandIcon id="docker" label="Docker" /></>);
    const [github, docker] = [...container.querySelectorAll<HTMLElement>(".brand-tile")];
    expect(github.style.getPropertyValue("--brand")).toBe(BRAND_PATHS.github.hex);
    expect(github.hasAttribute("data-l")).toBe(BRAND_PATHS.github.light);
    expect(github.hasAttribute("data-d")).toBe(BRAND_PATHS.github.dark);
    expect(docker.hasAttribute("data-l")).toBe(BRAND_PATHS.docker.light);
    expect(css).toContain(".brand-tile[data-l] svg{color:var(--brand)}");
    expect(css).toContain(':root[data-theme="dark"] .brand-tile[data-d] svg{color:var(--brand)}');
  });
});

describe("tables that do not fit", () => {
  const table = (cols: string[], rows: string[][], wrap = "") => {
    document.body.innerHTML = `<div class="main ${wrap}"><table><thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    return document.querySelector("table")!;
  };
  const size = (el: Element, prop: "clientWidth" | "offsetWidth", value: number) => Object.defineProperty(el, prop, { configurable: true, value });

  it("on a phone, names each cell after its column and stacks a table of three or more columns", () => {
    const t = table(["Run", "Agent", " Provider ", "Messages", ""], [["Hello", "assistant", "ollama", "4", "x"]]);
    expect(labelTable(t, 375)).toBe(true);
    expect(t.dataset.stack).toBe("1");
    expect([...t.querySelectorAll("td")].map((c) => c.dataset.label)).toEqual(["Run", "Agent", "Provider", "Messages", ""]);
    expect(labelTable(t, 375)).toBe(true);                              // a second pass changes nothing
    expect(MIN_COLUMNS).toBe(3);
    expect(PHONE).toBe(640);
  });

  it("on a wide window a table that fits stays a table", () => {
    const t = table(["Run", "Agent", "Provider"], [["Hello", "assistant", "ollama"]]);
    expect(labelTable(t)).toBe(false);                                  // the test window is 1024 wide; nothing is laid out, so nothing overflows
    expect(t.dataset.stack).toBe("0");
    size(t.parentElement!, "clientWidth", 700); size(t, "offsetWidth", 700);
    expect(labelTable(t, 768)).toBe(false);
    expect(shouldStack(t, 3, 768)).toBe(false);
  });

  it("at any width a table wider than its room is stacked, and stays stacked until there is room for it", () => {
    const t = table(["Framework", "Tools", "MCP", "Memory"], [["ADK", "yes", "yes", "yes"]]);
    size(t.parentElement!, "clientWidth", 694); size(t, "offsetWidth", 1164);
    expect(labelTable(t, 768)).toBe(true);
    expect(t.dataset.stack).toBe("1");
    size(t, "offsetWidth", 694);                                        // stacked, it is as wide as its room: that must not un-stack it
    expect(labelTable(t, 768)).toBe(true);
    size(t.parentElement!, "clientWidth", 1000);
    expect(labelTable(t, 1100)).toBe(true);                             // still narrower than the 1164 it needed
    size(t.parentElement!, "clientWidth", 1200);
    expect(labelTable(t, 1440)).toBe(false);
    expect(t.dataset.stack).toBe("0");
    const two = table(["Key", "Value"], [["a", "b"]]);
    size(two.parentElement!, "clientWidth", 300); size(two, "offsetWidth", 520);
    expect(labelTable(two, 375)).toBe(true);                            // two columns stack only when they overflow
    const inner = table(["Agent", "What it does", "Runs"], [["a", "b", "c"]]);
    size(inner.parentElement!, "clientWidth", 694); size(inner, "offsetWidth", 694);
    Object.defineProperty(inner, "scrollWidth", { configurable: true, value: 3902 });
    expect(labelTable(inner, 768)).toBe(true);                          // a table that scrolls inside itself still counts as too wide
    const fits = table(["Key", "Value"], [["a", "b"]]);
    expect(labelTable(fits, 375)).toBe(false);
  });

  it("leaves tables without a header row, tables in answers and rows that span columns alone", () => {
    document.body.innerHTML = `<div class="main"><table><tbody><tr><td>a</td><td>b</td><td>c</td></tr></tbody></table></div>`;
    expect(labelTable(document.querySelector("table")!, 375)).toBe(false);
    expect(document.querySelector("table")!.dataset.stack).toBeUndefined();
    expect(labelTable(table(["A", "B", "C", "D"], [["1", "2", "3", "4"]], "md"), 375)).toBe(false);
    const t = table(["A", "B", "C", "D"], [["1", "2", "3", "4"]]);
    t.querySelector("tbody")!.insertAdjacentHTML("beforeend", `<tr><td colspan="4">details</td></tr>`);
    expect(labelTable(t, 375)).toBe(true);
    expect(t.querySelectorAll("tr")[2].querySelector("td")!.dataset.label).toBeUndefined();
    const lone = document.createElement("table");                       // not in the page at all: no room to measure
    lone.innerHTML = "<thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody>";
    expect(labelTable(lone, 1024)).toBe(false);
  });

  it("labels tables on the page, picks up rows that arrive later, follows the window's width and stops when removed", async () => {
    table(["A", "B", "C", "D"], [["1", "2", "3", "4"]]);
    const width = (w: number) => Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
    width(375);
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((f) => { frames.push(f); return frames.length; });
    const caf = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    const flush = () => { const f = frames.splice(0); f.forEach((x) => x(0)); };
    const host = document.createElement("div"); document.body.appendChild(host);
    const { unmount } = render(<TableFx />, { container: host });
    flush();
    expect(document.querySelector("table")!.dataset.stack).toBe("1");
    document.querySelector("tbody")!.insertAdjacentHTML("beforeend", "<tr><td>5</td><td>6</td><td>7</td><td>8</td></tr>");
    await Promise.resolve(); await new Promise((r) => setTimeout(r, 0));
    flush();
    expect(document.querySelectorAll("tr")[2].querySelector("td")!.dataset.label).toBe("A");
    width(1024);
    window.dispatchEvent(new Event("resize"));
    flush();
    expect(document.querySelector("table")!.dataset.stack).toBe("0");
    unmount();
    expect(caf).toHaveBeenCalled();
    window.dispatchEvent(new Event("resize"));
    expect(frames).toHaveLength(0);
    raf.mockRestore(); caf.mockRestore();
  });

  it("the stylesheet draws only marked tables as cards", () => {
    expect(css).toContain('table[data-stack="1"]>tbody>tr>td[data-label]::before{content:attr(data-label)');
    expect(css).not.toMatch(/table\[data-stack\][>,{]/);
  });
});

describe("the type scale", () => {
  it("uses one scale: 12 13 14 16 18 20 24 28 32, three display sizes, and 11 only inside charts", () => {
    const allowed = new Set([12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 56, 72]);
    const off: string[] = [];
    for (const m of css.matchAll(/([^{}]*)\{([^{}]*)\}/g)) for (const f of m[2].matchAll(/font-size:(\d+(?:\.\d+)?)px/g)) {
      const v = Number(f[1]);
      if (allowed.has(v)) continue;
      if (v === 11 && /chart-tick|cost-chart/.test(m[1])) continue;
      off.push(`${m[1].trim()} ${v}px`);
    }
    expect(off).toEqual([]);
  });

  it("inline styles in pages and components keep to it", () => {
    const allowed = new Set([11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40]);
    const walk = (d: string): string[] => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : e.name.endsWith(".tsx") ? [path.join(d, e.name)] : []));
    const off: string[] = [];
    for (const f of [...walk(path.join(__dirname, "..", "..", "app")), ...walk(path.join(__dirname, "..", "..", "components"))])
      for (const m of fs.readFileSync(f, "utf8").matchAll(/fontSize: ?(\d+(?:\.\d+)?)(?=[,} ])/g)) if (!allowed.has(Number(m[1]))) off.push(`${path.basename(f)} ${m[1]}`);
    expect(off).toEqual([]);
  });
});

describe("small layout rules of the pass", () => {
  it("a switch's checkbox covers the switch and shows a focus ring", () => {
    expect(css).toContain(".switch input{inset:0;width:100%;height:100%");
    expect(css).toContain(".switch input:focus-visible+i{outline:2px solid var(--acc)");
  });
  it("an i inside a stacked label gets its own column instead of sitting on the label's text", () => {
    expect(css).toMatch(/label:not\(\.check\):not\(\.row\):not\(\[style\*="flex-direction: row"\]\):has\(>\.infotip\)\{display:grid/);
  });
  it("catalog cards stretch to one height with their actions on the last line", () => {
    expect(css).toContain(".cat-grid{align-items:stretch}");
    expect(css).toContain(".cat-card .cat-actions{margin-top:auto");
  });
});

import { mainOption } from "@/lib/asks";
import { fieldApplies, optionWord } from "@/components/workflows/Fields";

describe("approval cards and schedule boxes", () => {
  it("draws one main button on a permission request: allowing once", () => {
    expect(mainOption([{ id: "a", kind: "allow_always" }, { id: "b", kind: "allow_once" }, { id: "c", kind: "reject_once" }])).toBe("b");
    expect(mainOption([{ id: "a", kind: "allow_always" }, { id: "c", kind: "reject_once" }])).toBe("a");
    expect(mainOption([{ id: "c", kind: "reject_once" }, { id: "d" }])).toBeNull();
    expect(mainOption([])).toBeNull();
  });

  it("a Schedule box shows only the fields of the chosen way of repeating", () => {
    const shown = (mode?: string) => ["mode", "every_minutes", "at", "cron", "task", "missed", "max_per_day"].filter((n) => fieldApplies("schedule", mode ? { mode } : {}, n));
    expect(shown("interval")).toEqual(["mode", "every_minutes", "task", "missed", "max_per_day"]);
    expect(shown()).toEqual(shown("interval"));
    expect(shown("daily")).toEqual(["mode", "at", "task", "missed", "max_per_day"]);
    expect(shown("cron")).toEqual(["mode", "cron", "task", "missed", "max_per_day"]);
    expect(fieldApplies("llm", { mode: "cron" }, "at")).toBe(true);        // only Schedule boxes hide fields
    expect(optionWord("schedule", "cron")).toBe("A cron pattern (advanced)");
    expect(optionWord("schedule", "run_once")).toBe("Run it once when the app is open again");
    expect(optionWord("schedule", "something-new")).toBe("something-new");
    expect(optionWord("webhook", "body")).toBe("body");
  });
});
