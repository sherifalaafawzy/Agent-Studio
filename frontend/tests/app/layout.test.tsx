import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import RootLayout, { metadata } from "@/app/layout";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

describe("RootLayout", () => {
  it("publishes the app title and description", () => {
    expect(metadata.title).toBe("Agent Studio");
    expect(metadata.description).toBe("Model-agnostic agent workbench");
  });

  it("wraps the page in the shell with the navigation and the children", () => {
    const { container } = render(<RootLayout><p>Page body</p></RootLayout>);
    expect(container.querySelector(".app main.main .page-body, .app main.main")).not.toBeNull();
    expect(screen.getByText("Page body")).toBeInTheDocument();
    expect(container.querySelector("aside.sb")).not.toBeNull();
    expect(container.querySelector(".mobile-bar")).not.toBeNull();
  });

  /** The theme boot script is server-rendered into <head>; pull it out of the static markup and run it. */
  function bootScript(): string {
    const html = renderToStaticMarkup(<RootLayout><span /></RootLayout>);
    const m = html.match(/<script>([\s\S]*?)<\/script>/);
    if (!m) throw new Error("theme boot script not rendered");
    return m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&");
  }

  it("applies a saved light or dark theme before first paint", () => {
    const script = bootScript();
    expect(script).toContain("as-theme");

    localStorage.setItem("as-theme", "dark");
    new Function(script)();
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");

    localStorage.setItem("as-theme", "light");
    new Function(script)();
    expect(document.documentElement).toHaveAttribute("data-theme", "light");

    localStorage.setItem("as-theme", "neon");
    document.documentElement.removeAttribute("data-theme");
    new Function(script)();
    expect(document.documentElement).not.toHaveAttribute("data-theme");
  });

  it("ignores storage that cannot be read", () => {
    const script = bootScript();
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => new Function(script)()).not.toThrow();
    spy.mockRestore();
  });
});
