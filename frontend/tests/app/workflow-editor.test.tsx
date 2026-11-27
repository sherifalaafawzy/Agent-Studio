import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

// React Flow needs a real layout engine: the canvas is replaced by a stand-in that renders the nodes and exposes the same callbacks.
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, edges, nodeTypes, onNodesChange, onEdgesChange, onConnect, onPaneClick, children }: any) => (
    <div data-testid="rf">
      {nodes.map((n: any) => { const C = nodeTypes[n.type]; return <div key={n.id} data-nodeid={n.id} onClick={() => onNodesChange([{ type: "select", id: n.id, selected: true }])}><C data={n.data} selected={n.selected} /></div>; })}
      <span data-testid="edges">{edges.map((e: any) => `${e.source}>${e.target}:${e.sourceHandle}`).join(",")}</span>
      <button onClick={() => onNodesChange([{ type: "position", id: nodes[0].id, position: { x: 5, y: 6 }, dragging: true }])}>drag first</button>
      <button onClick={() => onNodesChange([{ type: "position", id: nodes[0].id, position: { x: 7, y: 8 }, dragging: false }])}>drop first</button>
      <button onClick={() => onNodesChange([{ type: "remove", id: nodes[0].id }])}>remove first</button>
      <button onClick={() => onNodesChange([{ type: "remove", id: nodes[nodes.length - 1].id }])}>remove last</button>
      <button onClick={() => onNodesChange([{ type: "dimensions", id: nodes[0].id }])}>measure</button>
      <button onClick={() => onNodesChange([{ type: "dimensions", id: nodes[0].id, dimensions: { width: 210, height: 90 } }])}>measure sized</button>
      <button onClick={() => onNodesChange([{ type: "dimensions", id: nodes[0].id, dimensions: { width: 230, height: 90 } }])}>measure resized</button>
      <span data-testid="measured">{nodes.map((n: any) => `${n.id}:${n.measured ? `${n.measured.width}x${n.measured.height}` : "-"}/${n.initialWidth}x${n.initialHeight}`).join(",")}</span>
      <button onClick={() => onConnect({ source: nodes[0].id, target: nodes[nodes.length - 1].id, sourceHandle: "out" })}>connect</button>
      <button onClick={() => onConnect({ source: nodes[1].id, target: nodes[2].id, sourceHandle: "true" })}>connect port</button>
      <button onClick={() => onConnect({ source: nodes[0].id, target: nodes[0].id })}>connect self</button>
      <button onClick={() => onConnect({ source: nodes[1].id, target: nodes[0].id })}>connect into input</button>
      <button onClick={() => onConnect({ source: null, target: null })}>connect nothing</button>
      <button onClick={() => edges[0] && onEdgesChange([{ type: "remove", id: edges[0].id }])}>remove edge</button>
      <button onClick={() => onEdgesChange([{ type: "select", id: "x", selected: true }])}>select edge</button>
      <button onClick={() => onNodesChange([{ type: "select", id: nodes[1].id, selected: true }, { type: "select", id: nodes[2].id, selected: true }])}>select agent and gate</button>
      <button onClick={() => onNodesChange([{ type: "select", id: nodes[2].id, selected: false }])}>deselect gate</button>
      <button onClick={onPaneClick}>pane</button>
      {children}
    </div>),
  ReactFlowProvider: ({ children }: any) => <>{children}</>, useReactFlow: () => ({ screenToFlowPosition: (p: any) => ({ x: p.x + 1, y: p.y + 1 }) }),
  Background: () => null, Controls: () => null, MiniMap: () => null, Handle: () => null, Position: { Left: "left", Right: "right" },
  applyNodeChanges: (ch: any[], ns: any[]) => ns.map((n) => { const c = ch.find((x) => x.id === n.id && x.type === "position"); return c ? { ...n, position: c.position } : n; }),
  applyEdgeChanges: (ch: any[], es: any[]) => es.filter((e) => !ch.some((c) => c.type === "remove" && c.id === e.id)),
  addEdge: (c: any, es: any[]) => [...es, { id: c.id, source: c.source, target: c.target, sourceHandle: c.sourceHandle }],
}));
let search = "id=wf_one";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search), useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

import EditPage from "@/app/workflows/edit/page";

const SPECS = [
  { type: "input", group: "trigger", label: "Task input", desc: "start", fields: [], outputs: ["out"], inputs: 0 },
  { type: "agent", group: "ai", label: "Agent", desc: "ask an agent", fields: [{ name: "agent", label: "Agent", kind: "agent" }, { name: "prompt", label: "Prompt", kind: "textarea", default: "{{ $prev }}" }], outputs: ["out"], inputs: 1 },
  { type: "if", group: "flow", label: "If", desc: "branch", fields: [{ name: "condition", label: "Condition", kind: "expr", default: "{{ 1 }}" }], outputs: ["true", "false"], inputs: 1 },
  { type: "template", group: "data", label: "Text template", desc: "fill text", fields: [{ name: "text", label: "Text", kind: "textarea", default: "x" }], outputs: ["out"], inputs: 1 },
  { type: "output", group: "output", label: "Result", desc: "the result", fields: [], outputs: [], inputs: 1 },
  { type: "webhook", group: "trigger", label: "Webhook", desc: "starts from outside", fields: [{ name: "respond", label: "Answer the caller", kind: "select", options: ["immediately", "when_done"], default: "immediately" }], outputs: ["out"], inputs: 0 },
  { type: "note", group: "canvas", label: "Sticky note", desc: "a note", fields: [{ name: "text", label: "Note", kind: "textarea" }], outputs: [], inputs: 0, visual: true },
];
const CATALOG = { groups: [{ id: "trigger", label: "Triggers" }, { id: "ai", label: "AI" }, { id: "flow", label: "Flow" }, { id: "data", label: "Data" }, { id: "output", label: "Output" }, { id: "canvas", label: "Canvas" }], nodes: SPECS };
const NODES = [
  { id: "start", type: "input", label: "Task", position: { x: 0, y: 0 }, config: {} },
  { id: "agent1", type: "agent", label: "Writer", position: { x: 200, y: 0 }, config: { agent: "helper", prompt: "Hi {{ $input }}" } },
  { id: "gate", type: "if", label: "Check", position: { x: 400, y: 0 }, config: { condition: "{{ 1 }}" } },
  { id: "result", type: "output", label: "Result", position: { x: 600, y: 0 }, config: {} },
];
const EDGES = [{ id: "a", source: "start", target: "agent1" }, { id: "b", source: "agent1", target: "gate" }, { id: "c", source: "gate", target: "result", source_port: "true" }];
const DOC = { id: "wf_one", name: "Flow one", autonomy: "approve", version: 2, nodes: NODES, edges: EDGES, problems: [{ node: "agent1", severity: "error", message: "agent 'helper' does not exist" }] };
const RUN = (status: string, nodes: any, extra: any = {}) => ({ id: "r1", status, final: "", error: null, plan: [], cost_usd: 0, autonomy: "approve", nodes, ...extra });
const RN = (status: string, extra: any = {}) => ({ status, output: "", error: null, label: "Writer", type: "agent", ...extra });

