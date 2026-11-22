import { describe, expect, it, vi } from "vitest";
import { cellText, colLetter, compareCells, crc32, downloadFile, fileSafe, kindOf, parseTable, splitLine, tableFromDelimited, toCsv, toJson, toMarkdown, toTsv, toXlsx, zip, MAX_CELL, MAX_COLS, MAX_ROWS, type Col } from "@/lib/tables";

const cols = (...t: Col["type"][]): Col[] => t.map((type, i) => ({ key: `c${i}`, label: `C${i}`, type }));

describe("parsing", () => {
  it("splits delimited lines with quotes", () => {
    expect(splitLine('a,"b,c",d', ",")).toEqual(["a", "b,c", "d"]);
    expect(splitLine('"say ""hi""",x', ",")).toEqual(['say "hi"', "x"]);
    expect(splitLine("a\tb", "\t")).toEqual(["a", "b"]);
  });

  it("guesses a column's type", () => {
    expect(kindOf([])).toBe("text");
    expect(kindOf(["", null])).toBe("text");
    expect(kindOf([true, "False"])).toBe("boolean");
    expect(kindOf([1, "2.5", " -3 "])).toBe("number");
    expect(kindOf(["2026-01-02", "2026-01-03T10:00"])).toBe("date");
    expect(kindOf(["2026-01-02", "x"])).toBe("text");
    expect(kindOf([1, "x"])).toBe("text");
  });

  it("builds a table from lists or objects, with typed columns", () => {
    const { table } = parseTable({ title: "Sales", columns: ["name", { key: "n", label: "Count", type: "number" }, { label: "L" }, { key: "k", type: "bogus" }], rows: [["a", 1, "x", "2026-01-01"], { name: "b", n: 2 }, ["c"]] });
    expect(table!.title).toBe("Sales");
    expect(table!.columns.map((c) => [c.key, c.label, c.type])).toEqual([["name", "name", "text"], ["n", "Count", "number"], ["L", "L", "text"], ["k", "k", "date"]]);
    expect(table!.rows).toEqual([["a", 1, "x", "2026-01-01"], ["b", 2, null, null], ["c", null, null, null]]);
    const byObj = parseTable({ rows: [{ x: "1", y: true }, { x: "2", y: false }] }, "fallback").table!;
    expect(byObj.columns.map((c) => c.type)).toEqual(["number", "boolean"]);
    expect(byObj.title).toBe("fallback");
  });

  it("refuses what is not a table and cuts what is too big", () => {
    expect(parseTable(null).error).toMatch(/JSON object/);
    expect(parseTable([1]).error).toMatch(/JSON object/);
    expect(parseTable({ rows: "x" }).error).toMatch(/JSON object/);
    expect(parseTable({ rows: [[1]] }).error).toMatch(/give the columns/);
    expect(parseTable({ rows: [] }).error).toMatch(/give the columns/);
    expect(parseTable({ columns: ["a"], rows: [5] }).error).toMatch(/list or an object/);
    const big = parseTable({ columns: Array.from({ length: MAX_COLS + 5 }, (_, i) => String(i)), rows: Array.from({ length: MAX_ROWS + 5 }, () => [1]) }).table!;
    expect(big.columns).toHaveLength(MAX_COLS);
    expect(big.rows).toHaveLength(MAX_ROWS);
    expect(big.truncated).toBe(true);
    expect(parseTable({ columns: ["a"], rows: [["x".repeat(MAX_CELL + 10)]] }).table!.rows[0][0]).toHaveLength(MAX_CELL);
    expect(parseTable({ columns: [{ key: "k" }, 7], rows: [{ k: undefined }] }).table!.rows).toEqual([[null, null]]);
    expect(parseTable({ columns: [{ type: "number" }], rows: [[1]] }).table!.columns[0]).toMatchObject({ key: "0", label: "0", type: "number" });
  });

  it("reads CSV and TSV text", () => {
    const t = tableFromDelimited('name,total\n"Lee, A",3\nBo,4\n\n', ",")!;
    expect(t.rows).toEqual([["Lee, A", "3"], ["Bo", "4"]]);
    expect(t.columns[1].type).toBe("number");
    expect(tableFromDelimited("a\tb\n1\t2", "\t")!.rows).toEqual([["1", "2"]]);
    expect(tableFromDelimited("  \n", ",")).toBeNull();
    const many = tableFromDelimited(["h", ...Array.from({ length: MAX_ROWS + 3 }, () => "1")].join("\n"), ",")!;
    expect(many.rows).toHaveLength(MAX_ROWS);
    expect(many.truncated).toBe(true);
  });

  it("orders cells: numbers by value, text naturally, empty cells last", () => {
    expect(compareCells(2, 10, "number")).toBeLessThan(0);
    expect(compareCells("b2", "b10", "text")).toBeLessThan(0);
    expect(compareCells(null, "a", "text")).toBeGreaterThan(0);
    expect(compareCells("a", "", "text")).toBeLessThan(0);
    expect(compareCells(null, "", "text")).toBe(0);
    expect(cellText(null)).toBe("");
    expect(cellText(true)).toBe("true");
  });
});

