import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

const push = vi.fn(), replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace }), useSearchParams: () => new URLSearchParams(search), usePathname: () => "/workflows/" }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

import FlowsTab from "@/components/workflows/FlowsTab";
import WorkflowsPage from "@/app/workflows/page";

const ROW = (id: string, over: Record<string, unknown> = {}) => ({ id, name: `Flow ${id}`, description: `about ${id}`, version: 2, updated: Date.now() / 1000 - 600, nodes: 4, autonomy: "approve", last_run: { status: "done", started: Date.now() / 1000 - 60, cost_usd: 0 }, has_draft: false, uses: [], ...over });
const TPL = { id: "research", name: "Research and summarise", description: "Look at a topic", nodes: 4, doc: { name: "Research and summarise", autonomy: "auto", nodes: [{ id: "start", type: "input", label: "Task", config: {}, position: { x: 0, y: 0 } }], edges: [] } };

function routes(rows: any[], extra: Record<string, any> = {}) {
  return routeFetch({ "GET /api/workflows": () => jsonResponse({ workflows: rows }), "GET /api/workflows/templates": () => jsonResponse({ templates: [TPL] }), "GET /api/workflows/apis": () => jsonResponse({ apis: [] }), ...extra });
}

const dlg = () => within(screen.getByRole("dialog"));

