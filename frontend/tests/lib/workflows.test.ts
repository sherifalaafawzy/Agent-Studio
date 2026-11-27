import { describe, expect, it, vi } from "vitest";
import { ago, blankDoc, defaultConfig, ENTRY, NO_REFS, portsOf, ROOTS, RULE_OPS, slug, summarise, tidy, uniqueId, type NodeSpec, type WNode } from "@/lib/workflows";

const n = (id: string, type: string, config: any = {}, settings?: any): WNode => ({ id, type, label: id, position: { x: 0, y: 0 }, config, settings });
const spec = (type: string, outputs: string[] | string, fields: any[] = []): NodeSpec => ({ type, group: "flow", label: type, desc: "", fields, outputs, inputs: 1 });

describe("workflow helpers", () => {
  it("slugs, blank docs, ids and relative times", () => {
    expect(slug("  My 1st Flow!! ")).toBe("my_1st_flow");
    expect(slug("9 lives")).toBe("lives");
    expect(blankDoc("Hello World")).toMatchObject({ id: "hello_world", name: "Hello World", autonomy: "approve", nodes: [{ id: "start", type: "input" }] });
    expect(blankDoc("123").id).toBe("workflow");
    expect(uniqueId([n("agent1", "agent"), n("agent2", "agent")], "agent")).toBe("agent3");
    vi.setSystemTime(new Date("2026-01-10T12:00:00Z"));
    const t = Date.now() / 1000;
    expect([ago(null), ago(undefined), ago(t - 5), ago(t - 300), ago(t - 7200), ago(t - 3 * 86400)]).toEqual(["never", "never", "just now", "5 min ago", "2 h ago", "3 d ago"]);
    vi.useRealTimers();
  });

  it("works out output ports", () => {
    expect(portsOf(n("a", "agent"), spec("agent", ["out"]))).toEqual(["out"]);
    expect(portsOf(n("a", "agent", {}, { on_error: "branch" }), spec("agent", ["out"]))).toEqual(["out", "error"]);
    expect(portsOf(n("c", "classifier", { labels: ["x", "y"] }), spec("classifier", "labels"))).toEqual(["x", "y", "other"]);
    expect(portsOf(n("s", "switch", { cases: ["a"] }), spec("switch", "cases"))).toEqual(["a", "other"]);
    expect(portsOf(n("c", "classifier"), spec("classifier", "labels"))).toEqual(["other"]);
    expect(portsOf(n("o", "output", {}, { on_error: "branch" }), spec("output", []))).toEqual([]);
    expect(portsOf(n("z", "mystery"))).toEqual(["out"]);
  });

  it("builds default configs from the catalog", () => {
    const fields = [{ name: "t", label: "t", kind: "text", default: "hi" }, { name: "s", label: "s", kind: "steps" }, { name: "j", label: "j", kind: "json" }, { name: "p", label: "p", kind: "pairs" },
      { name: "l", label: "l", kind: "lines" }, { name: "b", label: "b", kind: "bool" }, { name: "x", label: "x", kind: "text" }];
    expect(defaultConfig(spec("custom", ["out"], fields))).toEqual({ t: "hi", s: [], j: {}, p: [{ name: "field", value: "" }], l: [], b: false });
    expect(defaultConfig(spec("agent", ["out"]))).toEqual({ agent: "", prompt: "{{ $prev }}" });
    expect(defaultConfig(spec("tool", ["out"]))).toEqual({ tool: "", args: {}, confirmed: false });
    expect(defaultConfig(spec("openapi", ["out"]))).toEqual({ source: "", operation: "", args: {}, confirmed: false });
  });

  it("tidies into layers, leaving notes alone, and survives loops", () => {
    const nodes = [n("a", "input"), n("b", "agent"), n("c", "agent"), n("d", "output"), n("note", "note")];
    const edges = [{ source: "a", target: "b" }, { source: "a", target: "c" }, { source: "b", target: "d" }, { source: "c", target: "d" }];
    const out = tidy(nodes, edges);
    const pos = Object.fromEntries(out.map((x) => [x.id, x.position]));
    expect(pos.a).toEqual({ x: 60, y: 60 }); expect(pos.b.x).toBe(350); expect(pos.c).toEqual({ x: 350, y: 210 }); expect(pos.d.x).toBe(640); expect(pos.note).toEqual({ x: 0, y: 0 });
    expect(tidy([n("x", "agent"), n("y", "agent")], [{ source: "x", target: "y" }, { source: "y", target: "x" }]).length).toBe(2);
  });

  it("summarises every kind of box", () => {
    const cases: [WNode, string][] = [
      [n("a", "agent", { agent: "helper" }), "agent: helper"], [n("a", "agent"), "choose an agent"], [n("t", "tool", { tool: "read_file" }), "tool: read_file"], [n("t", "tool"), "choose a tool"],
      [n("o", "openapi", { source: "s", operation: "op" }), "s.op"], [n("o", "openapi"), "choose an API endpoint"], [n("l", "loop", { steps: [1], max_iterations: 4 }), "1 step(s), up to 4×"],
      [n("l", "loop", {}), "0 step(s), up to 3×"], [n("p", "parallel", { steps: [1, 2] }), "2 step(s)"], [n("m", "llm", { prompt: "Hello there" }), "Hello there"], [n("m", "llm"), "write a prompt"],
      [n("c", "classifier", { labels: ["a", "b"] }), "a / b"], [n("c", "classifier"), "add labels"], [n("i", "if", { condition: "{{ $x > 1 }}" }), "$x > 1"], [n("i", "if"), "set a condition"],
      [n("s", "switch", { cases: ["x"] }), "x"], [n("s", "switch"), "add cases"], [n("w", "wait", { seconds: 5 }), "5 s"], [n("w", "wait"), "0 s"], [n("u", "sub_workflow", { workflow: "w" }), "workflow: w"],
      [n("u", "for_each"), "choose a workflow"], [n("e", "set", { fields: [1] }), "1 field(s)"], [n("e", "set"), "0 field(s)"], [n("a", "approval", { message: "ok?" }), "ok?"], [n("a", "approval"), "ask a person"],
      [n("no", "note", { text: "memo" }), "memo"], [n("in", "input"), "the task comes in here"], [n("out", "output"), "the result goes out here"], [n("pj", "parse_json"), "parse json"],
    ];
    for (const [node, text] of cases) expect(summarise(node)).toBe(text);
  });

  it("gives every newer box a one-line summary, and sensible defaults for the new kinds of field", () => {
    const sum = (type: string, config: any = {}) => summarise(n("x", type, config));
    expect([
      sum("compare", { conditions: [{}, {}], combine: "or" }), sum("compare"), sum("filter", { rules: [{ field: "n", op: "gt" }, { field: "", op: "equals" }] }), sum("filter"),
      sum("http_request", { method: "POST", url: "https://api.test/items" }), sum("http_request"), sum("mcp_tool", { server: "s", tool: "t" }), sum("mcp_tool"),
      sum("memory", { operation: "save", kind: "procedural" }), sum("memory"), sum("knowledge", { kb: "docs" }), sum("knowledge"), sum("data_query", { source: "db", sql: "select 1" }), sum("data_query"),
      sum("artifact", { name: "r.md" }), sum("artifact"), sum("guardrail", { stage: "output" }), sum("guardrail"), sum("sort", { field: "n", order: "desc" }), sum("sort"), sum("limit", { count: 3, from: "end" }), sum("limit"),
      sum("dedupe", { field: "id" }), sum("dedupe"), sum("aggregate", { op: "sum", field: "n", group_by: "g" }), sum("aggregate"), sum("split_out", { mode: "separator" }), sum("split_out"), sum("join", { separator: "-" }), sum("join"),
      sum("merge_by_key", { key: "id", mode: "left" }), sum("merge_by_key"), sum("date_time", { operation: "diff_days" }), sum("date_time"), sum("regex", { mode: "all", pattern: "a+" }), sum("regex"),
      sum("webhook", { respond: "when_done" }), sum("webhook"), sum("respond_webhook", { status: 201 }), sum("respond_webhook"), sum("error_handler"), sum("never_heard_of_it"),
      sum("template", { text: "got\n  {{ $input }}" }), sum("template"), sum("stop", { message: "Stopped" }), sum("stop"),
      sum("schedule"), sum("schedule", { mode: "daily" }), sum("schedule", { mode: "daily", at: "07:30" }), sum("schedule", { mode: "cron", cron: "0 9 * * 1" }), sum("schedule", { mode: "cron" }), sum("schedule", { every_minutes: 5 }), sum("notify", { title: "Hi" }), sum("notify"),
    ]).toEqual([
      "2 condition(s), any must hold", "0 condition(s), all must hold", "keep items where n gt, item equals", "keep items where …", "POST https://api.test/items", "GET set an address", "s.t", "choose a server and tool",
      "save procedural memory", "search semantic memory", "search docs", "choose a knowledge base", "db: select 1", "choose a data source", "keep as r.md", "keep as file", "check the output", "check the input",
      "by n ↓", "by the item ↑", "last 3", "first 10", "by id", "whole items", "sum of n per g", "count", "by separator", "by lines", 'join with "-"', 'join with ""',
      "on id (left)", "on a key (inner)", "diff days", "now", "all /a+/", "first //", "starts when called · answers when done", "starts when called · answers immediately", "answers with status 201", "answers with status 200",
      "runs only if something fails", "never heard of it",
      "got {{ $input }}", "write the text", "Stopped", "stop with a message",
      "every 60 min", "every day at 09:00", "every day at 07:30", "cron 0 9 * * 1", "cron ", "every 5 min", "notify: Hi", "notify: ",
    ]);
    const field = (name: string, kind: string) => ({ name, label: name, kind });
    const fields = [field("r", "rules"), field("c", "conditions"), field("f", "formfields")];
    expect(defaultConfig(spec("x", ["out"], fields))).toEqual({ r: [{ field: "", op: "equals", value: "" }], c: [{ left: "", op: "equals", right: "" }], f: [] });
    expect(ENTRY).toEqual(["input", "webhook", "schedule"]);
    expect(ROOTS).toEqual(["input", "webhook", "schedule", "error_handler"]);
    expect(NO_REFS.mcp).toEqual([]);
    expect(RULE_OPS.find(([v]) => v === "gte")![1]).toBe("is at least");
  });
});
