// Tests for the shared pieces added by the November 2026 design review (docs/DESIGN-REVIEW.md).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings/" }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => <a href={href} {...rest}>{children}</a> }));

import TabStripFx, { revealSelected } from "@/components/TabStripFx";
import { Ticks } from "@/components/Ticks";
import { Modal } from "@/components/Modal";
import NotFound from "@/app/not-found";
import { inkOn } from "@/components/Runs";
import { tint } from "@/components/mcp/shared";

/** jsdom has no layout: give an element the sizes a real tab row would have. */
function size(el: HTMLElement, v: { scrollWidth: number; clientWidth: number; offsetLeft?: number; offsetWidth?: number }) {
  for (const [k, n] of Object.entries(v)) Object.defineProperty(el, k, { configurable: true, value: n });
}

describe("tab rows", () => {
  it("brings the selected tab into view once, and leaves a row the user scrolled alone", () => {
    const row = document.createElement("div");
    row.innerHTML = `<button class="tab">One</button><button class="tab on">Far away</button>`;
    size(row, { scrollWidth: 1700, clientWidth: 340, offsetLeft: 16 });
    const far = row.querySelector<HTMLElement>(".on")!;
    size(far, { scrollWidth: 0, clientWidth: 0, offsetLeft: 1500, offsetWidth: 90 });
    revealSelected(row);
    expect(row.scrollLeft).toBeGreaterThan(1000);              // centred on the selected tab
    row.scrollLeft = 0;                                        // the user scrolls back to look at the first tabs
    revealSelected(row);
    expect(row.scrollLeft).toBe(0);                            // same selection: not snapped back
  });

  it("does nothing for a row that fits, has no selection, or already shows the selected tab", () => {
    const fits = document.createElement("div");
    fits.innerHTML = `<button class="tab on">Only</button>`;
    size(fits, { scrollWidth: 200, clientWidth: 300 });
    revealSelected(fits);
    expect(fits.scrollLeft).toBe(0);
    const none = document.createElement("div");
    size(none, { scrollWidth: 900, clientWidth: 300 });
    revealSelected(none);
    expect(none.scrollLeft).toBe(0);
    const seen = document.createElement("div");
    seen.innerHTML = `<button role="tab" aria-selected="true">Second</button>`;
    size(seen, { scrollWidth: 900, clientWidth: 300, offsetLeft: 0 });
    size(seen.querySelector<HTMLElement>("button")!, { scrollWidth: 0, clientWidth: 0, offsetLeft: 60, offsetWidth: 80 });
    revealSelected(seen);
    expect(seen.scrollLeft).toBe(0);
  });

  describe("on a page", () => {
    let frames: FrameRequestCallback[] = [];
    beforeEach(() => {
      frames = [];
      vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => { frames.push(f); return frames.length; });
      vi.stubGlobal("cancelAnimationFrame", (n: number) => { frames[n - 1] = () => {}; });
    });
    afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });
    const flush = () => act(() => { const run = frames; frames = []; run.forEach((f) => f(0)); });
    const row = (html: string) => {
      const el = document.createElement("div");
      el.className = "tabs";
      el.innerHTML = html;
      size(el, { scrollWidth: 1700, clientWidth: 340, offsetLeft: 0 });
      el.querySelectorAll<HTMLElement>("button").forEach((b, i) => size(b, { scrollWidth: 0, clientWidth: 0, offsetLeft: i * 700, offsetWidth: 90 }));
      return el;
    };

    it("shows the selected tab on load, after a click in a row and when a row arrives late; never writes an attribute; stops when removed", async () => {
      const first = row(`<button class="tab">A</button><button class="tab">B</button><button class="tab on">C</button>`);
      document.body.appendChild(first);
      const before = first.outerHTML;
      const { unmount } = render(<TabStripFx />);
      flush();
      expect(first.scrollLeft).toBeGreaterThan(1000);
      expect(first.outerHTML).toBe(before);                     // React owns the attributes: none was added

      const [a, , c] = [...first.querySelectorAll("button")];
      c.classList.remove("on"); a.classList.add("on");          // what the page does when a tab is picked
      fireEvent.click(a);
      flush();
      expect(first.scrollLeft).toBe(0);
      fireEvent.click(document.body);                           // a click outside any row schedules nothing
      expect(frames.length).toBe(0);

      const late = row(`<button class="tab">A</button><button class="tab on">B</button>`);
      await act(async () => { document.body.appendChild(late); await Promise.resolve(); });
      flush();
      expect(late.scrollLeft).toBeGreaterThan(400);

      unmount();
      frames = [];
      a.classList.remove("on"); c.classList.add("on");
      fireEvent.click(c);
      expect(frames.length).toBe(0);                            // no longer listening
      expect(first.scrollLeft).toBe(0);
    });
  });
});

describe("Ticks", () => {
  it("draws `quoted` parts as code and leaves the rest as text", () => {
    const { container } = render(<p><Ticks text={"Run `ollama pull <name>` in a terminal, then `ollama list`."} /></p>);
    expect([...container.querySelectorAll("code")].map((c) => c.textContent)).toEqual(["ollama pull <name>", "ollama list"]);
    expect(container.textContent).toBe("Run ollama pull <name> in a terminal, then ollama list.");
  });
  it("leaves text without backticks, and a lone backtick, as they are", () => {
    const { container } = render(<p><Ticks text={"Nothing quoted ` here"} /></p>);
    expect(container.querySelector("code")).toBeNull();
    expect(container.textContent).toBe("Nothing quoted ` here");
  });
});