describe("Flows tab", () => {
  it("lists workflows with search and last-run filter, and opens or runs one", async () => {
    push.mockClear();
    routes([ROW("a"), ROW("b", { last_run: { status: "error", started: 1, cost_usd: 0 }, has_draft: true, autonomy: "auto" }), ROW("c", { last_run: null, autonomy: "suggest" })]);
    const user = userEvent.setup();
    render(<FlowsTab />);
    const table = await screen.findByTestId("flows-table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("unsaved draft")).toBeInTheDocument(); expect(within(table).getByText("plan only")).toBeInTheDocument(); expect(within(table).getByText("error")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search workflows"), "about b");
    expect(within(screen.getByTestId("flows-table")).getAllByRole("row")).toHaveLength(2);
    await user.clear(screen.getByLabelText("Search workflows"));
    await user.selectOptions(screen.getByLabelText("Last run"), "never");
    expect(within(screen.getByTestId("flows-table")).getByText("Flow c")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Last run"), "error");
    expect(within(screen.getByTestId("flows-table")).getAllByRole("row")).toHaveLength(2);
    await user.selectOptions(screen.getByLabelText("Last run"), "stopped");
    expect(screen.getByText("No workflow matches.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Last run"), "");
    await user.click(screen.getAllByRole("button", { name: "Open" })[0]);
    expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=a");
    await user.click(screen.getByRole("button", { name: "Run a" }));
    expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=a&run=1");
    await user.click(screen.getByRole("button", { name: "Flow a" }));
    expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=a");
  });

  it("shows an empty state and load errors", async () => {
    routes([]);
    const { unmount } = render(<FlowsTab />);
    expect(await screen.findByTestId("flows-empty")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create the first one" }));
    expect(screen.getByRole("dialog", { name: "New workflow" })).toBeInTheDocument();
    unmount();
    routeFetch({ "GET /api/workflows": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<FlowsTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });

  it("creates a workflow: build it myself, talk to an agent, or from a template (names never clash)", async () => {
    push.mockClear();
    const { calls } = routes([ROW("my_flow")], { "POST /api/workflows": () => jsonResponse({}) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByRole("button", { name: "New workflow" }));
    await user.type(screen.getByLabelText("Workflow name"), "My flow");
    await user.click(screen.getByRole("button", { name: /Build it myself/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/workflows/edit/?id=my_flow_2"));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/workflows")?.body).toMatchObject({ id: "my_flow_2", name: "My flow", nodes: [{ id: "start", type: "input" }] });
    await user.click(screen.getByRole("button", { name: /Talk to an agent/ }));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=my_flow_2&chat=1"));
    await user.click(screen.getByRole("button", { name: /Research and summarise/ }));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=my_flow_2"));
  });

  it("a template keeps its own name when none is typed; failures are shown; the chooser can switch to import", async () => {
    push.mockClear();
    routes([], { "POST /api/workflows": () => jsonResponse({ detail: "the workflow id is bad" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-empty");
    await user.click(screen.getByRole("button", { name: "New workflow" }));
    await user.click(screen.getByRole("button", { name: /Research and summarise/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("id is bad");
    await user.click(screen.getByRole("button", { name: "Import a workflow file instead" }));
    expect(screen.getByRole("dialog", { name: "Import a workflow" })).toBeInTheDocument();
  });

  it("imports from a file or paste and settles a name clash three ways", async () => {
    push.mockClear();
    let mode = "";
    const { calls } = routes([ROW("a")], { "POST /api/workflows/import": (init: any) => { mode = JSON.parse(String(init?.body)).on_conflict; return mode === "cancel" ? jsonResponse({ detail: "a workflow 'x' already exists" }, { status: 409 }) : jsonResponse({ id: mode === "replace" ? "x" : "x_2" }); } });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByRole("button", { name: "Import" }));
    await user.click(screen.getByLabelText("Pasted workflow"));
    await user.paste("not json");
    await user.click(dlg().getByRole("button", { name: "Import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
    await user.clear(screen.getByLabelText("Pasted workflow"));
    fireEvent.change(screen.getByLabelText("Pasted workflow"), { target: { value: '{"id":"x","name":"X"}' } });
    await user.click(dlg().getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/already exists. What should happen/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep both" }));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=x_2"));
    await user.click(screen.getAllByRole("button", { name: "Import" })[0]);
    await user.upload(screen.getByLabelText("Workflow file"), new File(['{"id":"x","name":"X"}'], "x.json", { type: "application/json" }));
    await waitFor(() => expect(screen.getByLabelText("Pasted workflow")).toHaveValue('{"id":"x","name":"X"}'));
    await user.click(dlg().getByRole("button", { name: "Import" }));
    await user.click(await screen.findByRole("button", { name: /Replace it/ }));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith("/workflows/edit/?id=x"));
    expect(calls.filter((c) => c.url.pathname.endsWith("/import")).map((c) => c.body.on_conflict)).toEqual(["cancel", "rename", "cancel", "replace"]);
    await user.click(screen.getAllByRole("button", { name: "Import" })[0]);
    fireEvent.change(screen.getByLabelText("Pasted workflow"), { target: { value: '{"id":"x"}' } });
    await user.click(dlg().getByRole("button", { name: "Import" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("duplicates, renames, exports, turns into an agent and deletes (with a used-by warning)", async () => {
    const { calls } = routes([ROW("a")], { "POST /api/workflows/a/duplicate": () => jsonResponse({}), "GET /api/workflows/a": () => jsonResponse({ id: "a", name: "Flow a", nodes: [], edges: [], autonomy: "auto" }), "PUT /api/workflows/a": () => jsonResponse({}),
      "POST /api/workflows/a/to-agent": () => jsonResponse({ agent: "wf_a" }), "GET /api/workflows/a/used-by": () => jsonResponse({ used_by: ["wf_b", "agent wf_a"] }), "DELETE /api/workflows/a": () => jsonResponse({ ok: true }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByLabelText("More for a"));
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/workflows/a/duplicate")).toBe(true));
    expect(screen.getByRole("link", { name: "Export JSON" })).toHaveAttribute("href", expect.stringContaining("/api/workflows/a/export"));
    await user.click(screen.getByRole("button", { name: "Turn into an agent" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname.endsWith("/to-agent"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await user.clear(screen.getByLabelText("New name")); await user.type(screen.getByLabelText("New name"), "Renamed");
    await user.click(dlg().getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT" && c.url.pathname === "/api/workflows/a")?.body).toMatchObject({ name: "Renamed", label: "Renamed" }));
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("wf_b, agent wf_a");
    await user.click(screen.getByRole("button", { name: "Delete anyway" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.search === "?force=true")).toBe(true));
  });

  it("a failing action is reported, and rename can be cancelled", async () => {
    routes([ROW("a")], { "POST /api/workflows/a/duplicate": () => jsonResponse({ detail: "no way" }, { status: 422 }), "GET /api/workflows/a/used-by": () => jsonResponse({ detail: "x" }, { status: 500 }), "GET /api/workflows/a": () => jsonResponse({ detail: "gone" }, { status: 404 }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByLabelText("More for a"));
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no way");
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    expect(await screen.findByRole("dialog", { name: /Delete the workflow/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await user.click(dlg().getByRole("button", { name: "Rename" }));
    expect(await screen.findByText("gone")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Workflows page", () => {
  it("shows the Flows tab by default and switches tabs through the address", async () => {
    replace.mockClear(); search = "";
    routes([]);
    const user = userEvent.setup();
    const { unmount } = render(<WorkflowsPage />);
    expect(await screen.findByTestId("flows-empty")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "OpenAPI steps" }));
    expect(replace).toHaveBeenLastCalledWith("/workflows/?tab=openapi");
    await user.click(screen.getByRole("tab", { name: "Flows" }));
    expect(replace).toHaveBeenLastCalledWith("/workflows/");
    unmount();
    search = "tab=openapi";
    render(<WorkflowsPage />);
    expect(await screen.findByTestId("openapi-tab")).toBeInTheDocument();
    search = "";
  });

  it("selects workflows and exports them as one file", async () => {
    let n = 0;
    const { calls } = routes([ROW("a"), ROW("b")], { "POST /api/workflows/export": () => (n++ === 0 ? new Response('{"format":"agent-studio-workflows"}', { status: 200 }) : jsonResponse({ detail: "none of those workflows exist" }, { status: 404 })) });
    const created = vi.fn(() => "blob:x"), revoked = vi.fn();
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: revoked });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Select a"));
    expect(screen.getByText("Export 1 selected")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Select all"));
    expect(screen.getByText("Export 2 selected")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Select b"));
    expect(screen.getByText("Export 1 selected")).toBeInTheDocument();
    await user.click(screen.getByText("Export 1 selected"));
    await waitFor(() => expect(revoked).toHaveBeenCalledWith("blob:x"));
    expect(calls.find((c) => c.url.pathname === "/api/workflows/export")!.body).toEqual({ ids: ["a"] });
    expect(created).toHaveBeenCalled();
    await user.click(screen.getByText("Export 1 selected"));
    expect(await screen.findByRole("alert")).toHaveTextContent("none of those workflows exist");
    await user.click(screen.getByLabelText("Select all"));
    expect(screen.getByText("Export 2 selected")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Select all"));                                                // all ticked, so this clears them
    expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
  });

  it("opens the earlier runs of a workflow under its row", async () => {
    routes([ROW("a"), ROW("b")], {
      "GET /api/workflows/a/runs": () => jsonResponse({ runs: [{ id: "r1", status: "done", task: "write the report", started: Date.now() / 1000 - 90, cost_usd: 0.0123, version: 2 }, { id: "r2", status: "error", task: "", started: Date.now() / 1000 - 9000, cost_usd: 0, version: 1 }] }),
      "GET /api/workflows/b/runs": () => jsonResponse({ runs: [] }),
    });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByLabelText("Show earlier runs of a"));
    const row = await screen.findByTestId("runs-row");
    expect(row).toHaveTextContent("write the report"); expect(row).toHaveTextContent("(no task)"); expect(row).toHaveTextContent("$0.0123"); expect(row).toHaveTextContent("v2");
    expect(screen.getByLabelText("Show earlier runs of a")).toHaveAttribute("aria-expanded", "true");
    await user.click(within(row).getByRole("button", { name: "See all runs of Flow a" }));
    expect(push).toHaveBeenLastCalledWith("/workflows/?tab=runs&workflow=a");
    await user.click(screen.getByLabelText("Show earlier runs of b"));
    await waitFor(() => expect(screen.getAllByTestId("runs-row")).toHaveLength(2));
    expect(screen.getByText("No earlier runs.")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Show earlier runs of a"));
    expect(screen.getAllByTestId("runs-row")).toHaveLength(1);
  });

  it("shows loading, and a failed lookup counts as no runs; collapsing while it loads drops the answer", async () => {
    let release: (r: Response) => void = () => undefined;
    routes([ROW("a"), ROW("c")], { "GET /api/workflows/a/runs": () => new Promise<Response>((r) => { release = r; }), "GET /api/workflows/c/runs": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByLabelText("Show earlier runs of a"));
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Show earlier runs of a"));
    release(jsonResponse({ runs: [] }));
    await user.click(screen.getByLabelText("Show earlier runs of c"));
    expect(await screen.findByText("No earlier runs.")).toBeInTheDocument();
    expect(screen.getAllByTestId("runs-row")).toHaveLength(1);
  });

  it("marks workflows that start from a webhook", async () => {
    routes([ROW("a", { trigger: "webhook", has_hook: true }), ROW("b", { trigger: "webhook", has_hook: false }), ROW("c", { trigger: "input" })]);
    render(<FlowsTab />);
    const table = await screen.findByTestId("flows-table");
    expect(within(table).getByText("webhook")).toBeInTheDocument();
    expect(within(table).getByText("webhook (no address yet)")).toBeInTheDocument();
  });

  it("imports a file of several workflows, with a choice for names that are taken", async () => {
    const { calls } = routes([ROW("a")], { "POST /api/workflows/import": () => jsonResponse({ imported: ["x", "y"], skipped: ["z"], errors: ["bad"] }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByText("Import"));
    fireEvent.change(screen.getByLabelText("Pasted workflow"), { target: { value: '{"format":"agent-studio-workflows","workflows":[{"id":"x"},{"id":"y"},{"id":"z"}]}' } });
    expect(dlg().getByText(/holds 3 workflow\(s\)/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Import workflows" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("If a name is taken"), "replace");
    await user.click(screen.getByText("Import 3"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/workflows/import")!.body).toMatchObject({ on_conflict: "replace", workflows: [{ id: "x" }, { id: "y" }, { id: "z" }] });
    expect(calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/workflows")).toHaveLength(2);        // the list is reloaded
  });

  it("reports a bad file of workflows and a file that is not JSON", async () => {
    routes([ROW("a")], { "POST /api/workflows/import": () => jsonResponse({ detail: "a file holds 1 to 50 workflows" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByText("Import"));
    fireEvent.change(screen.getByLabelText("Pasted workflow"), { target: { value: "not json at all" } });
    await user.click(dlg().getByText("Import"));
    expect(await screen.findByText("That is not valid JSON.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Pasted workflow"), { target: { value: '{"workflows":[]}' } });
    await user.click(screen.getByText("Import 0"));
    expect(await screen.findByText("a file holds 1 to 50 workflows")).toBeInTheDocument();
  });

  it("opens the workflow settings", async () => {
    routes([ROW("a")], { "GET /api/workflows/settings": () => jsonResponse({ settings: { webhooks_enabled: false, hook_rate_per_min: 30, hook_max_body_kb: 256, hook_wait_max_s: 30, max_nodes: 60, run_retention: 100, http_timeout_s: 30, http_max_kb: 512, http_allow_hosts: [] }, credentials: [] }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByText("Settings"));
    expect(await screen.findByTestId("wf-settings")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Close"));
    expect(screen.queryByTestId("wf-settings")).not.toBeInTheDocument();
  });

  it("copes with templates that cannot load and an export that fails without a reason, and closes the chooser and the rename dialog", async () => {
    routes([ROW("a")], { "GET /api/workflows/templates": () => jsonResponse({ detail: "down" }, { status: 500 }), "POST /api/workflows/export": () => new Response("<html>bad gateway</html>", { status: 502 }) });
    const user = userEvent.setup();
    render(<FlowsTab />);
    await screen.findByTestId("flows-table");
    await user.click(screen.getByText("New workflow"));
    expect(await screen.findByRole("dialog", { name: "New workflow" })).toBeInTheDocument();
    expect(screen.queryByText("Or start from a template")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByLabelText("Select a"));
    await user.click(screen.getByText("Export 1 selected"));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 502");
    await user.click(screen.getByLabelText("More for a"));
    await user.click(screen.getByText("Rename"));
    expect(screen.getByRole("dialog", { name: "Rename Flow a" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