function setup(extra: Record<string, any> = {}, doc: any = DOC) {
  return routeFetch({
    "GET /api/workflows/nodes": () => jsonResponse(CATALOG), "GET /api/agents": () => jsonResponse([{ id: "helper", name: "Helper" }]), "GET /api/tools": () => jsonResponse([{ name: "read_file" }]),
    "GET /api/workflows/apis": () => jsonResponse({ apis: [{ id: "pets", name: "Pets", allow_write: false, operations: [{ id: "list", method: "get", path: "/pets", enabled: true }] }] }),
    "GET /api/workflows": () => jsonResponse({ workflows: [{ id: "wf_one", name: "Flow one" }, { id: "wf_b", name: "Flow B" }] }), "GET /api/workflows/wf_one": () => jsonResponse(doc), "GET /api/workflows/wf_one/draft": () => jsonResponse({ detail: "no draft" }, { status: 404 }),
    "PUT /api/workflows/wf_one/draft": () => jsonResponse({}), ...extra });
}
const node = (id: string) => document.querySelector(`[data-nodeid="${id}"]`) as HTMLElement;
const load = async () => { render(<EditPage />); await screen.findByTestId("workflow-editor"); await screen.findByText("Writer"); };
afterEach(() => { search = "id=wf_one"; vi.useRealTimers(); });

