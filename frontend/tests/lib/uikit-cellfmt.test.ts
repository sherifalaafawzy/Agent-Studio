import { describe, expect, it } from "vitest";
import { badgeIcon, badgeTone, columnRange, linkCell, progressOf, showCell } from "@/lib/uikit/cellfmt";
import type { Col } from "@/lib/tables";

const col = (o: Partial<Col> = {}): Col => ({ key: "k", label: "K", type: "number", ...o });

describe("how a cell is shown", () => {
  it("formats by column format and falls back to the raw text", () => {
    expect(showCell(col(), null)).toBe(""); expect(showCell(col(), "")).toBe("");
    expect(showCell(col({ format: "currency", currency: "EUR" }), 1234)).toMatch(/1,234|1.234/); expect(showCell(col({ format: "currency" }), "x")).toBe("x");
    expect(showCell(col({ format: "percent" }), 0.256)).toBe("25.6%"); expect(showCell(col({ format: "percent", decimals: 0 }), 0.256)).toBe("26%"); expect(showCell(col({ format: "percent" }), "x")).toBe("x");
    expect(showCell(col({ format: "number", unit: "ms" }), 12345)).toBe("12.3k ms"); expect(showCell(col({ format: "number", decimals: 2 }), 1.5)).toBe("1.50"); expect(showCell(col({ format: "number" }), "x")).toBe("x");
    expect(showCell(col({ format: "bytes" }), 2048)).toBe("2 KB"); expect(showCell(col({ format: "bytes" }), "x")).toBe("x");
    expect(showCell(col({ format: "duration" }), 125)).toBe("2m 5s"); expect(showCell(col({ format: "duration" }), "x")).toBe("x");
    expect(showCell(col({ format: "date" }), "2025-03-04")).toBeTruthy(); expect(showCell(col({ format: "date" }), "nope")).toBe("nope");
    expect(showCell(col({ format: "datetime" }), "2025-03-04T10:00:00Z")).toBeTruthy(); expect(showCell(col({ format: "datetime" }), "nope")).toBe("nope");
    expect(showCell(col({ unit: "kg" }), 5)).toBe("5 kg"); expect(showCell(col({}), 5)).toBe("5"); expect(showCell(col({ type: "text", unit: "kg" }), "abc")).toBe("abc");
  });
  it("picks a badge tone with an icon", () => {
    expect(badgeTone("On track")).toBe("ok"); expect(badgeTone("failed")).toBe("bad"); expect(badgeTone("pending")).toBe("warn"); expect(badgeTone("other")).toBe("");
    expect([badgeIcon("ok"), badgeIcon("bad"), badgeIcon("warn"), badgeIcon("")]).toEqual(["✓", "✕", "!", "•"]);
  });
  it("makes links, progress and ranges", () => {
    expect(linkCell("https://a.co")).toEqual({ href: "https://a.co", text: "https://a.co" }); expect(linkCell("javascript:1").href).toBeNull();
    expect(progressOf(0.5)).toBe(0.5); expect(progressOf(50)).toBe(0.5); expect(progressOf(500)).toBe(1); expect(progressOf("x")).toBe(0);
    expect(columnRange(col(), [1, 5, "x"])).toEqual({ lo: 0, hi: 5 }); expect(columnRange(col({ min: 2, max: 2 }), [])).toEqual({ lo: 2, hi: 3 }); expect(columnRange(col(), [])).toEqual({ lo: 0, hi: 1 });
    expect(columnRange(col({ min: -4, max: 9 }), [1])).toEqual({ lo: -4, hi: 9 });
  });
});
