import { hydrateRoot } from "react-dom/client";
import { render } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { LegendRow } from "@/components/uikit/chartkit";

// The server draws colour swatches with the fixed palette, the browser with the real theme colours: those inline styles must be allowed to differ
// (React otherwise logs "A tree hydrated but some attributes of the server rendered HTML didn't match", seen on /components/).
describe("legend swatches and hydration", () => {
  it("marks the theme-coloured swatches as allowed to differ between server and browser", () => {
    const items = [{ name: "a", color: "#111111" }, { name: "b", color: "#222222" }];
    const html = renderToString(<LegendRow items={items} />);
    expect(html).toContain("chart-swatch");
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = document.createElement("div");
    host.innerHTML = renderToString(<LegendRow items={[{ name: "a", color: "#999999" }, { name: "b", color: "#888888" }]} />);   // what the server sent
    document.body.appendChild(host);
    
    const root = hydrateRoot(host, <LegendRow items={items} />);
    return new Promise<void>((done) => setTimeout(() => {
      expect(err.mock.calls.filter((c) => String(c[0]).includes("hydrat"))).toHaveLength(0);
      root.unmount(); err.mockRestore(); done();
    }, 50));
  });
});
