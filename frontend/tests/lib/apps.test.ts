import { describe, expect, it } from "vitest";
import { ago, authorText, buildTree, builderMessage, editorKind, fmtSize, nameFromPrompt, readAttachment } from "@/lib/apps";

describe("apps helpers", () => {
  it("writes sizes and times in words", () => {
    expect([fmtSize(5), fmtSize(2048), fmtSize(3 * 1024 * 1024)]).toEqual(["5 B", "2.0 KB", "3.0 MB"]);
    const now = 1_000_000;
    expect([ago(now - 5, now), ago(now - 120, now), ago(now - 7200, now), ago(now - 200000, now), ago(now + 50, now)]).toEqual(["just now", "2 min ago", "2 h ago", "2 d ago", "just now"]);
    expect([authorText("user"), authorText("restore"), authorText("agent:App Builder"), authorText("other")]).toEqual(["You", "A restore", "Agent App Builder", "other"]);
  });

  it("picks an editor for a file from its name", () => {
    expect(editorKind("README.md")).toEqual({ type: "markdown", language: "" });
    expect(editorKind("public/index.html").type).toBe("html");
    expect(editorKind("a.htm").type).toBe("html");
    expect(editorKind("server/main.py")).toEqual({ type: "code", language: "python" });
    expect(editorKind("public/app.js").language).toBe("javascript");
    expect(editorKind("x.json").language).toBe("json");
    expect(editorKind("Dockerfile")).toEqual({ type: "text", language: "" });
    expect(editorKind("notes.xyz")).toEqual({ type: "text", language: "" });
  });

  it("builds a tree of folders first, then files", () => {
    const t = buildTree([
      { path: "public/style.css", size: 3, binary: false }, { path: "app.json", size: 2, binary: false }, { path: "public/img/logo.png", size: 9, binary: true }, { path: "README.md", size: 1, binary: false },
    ]);
    expect(t.map((n) => n.name)).toEqual(["public", "app.json", "README.md"]);
    expect(t[0].dir && t[0].children.map((c) => c.name)).toEqual(["img", "style.css"]);
    expect(t[0].children[0].children[0]).toMatchObject({ name: "logo.png", path: "public/img/logo.png", dir: false, binary: true, size: 9 });
    expect(buildTree([])).toEqual([]);
  });

  it("makes the builder message, and a name from a prompt", () => {
    const app = { id: "abc", name: "My App" };
    expect(builderMessage(app, "  Make it blue ")).toBe('[App "My App" (app_id abc). Use the app_* tools with this app_id.]\nMake it blue');
    expect(builderMessage(app, "x", ["a.md", "b.csv"])).toContain("assets/ folder): a.md, b.csv");
    expect(nameFromPrompt("a page that replies to customer emails politely and quickly")).toBe("A page that replies to customer");
    expect(nameFromPrompt("   ")).toBe("New app");
    expect(nameFromPrompt("x".repeat(80)).length).toBeLessThanOrEqual(40);
  });

  it("reads attachments as text or base64", async () => {
    expect(await readAttachment(new File(["# hi"], "spec.md"))).toEqual({ name: "spec.md", text: "# hi" });
    const bin = await readAttachment(new File([new Uint8Array([137, 80, 78, 71])], "m.png"));
    expect(bin).toEqual({ name: "m.png", base64: btoa(String.fromCharCode(137, 80, 78, 71)) });
    const noExt = await readAttachment(new File([new Uint8Array([1])], "README"));
    expect(noExt.base64).toBe("AQ==");
  });
});

import { consoleReport, elementLabel, fixMessage, pickedMessage } from "@/lib/apps";

describe("builder messages for the smarter builder", () => {
  const app = { id: "abc", name: "My App" };
  const el = { selector: "#go", tag: "button", text: "Go now", html: "<button id=go>Go now</button>", label: "", page: "index.html", box: { x: 0, y: 0, w: 1, h: 1 }, style: { color: "red", fontSize: "16px" } };
  it("names a picked element and tells the builder what to change", () => {
    expect(elementLabel(el)).toBe('<button> "Go now"');
    expect(elementLabel({ ...el, text: "x".repeat(60) })).toBe(`<button> "${"x".repeat(28)}…"`);
    expect(elementLabel({ ...el, text: "  " })).toBe("<button>");
    const m = pickedMessage(app, el, "Make it bigger");
    expect(m).toContain('[App "My App" (app_id abc)');
    expect(m).toContain("Make it bigger");
    expect(m).toContain("selector `#go`");
    expect(m).toContain("color: red; fontSize: 16px");
    expect(m).toContain("Change only this element.");
  });
  it("turns a failed check into a fix request, and the preview's errors into a report", () => {
    const m = fixMessage(app, { report: "- error: boom", round: 2, max_rounds: 3 });
    expect(m).toContain("try 2 of 3");
    expect(m).toContain("- error: boom");
    expect(m).toContain("Do not rewrite whole files.");
    const entries = [{ kind: "error", text: "a", page: "", source: "app.js", line: 3, count: 1, at: 1 }, { kind: "console.warn", text: "w", page: "", source: "", line: 0, count: 1, at: 1 }, { kind: "resource", text: "r", page: "", source: "", line: 0, count: 3, at: 1 }];
    expect(consoleReport(entries)).toBe("- the preview reported (error, app.js:3): a\n- the preview reported (resource): r (x3)");
  });
});