describe("exports", () => {
  const c = cols("text", "number", "boolean");
  const r = [["=SUM(A1)", 3, true], ['say "hi", ok', null, false], ["+cmd", "-5", null]] as any;

  it("CSV quotes cells and defuses formulas in text cells", () => {
    expect(toCsv(c, r)).toBe('C0,C1,C2\r\n\'=SUM(A1),3,true\r\n"say ""hi"", ok",,false\r\n\'+cmd,-5,\r\n');
    expect(toCsv(cols("text"), [["multi\nline"]])).toBe('C0\r\n"multi\nline"\r\n');
  });

  it("JSON uses the column labels", () => {
    expect(JSON.parse(toJson(c, r))[1]).toEqual({ C0: 'say "hi", ok', C1: null, C2: false });
  });

  it("Markdown escapes pipes and aligns numbers right", () => {
    const md = toMarkdown(c, [["a|b\\c\nd", 1, true]] as any);
    expect(md.split("\n")[0]).toBe("| C0 | C1 | C2 |");
    expect(md.split("\n")[1]).toBe("| --- | ---: | --- |");
    expect(md.split("\n")[2]).toBe("| a\\|b\\\\c d | 1 | true |");
  });

  it("TSV flattens tabs and newlines", () => {
    expect(toTsv(cols("text", "text"), [["a\tb", "c\nd"], [null, "x"]] as any)).toBe("C0\tC1\na b\tc d\n\tx");
  });

  it("column letters run A, B ... Z, AA", () => {
    expect([0, 25, 26, 27, 701, 702].map(colLetter)).toEqual(["A", "Z", "AA", "AB", "ZZ", "AAA"]);
  });

  it("crc32 matches the known check value", () => {
    expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
  });

  it("builds a zip with a central directory and an end record", () => {
    const out = zip([{ name: "a.txt", data: new TextEncoder().encode("hello") }, { name: "b.txt", data: new Uint8Array() }]);
    const v = new DataView(out.buffer);
    expect(v.getUint32(0, true)).toBe(0x04034b50);
    expect(v.getUint32(out.length - 22, true)).toBe(0x06054b50);
    expect(v.getUint16(out.length - 22 + 10, true)).toBe(2);
    expect(new TextDecoder().decode(out)).toContain("hello");
  });

  it("builds an xlsx: strings are inline text, numbers and booleans are typed, XML is escaped", () => {
    const out = toXlsx(cols("text", "number", "boolean"), [["a<b>&\"\u0001", "7", true], [null, 3.5, false], ["", Number.NaN, null]] as any, "My: [sheet]/");
    const text = new TextDecoder().decode(out);
    for (const part of ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml"]) expect(text).toContain(part);
    expect(text).toContain('<sheet name="My   sheet"');
    expect(text).toContain("a&lt;b&gt;&amp;&quot;</t>");
    expect(text).toContain('<c r="B2"><v>7</v></c>');
    expect(text).toContain('<c r="B3"><v>3.5</v></c>');
    expect(text).toContain('<c r="C2" t="b"><v>1</v></c>');
    expect(text).toContain('<c r="C3" t="b"><v>0</v></c>');
    expect(text).toContain('<c r="B4" t="inlineStr">');          // NaN is not a number: kept as text
    expect(new DataView(out.buffer).getUint32(0, true)).toBe(0x04034b50);
    expect(new TextDecoder().decode(toXlsx(cols("text"), [], "///"))).toContain('<sheet name="Sheet1"');
  });

  it("file names are made safe", () => {
    expect(fileSafe("Q3 report: <draft>/v2", "x")).toBe("Q3 report draftv2");
    expect(fileSafe("???", "fallback")).toBe("fallback");
  });

  it("downloads through a temporary link", () => {
    vi.useFakeTimers();
    const create = vi.fn(() => "blob:x"), revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadFile("t.csv", "a,b", "text/csv");
    expect(create).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(document.querySelector("a[download]")).toBeNull();
    vi.advanceTimersByTime(1100);
    expect(revoke).toHaveBeenCalledWith("blob:x");
    vi.useRealTimers();
  });
});
