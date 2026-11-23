import { afterEach, describe, expect, it, vi } from "vitest";
import { isDark, mix } from "@/components/uikit/MapBlock";
import { readColors } from "@/components/uikit/useColors";

describe("theme colours for canvases", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("tells a dark panel from a light one so land and sea keep their contrast", () => {
    expect(isDark("#2c2c2a")).toBe(true);
    expect(isDark("#fdfcf8")).toBe(false);
    expect(isDark("rgb(1,2,3)")).toBe(false);        // anything that is not #rrggbb is treated as light
  });

  it("draws the land lighter than the sea in the dark theme", () => {
    const panel = "#2c2c2a", axis = "#4a4843", fg = "#dcd9cf";
    const sea = mix(panel, axis, 0.06), land = mix(panel, fg, 0.17);
    const lum = (h: string) => parseInt(h.slice(1, 3), 16);
    expect(lum(land)).toBeGreaterThan(lum(sea));
  });

  it("returns the fallback palette when there is no document to read (server render)", () => {
    vi.stubGlobal("document", undefined);
    const c = readColors();
    expect(c.series).toHaveLength(8);
    expect(c.panel).toBe("#fff");
  });
});