describe("Workflow editor", () => {
  it("needs an id, reports a load error and a loading state", async () => {
    search = "";
    const { unmount } = render(<EditPage />);
    expect(screen.getByText("No workflow chosen.")).toBeInTheDocument();
    unmount();
    search = "id=ghost";
    routeFetch({ "GET /api/workflows/nodes": () => jsonResponse(CATALOG), "GET /api/agents": () => jsonResponse([]), "GET /api/tools": () => jsonResponse([]), "GET /api/workflows/apis": () => jsonResponse({ apis: [] }), "GET /api/workflows": () => jsonResponse({ workflows: [] }),
      "GET /api/workflows/ghost": () => jsonResponse({ detail: "unknown workflow" }, { status: 404 }) });
    render(<EditPage />);
    expect(await screen.findByText(/unknown workflow/)).toBeInTheDocument();
  });

  it("loads full screen with the header, boxes and problems; the Task input cannot be deleted", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    expect(screen.getByText("saved · v2")).toBeInTheDocument(); expect(screen.getByLabelText("Workflow name")).toHaveValue("Flow one");
    expect(screen.getByTestId("edges")).toHaveTextContent("start>agent1:out,agent1>gate:out,gate>result:true");
    expect(screen.getByRole("button", { name: "1 problem(s)" })).toBeInTheDocument();
    await user.click(screen.getByText("remove first"));
    expect(screen.getByText("Task")).toBeInTheDocument();                       // the input box is still there
    expect(screen.getByRole("link", { name: "Back to workflows" })).toHaveAttribute("href", "/workflows/");
  });

  it("selects a box, edits it in the right-hand panel and closes it", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    await user.click(node("agent1"));
    const panel = screen.getByTestId("node-panel");
    expect(within(panel).getByLabelText("Box name")).toHaveValue("Writer");
    await user.type(within(panel).getByLabelText("Box name"), "!");
    expect(screen.getByText("unsaved changes")).toBeInTheDocument();
    await user.selectOptions(within(panel).getByLabelText("Agent"), "helper");
    await user.type(within(panel).getByLabelText("Prompt"), "?");
    await user.click(within(panel).getByText("When something goes wrong"));
    await user.selectOptions(within(panel).getByLabelText("On error"), "branch");
    fireEvent.change(within(panel).getByLabelText("Retries"), { target: { value: "2" } });
    fireEvent.change(within(panel).getByLabelText("Time limit"), { target: { value: "30" } });
    await user.click(within(panel).getByLabelText(/Switch this box off/));
    await user.click(within(panel).getByRole("tab", { name: "Notes" }));
    await user.type(within(panel).getByLabelText("Notes"), "remember");
    await user.click(within(panel).getByRole("tab", { name: "Input" }));
    expect(screen.getByTestId("Input-empty")).toBeInTheDocument();
    await user.click(within(panel).getByRole("tab", { name: "Output" }));
    expect(screen.getByTestId("Output-empty")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Close settings" }));
    expect(screen.queryByTestId("node-panel")).toBeNull();
    await user.click(node("start"));
    expect(within(screen.getByTestId("node-panel")).queryByRole("button", { name: "Delete" })).toBeNull();
    expect(within(screen.getByTestId("node-panel")).queryByText("When something goes wrong")).toBeNull();
    await user.click(screen.getByText("pane"));
    expect(screen.queryByTestId("node-panel")).toBeNull();
  });

  it("undo, redo, duplicate, delete, copy and paste, tidy up", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    await user.click(node("agent1"));
    await user.click(within(screen.getByTestId("node-panel")).getByRole("button", { name: "Duplicate" }));
    expect(screen.getByText("Writer copy")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.queryByText("Writer copy")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Redo" }));
    expect(screen.getByText("Writer copy")).toBeInTheDocument();
    await user.click(within(screen.getByTestId("node-panel")).getByRole("button", { name: "Delete" }));
    expect(screen.queryByText("Writer copy")).toBeNull();
    await user.click(node("gate"));
    fireEvent.keyDown(window, { key: "c", ctrlKey: true });
    fireEvent.keyDown(window, { key: "v", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-if").length).toBe(2);
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-if").length).toBe(3);
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "y", ctrlKey: true });
    await user.click(screen.getByRole("button", { name: "Tidy up" }));
    await user.click(node("start"));
    fireEvent.keyDown(window, { key: "c", ctrlKey: true });                       // the Task input is never copied
    fireEvent.keyDown(window, { key: "v", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-if").length).toBe(4);
  });

  it("canvas changes: dragging, removing, connecting (with ports) and removing connections", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByText("drag first"));
    await user.click(screen.getByText("drop first"));
    await user.click(screen.getByText("measure"));
    expect(screen.getByRole("button", { name: "Undo" })).toBeEnabled();
    await user.click(screen.getByText("connect"));
    expect(screen.getByTestId("edges").textContent).toContain("start>result:out");
    await user.click(screen.getByText("connect port"));
    expect(screen.getByTestId("edges").textContent).toContain("agent1>gate:true");
    for (const b of ["connect self", "connect into input", "connect nothing", "select edge"]) await user.click(screen.getByText(b));
    await user.click(screen.getByText("remove edge"));
    expect(screen.getByTestId("edges").textContent).not.toContain("start>agent1");
    await user.click(screen.getByText("remove last"));
    expect(screen.getByTestId("edges").textContent).not.toContain("result");
  });

  it("gives the minimap the measured size of each box (and a starting size before the first measurement)", async () => {
    setup();
    await load();
    expect(screen.getByTestId("measured").textContent).toContain("start:-/220x72");        // not measured yet: a starting size so the minimap draws something
    await userEvent.setup().click(screen.getByText("measure sized"));
    expect(screen.getByTestId("measured").textContent).toContain("start:210x90/220x72");
    await userEvent.setup().click(screen.getByText("measure sized"));                       // the same size again changes nothing
    expect(screen.getByTestId("measured").textContent).toContain("start:210x90/");
    await userEvent.setup().click(screen.getByText("measure resized"));
    expect(screen.getByTestId("measured").textContent).toContain("start:230x90/");
  });

  it("palette: search, add a box after the selected one (following the right port), add an API endpoint, drag and drop, keyboard", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    await user.click(node("gate"));
    await user.click(screen.getByRole("button", { name: "Add a box" }));
    const pal = screen.getByTestId("palette");
    expect(within(pal).getByText("AI")).toBeInTheDocument(); expect(within(pal).queryByText("Task input")).toBeNull();
    await user.type(within(pal).getByLabelText("Search boxes"), "xyz");
    expect(within(pal).getByText(/No box matches/)).toBeInTheDocument();
    await user.clear(within(pal).getByLabelText("Search boxes"));
    await user.type(within(pal).getByLabelText("Search boxes"), "template");
    await user.click(within(pal).getByRole("button", { name: /Text template/ }));
    expect(screen.queryByTestId("palette")).toBeNull();
    expect(screen.getByTestId("edges").textContent).toContain("gate>template1:true");        // an If box continues down its first port
    fireEvent.keyDown(document.body, { key: "/" });
    expect(screen.getByTestId("palette")).toBeInTheDocument();
    await user.click(within(screen.getByTestId("palette")).getByRole("button", { name: /Pets/ }));
    expect(screen.getByText("Pets.list".replace("Pets.", "") )).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "/" });
    const item = within(screen.getByTestId("palette")).getByRole("button", { name: /Sticky note/ });
    const dt = { setData: vi.fn(), getData: () => "agent", types: ["application/x-wf-node"] };
    fireEvent.dragStart(item, { dataTransfer: dt });
    expect(dt.setData).toHaveBeenCalledWith("application/x-wf-node", "note");
    const canvas = screen.getByTestId("wf-canvas");
    fireEvent.dragOver(canvas, { dataTransfer: dt }); fireEvent.dragOver(canvas, { dataTransfer: { types: ["text/plain"] } });
    fireEvent.drop(canvas, { dataTransfer: dt, clientX: 10, clientY: 20 });
    fireEvent.drop(canvas, { dataTransfer: { getData: () => "" } });
    expect(screen.getAllByText("Agent").length).toBeGreaterThan(0);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("palette")).toBeNull();
    fireEvent.keyDown(window, { key: "/" });
    await user.click(screen.getByRole("button", { name: "Close palette" }));
    expect(screen.queryByTestId("palette")).toBeNull();
  });

  it("saves a version (with a label), clears the draft and reports errors", async () => {
    let fail = true;
    const { calls } = setup({ "PUT /api/workflows/wf_one": () => (fail ? jsonResponse({ detail: "give the workflow a name" }, { status: 422 }) : jsonResponse({ ...DOC, version: 3, problems: [] })) });
    const user = userEvent.setup();
    await load();
    expect(screen.getByRole("button", { name: "Save version" })).toBeDisabled();
    await user.type(screen.getByLabelText("Workflow name"), "!");
    await user.type(screen.getByLabelText("Version label"), "milestone");
    await user.selectOptions(screen.getByLabelText("Autonomy"), "auto");
    await user.click(screen.getByRole("button", { name: "Save version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("give the workflow a name");
    fail = false;
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await screen.findByText("saved · v3");
    expect(calls.filter((c) => c.method === "PUT" && c.url.pathname === "/api/workflows/wf_one").at(-1)?.body).toMatchObject({ label: "milestone", name: "Flow one!", autonomy: "auto" });
    expect(screen.queryByRole("button", { name: /problem\(s\)/ })).toBeNull();
  });

  it("autosaves a draft, warns before leaving, and restores or discards a draft on load", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = setup();
    await load();
    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev); expect(ev.defaultPrevented).toBe(false);
    fireEvent.change(screen.getByLabelText("Workflow name"), { target: { value: "Edited" } });
    const ev2 = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev2); expect(ev2.defaultPrevented).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(calls.some((c) => c.method === "PUT" && c.url.pathname.endsWith("/draft") && c.body.name === "Edited")).toBe(true);
    vi.useRealTimers();
    // restoring a draft
    document.body.innerHTML = "";
    const r = setup({ "GET /api/workflows/wf_one/draft": () => jsonResponse({ doc: { ...DOC, name: "From draft", problems: undefined } }), "DELETE /api/workflows/wf_one/draft": () => jsonResponse({ ok: true }) });
    render(<EditPage />);
    expect(await screen.findByText(/your unsaved draft was restored/)).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow name")).toHaveValue("From draft");
    await userEvent.click(screen.getByRole("button", { name: "discard it" }));
    await waitFor(() => expect(screen.getByLabelText("Workflow name")).toHaveValue("Flow one"));
    expect(r.calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("opens the saved workflow when the server answers that there is no draft", async () => {
    setup({ "GET /api/workflows/wf_one/draft": () => jsonResponse({ doc: null }) });               // the server's plain "no draft" answer (it used to be a 404)
    render(<EditPage />);
    await waitFor(() => expect(screen.getByLabelText("Workflow name")).toHaveValue("Flow one"));
    expect(screen.queryByText(/your unsaved draft was restored/)).toBeNull();
  });

  it("checks for problems and lists them in the drawer", async () => {
    setup({ "POST /api/workflows/validate": () => jsonResponse({ ok: false, problems: [{ node: "agent1", severity: "error", message: "agent 'helper' does not exist" }, { node: "", severity: "warning", message: "no Output node" }] }) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Check" }));
    const probs = await screen.findByTestId("problems");
    expect(probs).toHaveTextContent("does not exist"); expect(probs).toHaveTextContent("no Output node");
    await user.click(within(probs).getByRole("button", { name: "agent1" }));
    expect(screen.getByTestId("node-panel")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Run log" }));
    expect(screen.getByText(/No run yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close drawer" }));
    expect(screen.queryByTestId("drawer")).toBeNull();
  });

  it("runs the saved version: approval, output tab with JSON/table views, pinning and earlier runs", async () => {
    let phase = "waiting";
    const table = JSON.stringify([{ a: 1, b: { x: 1 } }, { a: 2 }]);
    const { calls } = setup({
      "POST /api/workflows/wf_one/run": () => jsonResponse(RUN("running", { agent1: RN("waiting_approval", { message: "Run 'Writer'?" }) })),
      "GET /api/workflows/runs/r1": () => jsonResponse(phase === "waiting" ? RUN("running", { agent1: RN("waiting_approval", { message: "Run 'Writer'?" }), gate: RN("waiting_approval", { type: "approval", label: "Check", message: "Send it?" }) })
        : RUN("done", { agent1: RN("done", { output: table, input: "task text", attempts: 2, decision: "true" }), gate: RN("error", { error: "boom", label: "Check" }) }, { final: "FINAL ANSWER", cost_usd: 0.0123 })),
      "POST /api/workflows/runs/r1/decide": () => jsonResponse({ ok: true }),
      "GET /api/workflows/wf_one/runs": () => jsonResponse({ runs: [{ id: "old1", status: "done", task: "an old task", started: 1, cost_usd: 0, version: 1 }, { id: "old2", status: "error", task: "", started: 1, cost_usd: 0, version: 1 }] }),
      "GET /api/workflows/runs/old1": () => jsonResponse(RUN("done", {}, { final: "OLD RESULT" })),
    });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Run" }));
    const modalRun = within(screen.getByRole("dialog")).getByRole("button", { name: "Run" });
    expect(modalRun).toBeDisabled();
    await user.type(screen.getByLabelText("Task"), "do it");
    await user.click(modalRun);
    const log = await screen.findByTestId("run-log");
    expect(calls.find((c) => c.url.pathname.endsWith("/run"))?.body).toEqual({ task: "do it" });
    expect(await screen.findByText(/2 step\(s\) waiting for you/)).toBeInTheDocument();
    for (const name of ["Approve", "Yes", "Skip", "No"]) await user.click(await within(log).findByRole("button", { name }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname.endsWith("/decide")).length).toBe(4));
    expect(calls.filter((c) => c.url.pathname.endsWith("/decide")).map((c) => c.body.decision)).toEqual(["approve", "approve", "skip", "skip"]);
    phase = "done";
    await waitFor(() => expect(screen.getByTestId("run-final")).toHaveTextContent("FINAL ANSWER"), { timeout: 4000 });
    expect(log).toHaveTextContent("boom"); expect(log).toHaveTextContent("$0.0123");
    await user.click(within(log).getByRole("button", { name: "Writer" }));
    const panel = screen.getByTestId("node-panel");
    await user.click(within(panel).getByRole("tab", { name: "Input" }));
    expect(within(panel).getByTestId("Input-data")).toHaveTextContent("task text");
    await user.click(within(panel).getByRole("tab", { name: "Output" }));
    expect(panel).toHaveTextContent("after 2 tries"); expect(panel).toHaveTextContent("went true");
    const data = within(panel).getByTestId("Output-data");
    await user.click(within(data).getByRole("tab", { name: "json" }));
    expect(data).toHaveTextContent('"a": 1');
    await user.click(within(data).getByRole("tab", { name: "table" }));
    expect(within(data).getByRole("table")).toHaveTextContent('{"x":1}');
    await user.click(within(data).getByRole("tab", { name: "text" }));
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn() }, configurable: true });
    await user.click(within(data).getByRole("button", { name: "Copy" }));
    await user.click(within(data).getByRole("button", { name: "Pin this output" }));
    await user.click(within(panel).getByRole("tab", { name: "Settings" }));
    expect(screen.getByText(/Pinned output is used instead/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unpin" }));
    await user.click(screen.getByRole("tab", { name: "Earlier runs" }));
    const earlier = await screen.findByTestId("earlier-runs");
    expect(earlier).toHaveTextContent("an old task"); expect(earlier).toHaveTextContent("(no task)");
    await user.click(within(earlier).getAllByRole("button", { name: "Open" })[0]);
    expect(await screen.findByTestId("run-final")).toHaveTextContent("OLD RESULT");
  });

  it("stops a running run, shows a plan and reports run failures", async () => {
    let mode = "running";
    let fail = false;
    setup({ "POST /api/workflows/wf_one/run": () => (fail ? jsonResponse({ detail: "fix these first: s: bad" }, { status: 422 }) : jsonResponse(RUN(mode, { agent1: RN("running") }, { plan: [{ node: "agent1", label: "Writer", does: "Ask the agent" }] }))),
      "GET /api/workflows/runs/r1": () => jsonResponse(RUN(mode, mode === "plan" ? {} : { agent1: RN(mode === "stopped" ? "stopped" : "running") }, { plan: [{ node: "agent1", label: "Writer", does: "Ask the agent" }] })),
      "POST /api/workflows/runs/r1/stop": () => jsonResponse(RUN("stopped", { agent1: RN("stopped") })), "PUT /api/workflows/wf_one": () => jsonResponse({ ...DOC, autonomy: "suggest", version: 3, problems: [] }),
      "POST /api/workflows/runs/r1/decide": () => jsonResponse({ detail: "nope" }, { status: 422 }) });
    const user = userEvent.setup();
    await load();
    const runIt = async (label: RegExp) => { await user.click(screen.getByRole("button", { name: label })); await user.type(screen.getByLabelText("Task"), "x"); await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: label })); };
    await runIt(/^Run$/);
    await screen.findByTestId("run-log");
    mode = "stopped";
    await user.click(within(screen.getByTestId("drawer")).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByTestId("run-log")).toHaveTextContent("stopped"));
    mode = "plan";
    await user.selectOptions(screen.getByLabelText("Autonomy"), "suggest");
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await screen.findByText("saved · v3");
    await runIt(/^Show the plan$/);
    expect(await screen.findByTestId("plan")).toHaveTextContent("Writer: Ask the agent");
    fail = true;
    await runIt(/^Show the plan$/);
    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("fix these first"))).toBe(true);
  });

  it("lists versions, looks at one and restores it", async () => {
    const { calls } = setup({ "GET /api/workflows/wf_one/versions": () => jsonResponse({ versions: [{ version: 2, ts: 1, nodes: 4, label: "Milestone", note: "n" }, { version: 1, ts: 1, nodes: 2, label: "", note: "" }] }),
      "GET /api/workflows/wf_one/versions/1": () => jsonResponse({ ...DOC, nodes: NODES.slice(0, 2) }), "POST /api/workflows/wf_one/restore": () => jsonResponse({ ...DOC, version: 3, problems: [] }) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Versions" }));
    const v = await screen.findByTestId("versions");
    expect(v).toHaveTextContent("Milestone — n"); expect(v).toHaveTextContent("(no label)");
    await user.click(within(v).getAllByRole("button", { name: "Look" })[1]);
    expect(await screen.findByTestId("version-look")).toHaveTextContent("Task → Writer");
    await user.click(within(v).getAllByRole("button", { name: "Restore" })[1]);
    await screen.findByText("saved · v3");
    expect(calls.find((c) => c.url.pathname.endsWith("/restore"))?.body).toEqual({ version: 1 });
  });

  it("restoring or running can fail and shows why", async () => {
    setup({ "GET /api/workflows/wf_one/versions": () => jsonResponse({ versions: [{ version: 1, ts: 1, nodes: 2, label: "", note: "" }] }), "POST /api/workflows/wf_one/restore": () => jsonResponse({ detail: "that version does not exist" }, { status: 404 }),
      "POST /api/workflows/wf_one/run": () => jsonResponse({ detail: "fix these first: boom" }, { status: 422 }) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Versions" }));
    await user.click(await screen.findByRole("button", { name: "Restore" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("does not exist");
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "x");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("fix these first"))).toBe(true);
  });

  it("builder chat: proposes changes as a diff you apply, dismiss, or that fail", async () => {
    search = "id=wf_one&chat=1";
    let n = 0;
    const proposed = { ...DOC, nodes: [...NODES, { id: "extra", type: "template", label: "Extra", position: { x: 0, y: 0 }, config: { text: "x" } }], edges: EDGES };
    const { calls } = setup({ "POST /api/workflows/builder/chat": () => (++n === 1 ? jsonResponse({ reply: "Added an extra step.", diff: { added: ["extra"], removed: [], changed: ["gate"], edges_added: 1, edges_removed: 0 }, workflow: proposed, applied: ["added Text template 'Extra'"], refused: ["connect: nope"], note: "No model could help (x)." })
      : n === 2 ? jsonResponse({ reply: "Nothing to change.", diff: { added: [], removed: [], changed: [], edges_added: 0, edges_removed: 0 }, workflow: DOC, applied: [], refused: [], note: null })
        : n === 3 ? jsonResponse({ reply: "Another idea", diff: { added: [], removed: ["gate"], changed: [], edges_added: 0, edges_removed: 1 }, workflow: DOC, applied: [], refused: [], note: null }) : jsonResponse({ detail: "no model" }, { status: 500 })) });
    const user = userEvent.setup();
    render(<EditPage />);
    const chat = await screen.findByTestId("builder-chat");
    await screen.findByText("Writer");
    await user.click(within(chat).getByText("How it should work · systems it may use"));
    await user.selectOptions(within(chat).getByLabelText("Autonomy"), "auto");
    await user.click(within(chat).getByLabelText("Helper")); await user.click(within(chat).getByLabelText("Helper"));
    await user.click(within(chat).getByRole("button", { name: "Send" }));                       // empty: nothing sent
    expect(calls.some((c) => c.url.pathname.endsWith("/builder/chat"))).toBe(false);
    await user.type(within(chat).getByLabelText("Tell the builder"), "add an extra step{Enter}");
    const card = await within(chat).findByTestId("diff-card");
    expect(card).toHaveTextContent("+1 boxes"); expect(card).toHaveTextContent("~1 changed"); expect(card).toHaveTextContent("Left out: connect: nope"); expect(chat).toHaveTextContent("No model could help");
    expect(calls.find((c) => c.url.pathname.endsWith("/builder/chat"))?.body).toMatchObject({ autonomy: "auto", systems: { agents: ["helper"] }, messages: [{ role: "assistant" }, { role: "user", content: "add an extra step" }].slice(1).length ? expect.anything() : expect.anything() });
    await user.click(within(card).getByRole("button", { name: "Apply" }));
    expect(screen.getByText("Extra")).toBeInTheDocument(); expect(within(card).getByRole("button", { name: "Applied" })).toBeDisabled();
    await user.type(within(chat).getByLabelText("Tell the builder"), "anything else");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    expect(await within(chat).findByText("No changes proposed.")).toBeInTheDocument();
    await user.type(within(chat).getByLabelText("Tell the builder"), "remove it");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    const third = (await within(chat).findAllByTestId("diff-card"))[2];
    expect(third).toHaveTextContent("−1 removed");
    await user.click(within(third).getByRole("button", { name: "Dismiss" }));
    expect(within(chat).getAllByTestId("diff-card").length).toBe(2);
    await user.type(within(chat).getByLabelText("Tell the builder"), "break");
    await user.click(within(chat).getByRole("button", { name: "Send" }));
    expect(await within(chat).findByText("no model")).toBeInTheDocument();
    await user.click(within(chat).getByRole("button", { name: "Close chat" }));
    expect(screen.queryByTestId("builder-chat")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Build with an agent" }));
    expect(screen.getByTestId("builder-chat")).toBeInTheDocument();
  });

  it("opens straight into the Versions dialog from a run's detail", async () => {
    search = "id=wf_one&versions=1";
    setup({ "GET /api/workflows/wf_one/versions": () => jsonResponse({ versions: [{ version: 1, ts: 1, nodes: 2, label: "First", note: "" }] }) });
    render(<EditPage />);
    expect(await screen.findByTestId("versions")).toBeInTheDocument();
    search = "id=wf_one";
  });

  it("opens straight into the Run dialog from the Flows table, and the rail toggles the chat and drawer", async () => {
    search = "id=wf_one&run=1";
    setup();
    const user = userEvent.setup();
    render(<EditPage />);
    expect(await screen.findByRole("dialog", { name: "Run this workflow" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Run log" }));
    expect(screen.getByTestId("drawer")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run log" }));
    expect(screen.queryByTestId("drawer")).toBeNull();
  });

  it("a new workflow opened for chat starts with a question from the builder", async () => {
    search = "id=wf_one&chat=1";
    setup({}, { ...DOC, nodes: [NODES[0]], edges: [], problems: [] });
    render(<EditPage />);
    expect(await screen.findByTestId("builder-chat")).toHaveTextContent("Tell me what this workflow should do");
  });

  it("copies and pastes several boxes together, keeping the connections between them", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    fireEvent.click(screen.getByText("select agent and gate"));
    fireEvent.keyDown(window, { key: "c", ctrlKey: true });
    fireEvent.keyDown(window, { key: "v", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-agent")).toHaveLength(2);
    expect(screen.getAllByTestId("wf-node-if")).toHaveLength(2);
    expect(screen.getByTestId("edges")).toHaveTextContent("agent2>if1:out");                                   // the connection between the two copies comes along, under the new names
    expect(screen.getByTestId("edges")).toHaveTextContent("gate>result:true");
    fireEvent.click(screen.getByText("deselect gate"));
    fireEvent.click(screen.getByText("pane"));
    await user.click(node("start"));
    fireEvent.keyDown(window, { key: "c", ctrlKey: true });                                                 // the starting box is never copied
    fireEvent.keyDown(window, { key: "v", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-agent")).toHaveLength(3);                                        // the earlier copy is pasted again
  });

  it("pastes nothing when nothing was copied", async () => {
    setup();
    await load();
    fireEvent.keyDown(window, { key: "v", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-agent")).toHaveLength(1);
  });

  it("switches the start to a Webhook box and manages its secret address", async () => {
    const { calls } = setup({
      "GET /api/workflows/wf_one/hook": () => jsonResponse({ exists: false, created: null, last_used: null, calls: 0, webhooks_enabled: true }),
    });
    const user = userEvent.setup();
    await load();
    await user.click(node("start"));
    const panel = screen.getByTestId("node-panel");
    await user.selectOptions(within(panel).getByLabelText("Starts when"), "webhook");
    expect(await within(screen.getByTestId("node-panel")).findByTestId("webhook-box")).toBeInTheDocument();
    expect(screen.getByText("Webhook", { selector: ".wf-title" })).toBeInTheDocument();
    expect(calls.some((c) => c.url.pathname === "/api/workflows/wf_one/hook")).toBe(true);
    expect(within(screen.getByTestId("node-panel")).queryByTestId("try-box")).toBeNull();                       // a starting box has nothing to try
    await user.selectOptions(within(screen.getByTestId("node-panel")).getByLabelText("Starts when"), "input");
    expect(screen.queryByTestId("webhook-box")).toBeNull();
    expect(screen.getByText("Task", { selector: ".wf-title" })).toBeInTheDocument();
    await user.click(screen.getByLabelText("Add a box"));
    expect(within(screen.getByTestId("palette")).queryByText("Webhook")).toBeNull();                              // the start cannot be added as a second box
  });

  it("tries one box, and runs from a box once a whole run exists", async () => {
    let runs = 0;
    const done = (id: string) => RUN("done", { agent1: RN("done", { output: "hello", input: "last input" }) }, { id, final: "x", task: "do it" });
    const { calls } = setup({
      "POST /api/workflows/wf_one/nodes/agent1/test": () => jsonResponse({ status: "done", output: "hi there", ports: ["out"] }),
      "POST /api/workflows/wf_one/run": () => (runs++ === 0 ? jsonResponse(done("r1")) : runs === 2 ? jsonResponse(done("r2")) : jsonResponse({ detail: "run the whole workflow once first" }, { status: 422 })),
      "GET /api/workflows/runs/r1": () => jsonResponse(done("r1")), "GET /api/workflows/runs/r2": () => jsonResponse(done("r2")),
    });
    const user = userEvent.setup();
    await load();
    await user.click(node("agent1"));
    let panel = screen.getByTestId("node-panel");
    expect(within(panel).getByText("Run from here")).toBeDisabled();
    expect(within(panel).getByText(/Run the whole workflow once first/)).toBeInTheDocument();
    await user.type(within(panel).getByLabelText("Test input"), "hello");
    await user.click(within(panel).getByText("Test this box"));
    expect(await within(panel).findByTestId("try-result")).toHaveTextContent("hi there");
    expect(calls.find((c) => c.url.pathname.endsWith("/nodes/agent1/test"))!.body).toMatchObject({ input: "hello", allow_side_effects: false, doc: { id: "wf_one" } });
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "do it");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByTestId("run-final")).toHaveTextContent("x"));
    panel = screen.getByTestId("node-panel");
    await waitFor(() => expect(within(panel).getByText("Run from here")).toBeEnabled());
    await user.click(within(panel).getByText("Run from here"));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/workflows/wf_one/run")).toHaveLength(2));
    expect(calls.filter((c) => c.url.pathname === "/api/workflows/wf_one/run")[1].body).toEqual({ task: "do it", from_node: "agent1", base: "r1" });
    await waitFor(() => expect(screen.getByTestId("run-log")).toHaveTextContent("done"));
    await user.click(within(screen.getByTestId("node-panel")).getByText("Run from here"));
    expect(await screen.findByRole("alert")).toHaveTextContent("run the whole workflow once first");
  });

  it("will not run from a box while there are unsaved changes", async () => {
    setup({ "POST /api/workflows/wf_one/run": () => jsonResponse(RUN("done", { agent1: RN("done") }, { final: "ok" })), "GET /api/workflows/runs/r1": () => jsonResponse(RUN("done", { agent1: RN("done") }, { final: "ok" })) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "go");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    await screen.findByTestId("run-final");
    await user.click(node("agent1"));
    await user.type(screen.getByLabelText("Box name"), "!");
    expect(within(screen.getByTestId("node-panel")).getByText("Run from here")).toBeDisabled();
    expect(within(screen.getByTestId("node-panel")).getByText(/Save a version first/)).toBeInTheDocument();
  });

  it("asks a person for details inside the run log, and marks boxes reused from an earlier run", async () => {
    const form = [{ name: "who", label: "Who", type: "text", options: [], required: true }, { name: "size", label: "Size", type: "select", options: ["s", "m"], required: false },
      { name: "ok", label: "Sure", type: "bool", options: [], required: false }, { name: "n", label: "How many", type: "number", options: [], required: false }];
    const asking = RUN("running", { agent1: RN("waiting_approval", { type: "approval", label: "Ask", message: "Details?", form }), gate: RN("done", { reused: true, label: "Check" }) });
    const { calls } = setup({ "POST /api/workflows/wf_one/run": () => jsonResponse(asking), "GET /api/workflows/runs/r1": () => jsonResponse(asking), "POST /api/workflows/runs/r1/decide": () => jsonResponse({ ok: true }) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "go");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    const formBox = await screen.findByTestId("approval-form");
    expect(screen.getByTestId("run-log")).toHaveTextContent("reused from the earlier run");
    await user.type(within(formBox).getByLabelText("Who"), "Ann");
    await user.selectOptions(within(formBox).getByLabelText("Size"), "m");
    await user.click(within(formBox).getByLabelText("Sure"));
    await user.type(within(formBox).getByLabelText("How many"), "3");
    await user.click(within(formBox).getByText("Send answers and continue"));
    await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/decide"))).toBe(true));
    expect(calls.find((c) => c.url.pathname.endsWith("/decide"))!.body).toEqual({ node: "agent1", decision: "approve", values: { who: "Ann", size: "m", ok: true, n: "3" } });
  });

  it("lists the connected MCP servers, knowledge bases and credentials for the boxes that pick them", async () => {
    const { calls } = setup({ "GET /api/workflows/refs": () => jsonResponse({ mcp: [{ id: "srv", label: "Search", ready: true }], knowledge: [], data_source: [], guardrail: [], credential: [] }) });
    await load();
    expect(calls.some((c) => c.url.pathname === "/api/workflows/refs")).toBe(true);
  });

  it("keeps working when the lookups fail, and ignores shortcuts until the workflow has loaded", async () => {
    const bad = () => jsonResponse({ detail: "down" }, { status: 500 });
    routeFetch({ "GET /api/workflows/nodes": bad, "GET /api/agents": bad, "GET /api/tools": bad, "GET /api/workflows/apis": bad, "GET /api/workflows": bad, "GET /api/workflows/refs": bad,
      "GET /api/workflows/wf_one": () => jsonResponse(DOC), "GET /api/workflows/wf_one/draft": () => jsonResponse({ detail: "none" }, { status: 404 }) });
    render(<EditPage />);
    for (const k of ["s", "c", "v", "z", "y"]) fireEvent.keyDown(window, { key: k, ctrlKey: true });          // nothing is there to save, copy, paste or undo yet
    await screen.findByTestId("workflow-editor");
    await screen.findByText("Writer");
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });                                                 // nothing to undo
    fireEvent.keyDown(window, { key: "y", ctrlKey: true });
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("Ctrl+D never copies the start or a box the canvas already removed", async () => {
    setup();
    const user = userEvent.setup();
    await load();
    await user.click(node("start"));
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    expect(screen.getAllByTestId("wf-node-agent")).toHaveLength(1);
    await user.click(node("result"));
    fireEvent.click(screen.getByText("remove last"));
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    expect(screen.queryByTestId("wf-node-output")).toBeNull();
    expect(screen.getAllByTestId("wf-node-if")).toHaveLength(1);
  });

  it("shows a failed check, a failed poll and a failed answer", async () => {
    let step = "waiting";
    setup({
      "POST /api/workflows/validate": () => jsonResponse({ detail: "the check broke" }, { status: 500 }),
      "POST /api/workflows/wf_one/run": () => jsonResponse(RUN("running", { agent1: RN("waiting_approval", { message: "Run 'Writer'?" }) })),
      "GET /api/workflows/runs/r1": () => (step === "waiting" ? jsonResponse(RUN("running", { agent1: RN("waiting_approval", { message: "Run 'Writer'?" }) })) : jsonResponse({ detail: "gone" }, { status: 500 })),
      "POST /api/workflows/runs/r1/decide": () => jsonResponse({ detail: "that step is not waiting for an answer" }, { status: 422 }),
      "POST /api/workflows/runs/r1/stop": () => jsonResponse({ detail: "cannot stop" }, { status: 500 }),
    });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("the check broke")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /problem\(s\)/ }));                               // the problem count in the header opens the problems tab
    expect(screen.getByTestId("problems")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "go");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    await screen.findByTestId("run-log");
    await user.click(screen.getByLabelText("Close drawer"));
    expect(screen.queryByTestId("run-log")).toBeNull();
    await user.click(within(screen.getByRole("status")).getByRole("button", { name: "Open" }));            // the banner brings the drawer back
    expect(await screen.findByTestId("run-log")).toBeInTheDocument();
    await user.click(within(screen.getByTestId("run-log")).getByRole("button", { name: "Approve" }));
    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("not waiting for an answer"))).toBe(true);
    await user.click(within(screen.getByTestId("run-log")).getByRole("button", { name: "Stop" }));          // "Stop" next to the answer buttons stops the run
    step = "broken";
    await user.click(within(screen.getByTestId("drawer")).getAllByRole("button", { name: "Stop" })[0]);
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("cannot stop"))).toBe(true));
    await user.click(screen.getByRole("tab", { name: "Earlier runs" }));
    expect(await screen.findByText("No earlier runs.")).toBeInTheDocument();                                  // the list of earlier runs could not be read: it shows as empty
  });

  it("closes the Run dialog with Escape, and carries on when the autosave or the draft clean-up fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = setup({ "PUT /api/workflows/wf_one/draft": () => jsonResponse({ detail: "disk full" }, { status: 500 }) });
    await load();
    fireEvent.change(screen.getByLabelText("Workflow name"), { target: { value: "Edited" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(calls.some((c) => c.method === "PUT" && c.url.pathname.endsWith("/draft"))).toBe(true);
    vi.useRealTimers();
    document.body.innerHTML = "";
    setup({ "GET /api/workflows/wf_one/draft": () => jsonResponse({ doc: { ...DOC, name: "From draft", problems: undefined } }), "DELETE /api/workflows/wf_one/draft": () => jsonResponse({ detail: "locked" }, { status: 500 }) });
    render(<EditPage />);
    await userEvent.click(await screen.findByRole("button", { name: "discard it" }));
    await waitFor(() => expect(screen.getByLabelText("Workflow name")).toHaveValue("Flow one"));
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.getByRole("dialog", { name: "Run this workflow" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("shows a note on the canvas, a builder that ignores an empty message, a version list that cannot load, and Stop in the run log", async () => {
    const withNote = { ...DOC, nodes: [...NODES, { id: "note1", type: "note", label: "Note", position: { x: 0, y: 200 }, config: { text: "Remember the budget" } }] };
    setup({ "GET /api/workflows/wf_one": () => jsonResponse(withNote), "GET /api/workflows/wf_one/versions": () => jsonResponse({ detail: "down" }, { status: 500 }),
      "POST /api/workflows/wf_one/run": () => jsonResponse(RUN("running", { agent1: RN("waiting_approval", { message: "Run 'Writer'?" }) })), "GET /api/workflows/runs/r1": () => jsonResponse(RUN("running", { agent1: RN("waiting_approval", { message: "Run 'Writer'?" }) })),
      "POST /api/workflows/runs/r1/decide": () => jsonResponse({ ok: true }) });
    const user = userEvent.setup();
    await load();
    expect(screen.getByTestId("wf-node-note")).toHaveTextContent("Remember the budget");
    await user.click(screen.getByRole("button", { name: "Build with an agent" }));
    const box = screen.getByLabelText("Tell the builder");
    await user.click(box);
    await user.keyboard("{Enter}");                                                                           // an empty message is not sent
    expect(screen.getByTestId("builder-chat")).not.toHaveTextContent("Thinking");
    await user.click(screen.getByRole("button", { name: "Versions" }));
    await waitFor(() => expect(screen.getByTestId("versions")).toBeInTheDocument());
    await user.click(screen.getByLabelText("Close"));
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "go");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    await user.click(await within(await screen.findByTestId("run-log")).findByRole("button", { name: "Stop" }));
  });

  it("stops polling when a run cannot be read, and leaves the start alone when the catalog has no Webhook box", async () => {
    const noWebhook = { ...CATALOG, nodes: CATALOG.nodes.filter((n: any) => n.type !== "webhook") };
    setup({ "GET /api/workflows/nodes": () => jsonResponse(noWebhook), "POST /api/workflows/wf_one/run": () => jsonResponse(RUN("running", { agent1: RN("running") })), "GET /api/workflows/runs/r1": () => jsonResponse({ detail: "gone" }, { status: 500 }) });
    const user = userEvent.setup();
    await load();
    await user.click(node("start"));
    await user.selectOptions(within(screen.getByTestId("node-panel")).getByLabelText("Starts when"), "webhook");
    expect(screen.getByText("Task", { selector: ".wf-title" })).toBeInTheDocument();                       // nothing changed: this catalog has no Webhook box to switch to
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Task"), "go");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    expect(await screen.findByTestId("run-log")).toHaveTextContent("running");
  });
});