describe("Modal", () => {
  it("is announced as a modal dialog and keeps the close button beside the title", () => {
    const close = vi.fn();
    render(<Modal title="A long dialog title" onClose={close}><p>Body</p></Modal>);
    const dialog = screen.getByRole("dialog", { name: "A long dialog title" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const head = dialog.querySelector(".modal-head")!;
    expect(head).toContainElement(screen.getByRole("heading", { name: "A long dialog title" }));
    expect(head).toContainElement(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(close).toHaveBeenCalledTimes(1);
  });
});

describe("the page for a wrong address", () => {
  it("says so in the app's own words and offers three ways back", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { name: "This page does not exist" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the chat" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Open the dashboard" })).toHaveAttribute("href", "/dashboard/");
    expect(screen.getByRole("link", { name: "Read the documentation" })).toHaveAttribute("href", "/docs/");
  });
});

// ---------------------------------------------------------------------------------------------------- colour contrast
// The palette is checked by arithmetic, not by eye: every text colour must reach WCAG AA (4.5:1) on every surface it is used on.
function tokens(css: string, block: RegExp): Record<string, string> {
  const m = css.match(block);
  if (!m) throw new Error("token block not found");
  return Object.fromEntries([...m[1].matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)].map((x) => [x[1], x[2]]));
}
function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string) { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); }

describe("the palette", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "globals.css"), "utf8");
  const themes = { light: tokens(css, /:root\{([\s\S]*?)\n\}/), dark: tokens(css, /:root\[data-theme="dark"\]\{([\s\S]*?)\n\}/) };
  const surfaces = ["bg", "side", "panel", "panel2", "raise"];
  const soft = { "acc-text": "acc-soft", "ok-text": "ok-soft", "warn-text": "warn-soft", "bad-text": "bad-soft" } as const;

  it.each(Object.keys(themes))("%s: body and muted text reach 4.5:1 on every surface", (name) => {
    const t = themes[name as keyof typeof themes];
    for (const fg of ["fg", "fg2", "mut"]) for (const s of surfaces) expect(contrast(t[fg], t[s]), `${fg} on ${s}`).toBeGreaterThanOrEqual(4.5);
  });

  it.each(Object.keys(themes))("%s: accent and status text reach 4.5:1 on every surface and on their own tint", (name) => {
    const t = themes[name as keyof typeof themes];
    for (const [fg, tint] of Object.entries(soft)) {
      for (const s of surfaces) expect(contrast(t[fg], t[s]), `${fg} on ${s}`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t[fg], t[tint]), `${fg} on ${tint}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(Object.keys(themes))("%s: the label on an accent button reaches 4.5:1", (name) => {
    const t = themes[name as keyof typeof themes];
    expect(contrast(t["acc-fg"], t.acc)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["acc-fg"], t["acc-h"])).toBeGreaterThanOrEqual(4.5);
  });

  it("the two dark blocks (system setting and chosen by hand) are the same", () => {
    const system = tokens(css, /@media \(prefers-color-scheme:dark\)\{:root:not\(\[data-theme="light"\]\)\{([\s\S]*?)\n\}\}/);
    expect(system).toEqual(themes.dark);
  });

  it("no rule uses a fill colour for text: text takes the -text tokens", () => {
    expect(css.match(/(?<![-a-z])color:var\(--(acc|ok|warn|bad|faint)\)/g) ?? []).toEqual([]);
  });
});

describe("text on generated colours", () => {
  it("picks white on a dark lane and dark ink on a light one, and falls back to white for anything that is not a hex colour", () => {
    expect(inkOn("#1c4fa0")).toBe("#fff");
    expect(inkOn("#c46aa6")).toBe("#000");                     // the pink lane: white was 3.5:1
    expect(inkOn("d9a43a")).toBe("#000");
    expect(inkOn("var(--viz-1)")).toBe("#fff");
    for (const lane of ["#4f7fd9", "#e0794f", "#2aa58f", "#d9a43a", "#5aa452", "#8a66d2", "#d0574f", "#3d9fcc", "#c46aa6", "#7b8a3b", "#777777"])
      expect(contrast(inkOn(lane) === "#fff" ? "#ffffff" : "#000000", lane), lane).toBeGreaterThanOrEqual(4.5);
  });

  it("gives every server tile a tint that carries white initials at 4.5:1", () => {
    const tints = new Set(Array.from({ length: 200 }, (_, i) => tint(`server-${i}`)));
    expect(tints.size).toBe(8);
    for (const t of tints) expect(contrast("#ffffff", t), t).toBeGreaterThanOrEqual(4.5);
  });
});

describe("the type scale", () => {
  it("uses whole pixels for text (chart tick labels inside SVG are the exception)", () => {
    const css = fs.readFileSync(path.join(__dirname, "..", "..", "app", "globals.css"), "utf8");
    const halves = [...css.matchAll(/([^{}]*)\{[^{}]*font-size:\d+\.5px/g)].map((m) => m[1].trim()).filter((sel) => !/tick|tl-axis/.test(sel));
    expect(halves).toEqual([]);
  });
});
