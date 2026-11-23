import { render, screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

// React Flow needs a real layout engine: the canvas is replaced by a stand-in that renders the boxes and reports how it was configured.
vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ nodes, nodeTypes, onNodesChange, deleteKeyCode, nodesConnectable, children }: any) => (
    <div data-testid="rf" data-delete={String(deleteKeyCode)} data-connectable={String(nodesConnectable)}>
      {nodes.map((n: any) => { const C = nodeTypes[n.type]; return <div key={n.id} data-nodeid={n.id} data-draggable={String(n.draggable)} onClick={() => onNodesChange([{ type: "select", id: n.id, selected: true }])}><C data={n.data} selected={n.selected} /></div>; })}
      <button onClick={() => onNodesChange([{ type: "position", id: nodes[0].id, position: { x: 1, y: 1 }, dragging: false }])}>nudge</button>
      {children}
    </div>),
  ReactFlowProvider: ({ children }: any) => <>{children}</>, useReactFlow: () => ({ screenToFlowPosition: (p: any) => p }),
  Background: () => null, Controls: () => null, MiniMap: () => null, Handle: () => null, Position: { Left: "left", Right: "right" },
  applyNodeChanges: (_c: any[], ns: any[]) => ns, applyEdgeChanges: (_c: any[], es: any[]) => es, addEdge: (_c: any, es: any[]) => es,
}));
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }), useSearchParams: () => new URLSearchParams("") }));

import RunDetail from "@/components/workflows/RunDetail";

const NOW = Date.now() / 1000;
const NODE = (label: string, type: string, status: string, over: Record<string, unknown> = {}) => ({ status, label, type, output: "", error: null, input: "", started: NOW - 100, ended: NOW - 99, attempts: 1, ports: [], ...over });
const RUN = (over: Record<string, unknown> = {}) => ({
  id: "r1", workflow: "wf", name: "Flow wf", status: "error", final: "", error: "boom", plan: [], autonomy: "auto", started: NOW - 100, ended: NOW - 90, cost_usd: 0.0123, tokens: 1500, version: 3, origin: "webhook",
  task: "payload [secret removed]", counts: { total: 5, done: 2, error: 1, skipped: 1, running: 0, waiting: 0, pending: 1, stopped: 0 }, needs_you: false, from_node: null,
  nodes: {
    later: NODE("Later", "template", "pending", { started: null, ended: null }), sk: NODE("Left out", "template", "skipped", { started: null, ended: NOW - 99.5 }), boom: NODE("Boom", "stop", "error", { error: "nope", started: NOW - 98, ended: NOW - 97.5, attempts: 2, output: "partial" }),
    s: NODE("Task", "input", "done", { output: "hello", started: NOW - 100, ended: NOW - 99.9 }), a: NODE("Step A", "template", "done", { input: "hello", output: '[{"n":1},{"n":2}]', started: NOW - 99, ended: NOW - 98.9, reused: true, pinned: true }),
  }, ...over });
const DOC = { id: "wf", name: "Flow wf", version: 3, autonomy: "auto", nodes: [{ id: "s", type: "input", label: "Task", position: { x: 0, y: 0 }, config: {} }, { id: "a", type: "template", label: "Step A", position: { x: 200, y: 0 }, config: { text: "x" } }], edges: [{ source: "s", target: "a" }] };
const CATALOG = { groups: [], nodes: [{ type: "input", group: "trigger", label: "Task", desc: "", fields: [], outputs: ["out"], inputs: 0 }, { type: "template", group: "data", label: "Text template", desc: "", fields: [], outputs: ["out"], inputs: 1 }, { type: "stop", group: "flow", label: "Stop with error", desc: "", fields: [], outputs: [], inputs: 1 }] };

function routes(run: () => any, extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/workflows/runs/r1": () => jsonResponse(run()), "GET /api/workflows/wf/versions/3": () => jsonResponse(DOC), "GET /api/workflows/wf": () => jsonResponse({ ...DOC, version: 5 }), "GET /api/workflows/nodes": () => jsonResponse(CATALOG), ...extra });
}
const props = () => ({ id: "r1", onClose: vi.fn(), onChanged: vi.fn(), onOpen: vi.fn() });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); push.mockClear(); });

