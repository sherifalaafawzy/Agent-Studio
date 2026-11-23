import { describe, expect, it } from "vitest";
import { ago, labelShow, parseObject, recordsTable, tableChart, VERIFIED_TEXT, when } from "@/lib/science";

describe("science helpers", () => {
  it("shows every label with a symbol and a tone, and a neutral one for an unknown label", () => {
    for (const l of ["established", "single_source", "computed", "model_inference", "conflicting", "unsupported"]) expect(labelShow(l).icon).not.toBe("·");
    expect(labelShow("established").tone).toBe("ok");
    expect(labelShow("unsupported").tone).toBe("bad");
    expect(labelShow("weird")).toEqual({ icon: "·", tone: "mut" });
    expect(VERIFIED_TEXT[0]).toBe("could not verify");
  });

  it("describes time in words", () => {
    expect(ago(100, 110)).toBe("just now");
    expect(ago(0, 600)).toBe("10 min ago");
    expect(ago(0, 7200)).toBe("2 h ago");
    expect(ago(0, 86400 * 3)).toBe("3 days ago");
    expect(when(0)).toBe("unknown");
    expect(when(1700000000)).toBe("2023-11-14 22:13 UTC");
  });

  it("turns search records into a table spec with the fields they share", () => {
    const t = recordsTable([{ id: "1", title: "A", summary: "", url: "", kind: "paper", fields: { doi: "10.1/x", year: 2020 } }, { id: "2", title: "B", summary: "", url: "", kind: "paper", fields: { year: "2021", cid: null } }]);
    expect(t.columns.map((c) => c.key)).toEqual(["id", "title", "doi", "year", "cid"]);
    expect(t.rows).toEqual([["1", "A", "10.1/x", "2020", ""], ["2", "B", "", "2021", ""]]);
  });

  it("builds a line chart spec from a table result with rounded numbers", () => {
    const c = tableChart([0, 0.3333333333, 1], [0, 0.1234567891, 1], "sin(x)");
    expect(c).toEqual({ type: "chart.line", title: "sin(x)", x: ["0", "0.333333", "1"], series: [{ name: "sin(x)", values: [0, 0.123457, 1] }] });
  });

  it("parses the variables a person types without throwing", () => {
    expect(parseObject("")).toEqual({ value: {} });
    expect(parseObject('{"m": 2}')).toEqual({ value: { m: 2 } });
    expect(parseObject("[1]").error).toMatch(/JSON object/);
    expect(parseObject("{bad").error).toMatch(/JSON object/);
    expect(parseObject("null").error).toMatch(/JSON object/);
  });
});
