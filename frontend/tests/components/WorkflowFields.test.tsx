import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Fields, { type Ctx } from "@/components/workflows/Fields";
import type { NodeSpec, WNode } from "@/lib/workflows";

const CTX: Ctx = {
  agents: [{ id: "helper", name: "Helper" }], tools: [{ name: "read_file" }, { name: "write_file" }],
  srcs: [{ id: "pets", name: "Pets", allow_write: false, operations: [{ id: "list", method: "get", path: "/pets", enabled: true }, { id: "add", method: "post", path: "/pets", enabled: true }, { id: "off", method: "get", path: "/off", enabled: false }] }],
  workflows: [{ id: "wf_b", name: "Flow B" }],
};
const spec = (type: string, fields: any[]): NodeSpec => ({ type, group: "flow", label: type, desc: "does things", fields, outputs: ["out"], inputs: 1 });
const F = (name: string, kind: string, extra: any = {}) => ({ name, label: name.toUpperCase(), kind, ...extra });
const node = (type: string, config: any): WNode => ({ id: "n", type, label: "n", position: { x: 0, y: 0 }, config });

function setup(s: NodeSpec, n: WNode) {
  const onChange = vi.fn();
  render(<Fields spec={s} node={n} ctx={CTX} onChange={onChange} />);
  return onChange;
}