describe("RunDetail", () => {
  it("shows who started the run, what it was given, the versions, every box with its timing, the result and the log", async () => {
    routes(() => RUN({ final: "all done" }));
    render(<RunDetail {...props()} />);
    expect(screen.getByLabelText("Loading the run")).toBeInTheDocument();
    expect(await screen.findByText("Flow wf")).toBeInTheDocument();
    expect(screen.getByText(/Failed/, { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText(/Webhook · started 1 min ago · took 10 s · \$0\.0123 · 1,500 tokens · ran by itself/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("run-version")).toHaveTextContent("Ran on version v3 (the workflow is now v5; Run again uses v5)."));
    expect(screen.getByText("What the webhook sent (secrets removed)")).toBeInTheDocument();
    expect(screen.getByTestId("run-task")).toHaveTextContent("payload [secret removed]");
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    const boxes = within(screen.getByTestId("run-boxes"));
    expect(boxes.getAllByRole("button").map((b) => b.textContent?.replace(/\s+/g, " ").trim())).toEqual(["✓ done Task", "– skipped Left out", "✓ done Step A", "✕ failed Boom", "· waiting Later"]);   // in the order they ran (a box that never started sorts by when it ended), boxes that did not run last
    expect(screen.getByText(/2 tries/)).toBeInTheDocument(); expect(screen.getByText(/reused from the earlier run/)).toBeInTheDocument(); expect(screen.getByText(/· pinned/)).toBeInTheDocument();
    expect(screen.getByTestId("run-result")).toHaveTextContent("all done");
    const graph = await screen.findByTestId("run-graph");
    expect(within(graph).getByTestId("rf")).toHaveAttribute("data-delete", "null");                       // the picture of the run cannot be edited
    expect(within(graph).getByTestId("rf")).toHaveAttribute("data-connectable", "false");
    expect(graph.querySelectorAll("[data-draggable='false']")).toHaveLength(2);
    await userEvent.click(within(graph).getByRole("button", { name: "nudge" }));                               // a nudge changes nothing: there is nobody to save it
    expect(screen.getByTestId("run-graph")).toBeInTheDocument();
    expect(within(graph).getAllByText("done", { selector: ".wf-status" })).toHaveLength(2);
    await userEvent.click(screen.getByText("Run log"));
    const log = within(screen.getByTestId("run-log-list")).getAllByRole("listitem").map((l) => l.textContent);
    expect(log[0]).toMatch(/Task started/); expect(log.some((l) => /Boom failed: nope/.test(l ?? ""))).toBe(true);
  });

  it("opens a box to show its input, output and error, and runs again from it", async () => {
    const user = userEvent.setup();
    const p = props();
    const { calls } = routes(() => RUN(), { "POST /api/workflows/runs/r1/rerun": () => jsonResponse({ id: "r9" }) });
    render(<RunDetail {...p} />);
    await user.click(await screen.findByRole("button", { name: /Step A/ }));
    const box = within(screen.getByTestId("run-box"));
    expect(box.getByText("Text template")).toBeInTheDocument();
    expect(box.getByTestId("Input-data")).toHaveTextContent("hello"); expect(box.getByTestId("Output-data")).toHaveTextContent('[{"n":1},{"n":2}]');
    await user.click(box.getByRole("tab", { name: "table" }));
    await user.click(box.getByRole("button", { name: "Run again from this box" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname.endsWith("/rerun"))?.body).toEqual({ from_node: "a" }));
    expect(p.onOpen).toHaveBeenCalledWith("r9"); expect(p.onChanged).toHaveBeenCalled();
    await user.click(within(screen.getByTestId("run-boxes")).getByRole("button", { name: /Boom/ }));          // a failed box shows its error
    expect(within(screen.getByTestId("run-box")).getByText("nope")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close box details" }));
    expect(screen.queryByTestId("run-box")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Task/ }));                                         // the starting box cannot be a place to start from
    expect(within(screen.getByTestId("run-box")).queryByRole("button", { name: "Run again from this box" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /Task/ }));                                         // a second click closes it
    expect(screen.queryByTestId("run-box")).toBeNull();
  });

  it("selecting a box on the picture selects it in the list", async () => {
    const user = userEvent.setup();
    routes(() => RUN());
    render(<RunDetail {...props()} />);
    const graph = await screen.findByTestId("run-graph");
    await user.click(graph.querySelector("[data-nodeid='a']")!);
    expect(within(await screen.findByTestId("run-box")).getByText("Step A")).toBeInTheDocument();
  });

  it("runs the same task again, or again from the box that failed, and follows the new run", async () => {
    const user = userEvent.setup();
    const p = props();
    const { calls } = routes(() => RUN({ version: 5 }), { "POST /api/workflows/runs/r1/rerun": () => jsonResponse({ id: "r8" }), "GET /api/workflows/wf/versions/5": () => jsonResponse(DOC) });
    render(<RunDetail {...p} />);
    await user.click(await screen.findByRole("button", { name: "Run again" }));
    await waitFor(() => expect(p.onOpen).toHaveBeenCalledWith("r8"));
    expect(calls.filter((c) => c.url.pathname.endsWith("/rerun")).at(-1)?.body).toEqual({});
    await user.click(screen.getByRole("button", { name: "Run again from “Boom”" }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname.endsWith("/rerun")).at(-1)?.body).toEqual({ from_node: "failed" }));
    expect(screen.getByTestId("run-version")).toHaveTextContent("Ran on version v5.");                       // the workflow has not changed since: no "now" note
  });

  it("shows why a run could not be started again", async () => {
    const user = userEvent.setup();
    routes(() => RUN(), { "POST /api/workflows/runs/r1/rerun": () => jsonResponse({ detail: "this run is still working: stop it or wait for it to finish" }, { status: 422 }) });
    render(<RunDetail {...props()} />);
    await user.click(await screen.findByRole("button", { name: "Run again" }));
    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
    expect(screen.getByText(/this run is still working/)).toBeInTheDocument();
  });

  it("exports the run as a file, and opens the editor", async () => {
    const user = userEvent.setup();
    const made: Blob[] = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: (b: Blob) => { made.push(b); return "blob:x"; }, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    routes(() => RUN());
    render(<RunDetail {...props()} />);
    await user.click(await screen.findByRole("button", { name: "Export JSON" }));
    expect(click).toHaveBeenCalled();
    expect(JSON.parse(await made[0].text()).id).toBe("r1");
    await user.click(screen.getByRole("button", { name: "Open in editor" }));
    expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=wf");
    await user.click(screen.getByRole("button", { name: "Flow wf" }));
    expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=wf");
    await user.click(screen.getByRole("button", { name: "See the versions in the editor" }));
    expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=wf&versions=1");
    click.mockRestore();
  });

  it("deletes after a confirmation, and says when it cannot", async () => {
    const user = userEvent.setup();
    const p = props();
    let fail = true;
    routes(() => RUN(), { "DELETE /api/workflows/runs/r1": () => (fail ? jsonResponse({ detail: "still working: stop it first" }, { status: 409 }) : jsonResponse({ ok: true })) });
    render(<RunDetail {...p} />);
    await user.click(await screen.findByRole("button", { name: "Delete…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("still working: stop it first")).toBeInTheDocument();
    expect(p.onClose).not.toHaveBeenCalled();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(p.onClose).toHaveBeenCalled());
    expect(p.onChanged).toHaveBeenCalled();
  });

  it("follows a running run, offers Stop, and stops refreshing when it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let state = "running";
    const p = props();
    const { calls } = routes(() => (state === "running" ? RUN({ status: "running", ended: null, error: null, nodes: { s: NODE("Task", "input", "done"), w: NODE("Wait", "wait", "running", { ended: null }) } }) : RUN({ status: "stopped", error: null })),
      { "POST /api/workflows/runs/r1/stop": () => { state = "stopped"; return jsonResponse({ ok: true }); } });
    render(<RunDetail {...p} />);
    expect(await screen.findByText(/Running/, { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText(/running for/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run again" })).toBeNull(); expect(screen.queryByRole("button", { name: "Delete…" })).toBeNull();
    const reads = () => calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/workflows/runs/r1").length;
    const before = reads();
    await act(async () => { await vi.advanceTimersByTimeAsync(2600); });
    expect(reads()).toBeGreaterThan(before);
    await act(async () => { screen.getByRole("button", { name: "Stop" }).click(); });
    await waitFor(() => expect(screen.getByText(/Stopped/, { selector: ".pill" })).toBeInTheDocument());
    expect(p.onChanged).toHaveBeenCalled();
    const after = reads();
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(reads()).toBe(after);
  });

  it("lets you answer a step that is waiting: yes, no, stop, and a form with details", async () => {
    const user = userEvent.setup();
    let nodes: any = { s: NODE("Task", "input", "done"), ask: NODE("Go on?", "approval", "waiting_approval", { message: "Shall we?", ended: null }), step: NODE("Review", "agent", "waiting_approval", { ended: null }),
      form: NODE("Details", "approval", "waiting_approval", { ended: null, form: [{ name: "who", label: "Who", type: "text", options: [], required: true }] }) };
    const { calls } = routes(() => RUN({ status: "running", ended: null, error: null, needs_you: true, nodes }), { "POST /api/workflows/runs/r1/decide": () => jsonResponse({ ok: true }) });
    render(<RunDetail {...props()} />);
    const wait = within(await screen.findByTestId("run-needs-you"));
    expect(wait.getByText("Shall we?")).toBeInTheDocument();
    expect(screen.getByText("needs you")).toBeInTheDocument();
    const decisions = () => calls.filter((c) => c.url.pathname.endsWith("/decide")).map((c) => c.body);
    await user.click(wait.getByRole("button", { name: "Yes" }));
    await waitFor(() => expect(decisions()[0]).toMatchObject({ node: "ask", decision: "approve" }));
    await user.click(wait.getAllByRole("button", { name: "No" })[0]);
    await waitFor(() => expect(decisions()[1]).toMatchObject({ node: "ask", decision: "skip" }));
    await user.click(wait.getAllByRole("button", { name: "Stop the run" })[0]);
    await waitFor(() => expect(decisions()[2]).toMatchObject({ node: "ask", decision: "stop" }));
    await user.click(wait.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(decisions()[3]).toMatchObject({ node: "step", decision: "approve" }));
    await user.click(wait.getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(decisions()[4]).toMatchObject({ node: "step", decision: "skip" }));
    await user.type(wait.getByLabelText("Who"), "Sam");
    await user.click(wait.getByRole("button", { name: "Send answers and continue" }));
    await waitFor(() => expect(decisions()[5]).toEqual({ node: "form", decision: "approve", values: { who: "Sam" } }));
  });

  it("shows the plan of a plan-only run instead of boxes", async () => {
    routes(() => RUN({ status: "plan", error: null, autonomy: "suggest", plan: [{ node: "a", label: "Step A", does: "Fill in a text" }], nodes: { a: NODE("Step A", "template", "pending") } }));
    render(<RunDetail {...props()} />);
    expect(await screen.findByTestId("run-plan")).toHaveTextContent("Step A: Fill in a text");
    expect(screen.queryByTestId("run-boxes")).toBeNull(); expect(screen.queryByTestId("run-graph")).toBeNull();
    expect(screen.getByText(/plan only/)).toBeInTheDocument();
  });

  it("copes with a version that is no longer kept, a missing catalog, a manual run and a run asked before each step", async () => {
    routes(() => RUN({ origin: undefined, autonomy: "approve", tokens: 0, cost_usd: 0, task: "", final: "" }), { "GET /api/workflows/wf/versions/3": () => jsonResponse({ detail: "no such version" }, { status: 404 }), "GET /api/workflows/nodes": () => jsonResponse({ detail: "x" }, { status: 500 }), "GET /api/workflows/wf": () => jsonResponse({ detail: "x" }, { status: 404 }) });
    render(<RunDetail {...props()} />);
    expect(await screen.findByText(/Run by you · started .* · asked before each step/)).toBeInTheDocument();
    expect(screen.getByText("What it was given")).toBeInTheDocument(); expect(screen.getByTestId("run-task")).toHaveTextContent("(no task)");
    expect(screen.getByTestId("run-version")).toHaveTextContent("Ran on version v3.");
    expect(screen.queryByTestId("run-graph")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Step A/ }));
    expect(within(screen.getByTestId("run-box")).getByText("template")).toBeInTheDocument();                  // no catalog: the plain type is shown
  });

  it("shows an error for a run that cannot be read and closes with either button", async () => {
    const user = userEvent.setup();
    const p = props();
    routes(() => ({ detail: "unknown run" }), { "GET /api/workflows/runs/r1": () => jsonResponse({ detail: "unknown run" }, { status: 404 }) });
    render(<RunDetail {...p} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown run");
    expect(screen.queryByLabelText("Loading the run")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Back to runs" }));
    await user.click(screen.getByRole("button", { name: "Close run details" }));
    expect(p.onClose).toHaveBeenCalledTimes(2);
  });

  it("a run that is not found in time does not update a closed panel", async () => {
    const p = props();
    routes(() => RUN());
    const { unmount } = render(<RunDetail {...p} />);
    unmount();
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  });
});
