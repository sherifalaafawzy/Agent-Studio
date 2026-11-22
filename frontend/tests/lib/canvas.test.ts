import { describe, expect, it } from "vitest";
import { askMessage, authorText, canvasKindFor, fileExt, findRange, lineDiff } from "@/lib/canvas";

describe("canvas helpers", () => {
  it("says who made a version", () => {
    expect(authorText("user")).toBe("You");
    expect(authorText("restore")).toBe("A restore");
    expect(authorText("agent:writer")).toBe("Agent writer");
    expect(authorText("other")).toBe("other");
  });

  it("compares two texts line by line", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc\nd")).toEqual([{ kind: "same", text: "a" }, { kind: "del", text: "b" }, { kind: "add", text: "B" }, { kind: "same", text: "c" }, { kind: "add", text: "d" }]);
    expect(lineDiff("x", "x")).toEqual([{ kind: "same", text: "x" }]);
    expect(lineDiff("a\nb", "").map((l) => l.kind)).toEqual(["del", "del", "add"]);
    expect(lineDiff("", "a\nb").map((l) => l.kind)).toEqual(["del", "add", "add"]);
    expect(lineDiff("a\nb\nc", "b\nc").map((l) => l.kind)).toEqual(["del", "same", "same"]);
  });

  it("compares very long texts as whole blocks", () => {
    const big = Array.from({ length: 1600 }, (_, i) => `line ${i}`).join("\n");
    expect(lineDiff(big, big).every((l) => l.kind === "same")).toBe(true);
    const d = lineDiff(big, big + "!");
    expect(d.filter((l) => l.kind === "del")).toHaveLength(1600);
    expect(d.filter((l) => l.kind === "add")).toHaveLength(1600);
  });

  it("finds where an edit's old text sits, only when it is there exactly once", () => {
    expect(findRange("hello world", { old: "world" })).toEqual({ from: 6, to: 11 });
    expect(findRange("a a", { old: "a" })).toBeNull();
    expect(findRange("abc", { old: "zz" })).toBeNull();
    expect(findRange("abc", { old: "" })).toBeNull();
    expect(findRange("abc", {})).toBeNull();
    expect(findRange("abc", { at: "end" })).toBeNull();
  });

  it("writes the message sent to the chat for a selection", () => {
    const m = askMessage("Memo", "abc123", "  make this shorter ", "long text");
    expect(m).toContain('About the canvas "Memo" (id abc123): make this shorter');
    expect(m).toContain('"""\nlong text\n"""');
    expect(m).toContain("canvas_replace_selection");
  });

  it("picks a file extension", () => {
    expect([{ type: "markdown", language: "" }, { type: "table", language: "" }, { type: "html", language: "" }, { type: "text", language: "" }, { type: "code", language: "python" }, { type: "code", language: "" }, { type: "code", language: "bash" }]
      .map((c) => fileExt(c as any))).toEqual(["md", "csv", "html", "txt", "py", "txt", "sh"]);
  });

  it("opens a code block as the right kind of canvas", () => {
    expect(canvasKindFor("MD")).toEqual({ type: "markdown", language: "" });
    expect(canvasKindFor("tsv")).toEqual({ type: "table", language: "" });
    expect(canvasKindFor("html-preview")).toEqual({ type: "html", language: "" });
    expect(canvasKindFor("py")).toEqual({ type: "code", language: "python" });
    expect(canvasKindFor("tsx")).toEqual({ type: "code", language: "typescript" });
    expect(canvasKindFor("jsonview")).toEqual({ type: "code", language: "json" });
    expect(canvasKindFor("diff")).toEqual({ type: "code", language: "" });
    expect(canvasKindFor("brainfuck")).toEqual({ type: "text", language: "" });
    expect(canvasKindFor("")).toEqual({ type: "text", language: "" });
  });
});