describe("Fields (schema-driven settings form)", () => {
  it("shows the description of a box with nothing to set", () => {
    setup(spec("input", []), node("input", {}));
    expect(screen.getByText("does things")).toBeInTheDocument();
  });

  it("edits text, textarea, expression, number, select and bool fields", async () => {
    const user = userEvent.setup();
    const on = setup(spec("x", [F("t", "text"), F("a", "textarea"), F("e", "expr", { help: "true when yes" }), F("n", "number", { min: 0, max: 9 }), F("s", "select", { options: ["one", "two"] }), F("b", "bool")]), node("x", { t: "", a: "", e: "", n: 1, s: "one", b: false }));
    await user.type(screen.getByLabelText("T"), "h");
    expect(on).toHaveBeenLastCalledWith({ t: "h", a: "", e: "", n: 1, s: "one", b: false });
    await user.type(screen.getByLabelText("A"), "x"); await user.type(screen.getByLabelText("E"), "y");
    fireEvent.change(screen.getByLabelText("N"), { target: { value: "5" } });
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ n: 5 }));
    await user.selectOptions(screen.getByLabelText("S"), "two");
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ s: "two" }));
    await user.click(screen.getByLabelText("B"));
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ b: true }));
    expect(screen.getByText("true when yes")).toBeInTheDocument();
  });

  it("chooses agents, tools, APIs, endpoints and workflows; resets the endpoint when the API changes", async () => {
    const user = userEvent.setup();
    const on = setup(spec("x", [F("agent", "agent"), F("tool", "tool"), F("source", "source"), F("operation", "operation"), F("workflow", "workflow")]), node("x", { agent: "", tool: "", source: "pets", operation: "list", workflow: "" }));
    await user.selectOptions(screen.getByLabelText("AGENT"), "helper");
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ agent: "helper" }));
    await user.selectOptions(screen.getByLabelText("TOOL"), "write_file");
    await user.selectOptions(screen.getByLabelText("SOURCE"), "pets");
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ source: "pets", operation: "" }));
    expect(screen.getByText(/write_file \(changes things\)/)).toBeInTheDocument();
    expect(screen.queryByText(/\/off/)).toBeNull();
    await user.selectOptions(screen.getByLabelText("OPERATION"), "add");
    await user.selectOptions(screen.getByLabelText("WORKFLOW"), "wf_b");
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ workflow: "wf_b" }));
  });

  it("edits lines, JSON and name/value pairs", async () => {
    const user = userEvent.setup();
    const on = setup(spec("x", [F("l", "lines"), F("j", "json"), F("p", "pairs", { help: "any {{ }}" })]), node("x", { l: ["a", "b"], j: { k: 1 }, p: [{ name: "n1", value: "v1" }] }));
    const lines = screen.getByLabelText("L") as HTMLTextAreaElement;
    expect(lines.value).toBe("a\nb");
    await user.type(lines, "{Enter}c{Enter}");                          // a new empty line can be typed: the raw text is kept while the list ignores blanks
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ l: ["a", "b", "c"] }));
    expect(lines.value).toBe("a\nb\nc\n");
    fireEvent.change(screen.getByLabelText("J"), { target: { value: '{"k": 2}' } });
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ j: { k: 2 } }));
    fireEvent.change(screen.getByLabelText("J"), { target: { value: "[1]" } });
    expect(screen.getByText("Not valid JSON yet")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("J"), { target: { value: "{bad" } });
    fireEvent.change(screen.getByLabelText("J"), { target: { value: "" } });
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ j: {} }));
    await user.type(screen.getByLabelText("Field 1 name"), "!"); await user.type(screen.getByLabelText("Field 1 value"), "?");
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ p: [{ name: "n1", value: "v1?" }] }));
    await user.click(screen.getByRole("button", { name: "Add a field" }));
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ p: [{ name: "n1", value: "v1" }, { name: "", value: "" }] }));
    await user.click(screen.getByRole("button", { name: "Remove field 1" }));
    expect(on).toHaveBeenLastCalledWith(expect.objectContaining({ p: [] }));
  });

  it("edits the steps of a container: agent, tool and API steps", async () => {
    const user = userEvent.setup();
    const on = setup(spec("sequential", [F("steps", "steps")]), node("sequential", { steps: [{ kind: "agent", agent: "", prompt: "p" }, { kind: "tool", tool: "write_file", args: {}, confirmed: false }, { kind: "openapi", source: "pets", operation: "add", args: {}, confirmed: false }] }));
    await user.selectOptions(screen.getAllByLabelText("Agent")[0], "helper");
    expect(on).toHaveBeenLastCalledWith({ steps: [expect.objectContaining({ agent: "helper" }), expect.anything(), expect.anything()] });
    await user.type(screen.getByLabelText("Prompt"), "!");
    await user.selectOptions(screen.getAllByLabelText("Tool")[0], "read_file");
    fireEvent.change(screen.getAllByLabelText("Arguments")[0], { target: { value: '{"path": "x"}' } });
    await user.click(screen.getAllByLabelText(/I confirm this tool/)[0]);
    await user.selectOptions(screen.getByLabelText("API"), "pets");
    expect(on).toHaveBeenLastCalledWith({ steps: [expect.anything(), expect.anything(), expect.objectContaining({ source: "pets", operation: "" })] });
    await user.selectOptions(screen.getByLabelText("Endpoint"), "list");
    await user.click(screen.getByLabelText(/I confirm this call/).closest("label") ? screen.getByLabelText(/I confirm this call/) : screen.getByLabelText(/I confirm this call/));
    expect(screen.getByText(/writes are also off for this API/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Step 1 kind"), "tool");
    expect(on).toHaveBeenLastCalledWith({ steps: [{ kind: "tool" }, expect.anything(), expect.anything()] });
    await user.click(screen.getByRole("button", { name: "Remove step 2" }));
    expect(on).toHaveBeenLastCalledWith({ steps: [expect.anything(), expect.anything()] });
    await user.click(screen.getByRole("button", { name: "Add a step" }));
    expect(on).toHaveBeenLastCalledWith({ steps: [expect.anything(), expect.anything(), expect.anything(), { kind: "agent", agent: "", prompt: "{{ $prev }}" }] });
  });

  it("tools and API calls ask for confirmation only when they change things", async () => {
    const user = userEvent.setup();
    const on = setup(spec("tool", [F("tool", "tool"), F("confirmed", "bool")]), node("tool", { tool: "read_file", confirmed: false }));
    expect(screen.getByText(/not needed for a read-only tool/)).toBeInTheDocument();
    await user.click(screen.getByLabelText("CONFIRMED"));
    expect(on).toHaveBeenLastCalledWith({ tool: "read_file", confirmed: true });
  });

  it("falls back to a text input for an unknown kind", async () => {
    const user = userEvent.setup();
    const on = setup(spec("x", [F("z", "mystery")]), node("x", { z: "a" }));
    await user.type(screen.getByLabelText("Z"), "b");
    expect(on).toHaveBeenLastCalledWith({ z: "ab" });
  });

  it("picks an MCP server, knowledge base, data source or credential from what exists", async () => {
    const user = userEvent.setup();
    const refs = { mcp: [{ id: "srv", label: "Search server", ready: true }, { id: "off", label: "Old server", ready: false }], knowledge: [{ id: "kb", label: "Docs" }], data_source: [], guardrail: [], credential: [] };
    const onChange = vi.fn();
    render(<Fields spec={spec("mcp_tool", [F("server", "ref", { ref: "mcp", required: true, help: "which one" }), F("kb", "ref", { ref: "knowledge" }), F("other", "ref", { ref: "data_source" }), F("lost", "ref")])} node={node("mcp_tool", { server: "", kb: "", other: "", lost: "" })} ctx={{ ...CTX, refs }} onChange={onChange} />);
    expect(screen.getByText("Old server (not ready)")).toBeInTheDocument();
    expect(screen.getByLabelText("SERVER")).toHaveDisplayValue("choose…");
    expect(screen.getByLabelText("KB")).toHaveDisplayValue("none");
    await user.selectOptions(screen.getByLabelText("SERVER"), "srv");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ server: "srv" }));
    await user.selectOptions(screen.getByLabelText("KB"), "kb");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ kb: "kb" }));
    expect(screen.getByLabelText("OTHER").querySelectorAll("option")).toHaveLength(1);
    expect(screen.getByLabelText("LOST").querySelectorAll("option")).toHaveLength(1);
    expect(screen.getByText("which one")).toBeInTheDocument();
  });

  it("edits the rules of a Filter", async () => {
    const user = userEvent.setup();
    const on = setup(spec("filter", [F("rules", "rules")]), node("filter", { rules: [{ field: "n", op: "gte", value: "2" }, { field: "", op: "is_empty", value: "" }] }));
    expect(screen.queryByLabelText("Rule 2 value")).not.toBeInTheDocument();                         // "is empty" needs no value
    await user.type(screen.getByLabelText("Rule 1 field"), "x");
    expect(on).toHaveBeenLastCalledWith({ rules: [{ field: "nx", op: "gte", value: "2" }, { field: "", op: "is_empty", value: "" }] });
    await user.selectOptions(screen.getByLabelText("Rule 1 comparison"), "contains");
    expect(on).toHaveBeenLastCalledWith({ rules: [{ field: "n", op: "contains", value: "2" }, expect.anything()] });
    await user.type(screen.getByLabelText("Rule 1 value"), "5");
    expect(on).toHaveBeenLastCalledWith({ rules: [{ field: "n", op: "gte", value: "25" }, expect.anything()] });
    await user.click(screen.getByText("Add a rule"));
    expect(on).toHaveBeenLastCalledWith({ rules: [expect.anything(), expect.anything(), { field: "", op: "equals", value: "" }] });
    await user.click(screen.getByLabelText("Remove rule 2"));
    expect(on).toHaveBeenLastCalledWith({ rules: [{ field: "n", op: "gte", value: "2" }] });
  });

  it("edits conditions with a left and a right side", async () => {
    const user = userEvent.setup();
    const on = setup(spec("compare", [F("conditions", "conditions")]), node("compare", { conditions: [{ left: "{{ $prev }}", op: "equals", right: "yes" }] }));
    await user.type(screen.getByLabelText("Rule 1 right"), "!");
    expect(on).toHaveBeenLastCalledWith({ conditions: [{ left: "{{ $prev }}", op: "equals", right: "yes!" }] });
    await user.click(screen.getByText("Add a rule"));
    expect(on).toHaveBeenLastCalledWith({ conditions: [expect.anything(), { left: "", op: "equals", right: "" }] });
  });

  it("edits the details an approval asks for", async () => {
    const user = userEvent.setup();
    const form = [{ name: "who", label: "Who", type: "text", options: [], required: true }, { name: "size", label: "", type: "select", options: ["s", "m"], required: false }];
    const on = setup(spec("approval", [F("form", "formfields")]), node("approval", { form }));
    await user.type(screen.getByLabelText("Detail 1 name"), "2");
    expect(on).toHaveBeenLastCalledWith({ form: [{ ...form[0], name: "who2" }, form[1]] });
    await user.type(screen.getByLabelText("Detail 1 question"), "?");
    expect(on).toHaveBeenLastCalledWith({ form: [{ ...form[0], label: "Who?" }, form[1]] });
    await user.selectOptions(screen.getByLabelText("Detail 1 kind"), "number");
    expect(on).toHaveBeenLastCalledWith({ form: [{ ...form[0], type: "number" }, form[1]] });
    await user.type(screen.getByLabelText("Detail 2 options"), ", l");
    expect(on).toHaveBeenLastCalledWith({ form: [form[0], { ...form[1], options: ["s", "m", "l"] }] });
    await user.click(screen.getAllByLabelText("needed")[1]);
    expect(on).toHaveBeenLastCalledWith({ form: [form[0], { ...form[1], required: true }] });
    await user.click(screen.getByText("Add a detail"));
    expect(on).toHaveBeenLastCalledWith({ form: [form[0], form[1], { name: "", label: "", type: "text", options: [], required: false }] });
    await user.click(screen.getByLabelText("Remove detail 2"));
    expect(on).toHaveBeenLastCalledWith({ form: [form[0]] });
  });

  it("edits the steps of a container: an API step has its own arguments", async () => {
    const user = userEvent.setup();
    const steps = [{ kind: "openapi", source: "pets", operation: "list", args: {}, confirmed: false }];
    const on = setup(spec("parallel", [F("steps", "steps")]), node("parallel", { steps }));
    fireEvent.change(screen.getByLabelText("Arguments"), { target: { value: '{"limit": 5}' } });
    expect(on).toHaveBeenLastCalledWith({ steps: [{ ...steps[0], args: { limit: 5 } }] });
    await user.selectOptions(screen.getByLabelText("Step 1 kind"), "tool");
    expect(on).toHaveBeenLastCalledWith({ steps: [{ kind: "tool" }] });
  });
});
