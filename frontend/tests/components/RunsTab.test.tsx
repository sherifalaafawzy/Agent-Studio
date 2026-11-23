import { render, screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

const push = vi.fn(), replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace }), useSearchParams: () => new URLSearchParams(search) }));
vi.mock("@/components/workflows/RunDetail", () => ({
  default: ({ id, onClose, onChanged, onOpen }: any) => (
    <aside data-testid="detail-stub">detail {id}<button onClick={onClose}>stub close</button><button onClick={onChanged}>stub changed</button><button onClick={() => onOpen("new1")}>stub open new</button></aside>
  ),
}));

import RunsTab from "@/components/workflows/RunsTab";
import WorkflowsPage from "@/app/workflows/page";

const NOW = Date.now() / 1000;
const C = (over: Record<string, number> = {}) => ({ total: 4, done: 3, error: 0, skipped: 0, running: 0, waiting: 0, pending: 1, stopped: 0, ...over });
const ROW = (id: string, over: Record<string, unknown> = {}) => ({ id, workflow: "wf_a", name: "Flow A", status: "done", origin: "manual", autonomy: "auto", version: 2, started: NOW - 600, ended: NOW - 590, duration: 10, task: `task ${id}`, error: null,
  cost_usd: 0, tokens: 0, from_node: null, counts: C(), needs_you: false, ...over });
const LIST = (runs: any[], over: Record<string, unknown> = {}) => ({ runs, total: runs.length, counts: { running: 0, done: runs.length, error: 0, stopped: 0, plan: 0 }, needs_you: 0, retention: 100, limit: 25, offset: 0, ...over });

function routes(list: (url: URL) => any, extra: Record<string, any> = {}) {
  return routeFetch({ "GET /api/workflows": () => jsonResponse({ workflows: [{ id: "wf_a", name: "Flow A" }, { id: "wf_b", name: "Flow B" }] }), "GET /api/workflows/runs": (_i, url) => jsonResponse(list(url)), ...extra });
}
const lastRuns = (calls: any[]) => [...calls].reverse().find((c) => c.url.pathname === "/api/workflows/runs")!.url.searchParams;
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); push.mockClear(); replace.mockClear(); search = ""; });

describe("Runs tab", () => {
  it("shows a loading state, then every run with its result, trigger, boxes, cost and version", async () => {
    routes(() => LIST([
      ROW("r1", { cost_usd: 0.0123, tokens: 1500, counts: C({ done: 2, error: 1, skipped: 1, pending: 0 }), status: "error", error: "nope", origin: "webhook" }),
      ROW("r2", { status: "running", ended: null, duration: 3, needs_you: true, origin: "agent", counts: C({ waiting: 1, done: 1, pending: 2 }) }),
      ROW("r3", { status: "plan", origin: "run_from" }), ROW("r4", { status: "stopped" }), ROW("r5", { status: "weird" })], { counts: { running: 1, done: 0, error: 1, stopped: 1, plan: 1 } }));
    render(<RunsTab />);
    expect(screen.getByTestId("runs-loading")).toBeInTheDocument();
    const table = await screen.findByTestId("runs-table");
    const rows = within(table).getAllByTestId("run-row");
    expect(rows).toHaveLength(5);
    expect(within(rows[0]).getByText(/Failed/)).toBeInTheDocument(); expect(within(rows[0]).getByText("Webhook")).toBeInTheDocument(); expect(within(rows[0]).getByText("nope")).toBeInTheDocument();
    expect(within(rows[0]).getByText(/3 of 4 ran/)).toBeInTheDocument(); expect(within(rows[0]).getByText(/1 failed/)).toBeInTheDocument(); expect(within(rows[0]).getByText(/1 skipped/)).toBeInTheDocument();
    expect(within(rows[0]).getByText(/\$0\.0123 · 1,500 tokens/)).toBeInTheDocument(); expect(within(rows[0]).getByText("v2")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/Running/)).toBeInTheDocument(); expect(within(rows[1]).getByText("needs you")).toBeInTheDocument(); expect(within(rows[1]).getByText(/1 waiting/)).toBeInTheDocument(); expect(within(rows[1]).getByText("An agent")).toBeInTheDocument(); expect(within(rows[1]).getByText("3.0 s…")).toBeInTheDocument();
    expect(within(rows[2]).getByText("From a box")).toBeInTheDocument(); expect(within(rows[3]).getByText(/Stopped/)).toBeInTheDocument(); expect(within(rows[4]).getByText("weird")).toBeInTheDocument();
    expect(within(rows[1]).getByRole("checkbox")).toBeDisabled();
    expect(screen.getByText(/Showing 1–5 of 5\. Agent Studio keeps the latest 100 runs of each workflow\./)).toBeInTheDocument();
    const chips = within(screen.getByRole("group", { name: "Show runs by result" }));
    expect(chips.getByRole("button", { name: /All 4/ })).toBeInTheDocument(); expect(chips.getByRole("button", { name: /Failed 1/ })).toBeInTheDocument();
  });

  it("shows an empty state, and a different one when filters hide everything", async () => {
    const user = userEvent.setup();
    routes((u) => (u.searchParams.get("q") ? LIST([], { counts: {} }) : LIST([], { counts: {} })));
    const { unmount } = render(<RunsTab />);
    expect(await screen.findByTestId("runs-empty")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Flows" }));
    expect(replace).toHaveBeenCalledWith("/workflows/");
    await user.type(screen.getByLabelText("Search runs"), "zzz");
    expect(await screen.findByTestId("runs-none")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear the filters" }));
    expect(await screen.findByTestId("runs-empty")).toBeInTheDocument();
    expect(screen.getByLabelText("Search runs")).toHaveValue("");
    unmount();
  });

  it("filters by result, workflow, what started it, when and what needs you, and searches", async () => {
    const user = userEvent.setup();
    const { calls } = routes(() => LIST([ROW("r1")], { needs_you: 2 }));
    render(<RunsTab />);
    await screen.findByTestId("runs-table");
    await user.click(screen.getByRole("button", { name: /Failed/ }));
    await waitFor(() => expect(lastRuns(calls).get("status")).toBe("error"));
    expect(screen.getByRole("button", { name: /Failed/ })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: /^All/ }));
    await waitFor(() => expect(lastRuns(calls).get("status")).toBeNull());
    await user.selectOptions(screen.getByLabelText("Workflow"), "wf_b");
    await waitFor(() => expect(lastRuns(calls).get("workflow")).toBe("wf_b"));
    await user.selectOptions(screen.getByLabelText("Started by"), "webhook");
    await waitFor(() => expect(lastRuns(calls).get("origin")).toBe("webhook"));
    await user.selectOptions(screen.getByLabelText("When"), "24h");
    await waitFor(() => expect(Number(lastRuns(calls).get("since"))).toBeGreaterThan(NOW - 86400 - 60));
    expect(Number(lastRuns(calls).get("since"))).toBeLessThan(NOW);
    await user.click(screen.getByRole("button", { name: "Needs you (2)" }));
    await waitFor(() => expect(lastRuns(calls).get("needs_you")).toBe("true"));
    await user.type(screen.getByLabelText("Search runs"), "boom");
    await waitFor(() => expect(lastRuns(calls).get("q")).toBe("boom"));
    expect(lastRuns(calls).get("offset")).toBe("0");
  });

  it("sorts by a column, flipping the direction on a second click", async () => {
    const user = userEvent.setup();
    const { calls } = routes(() => LIST([ROW("r1")]));
    render(<RunsTab />);
    await screen.findByTestId("runs-table");
    expect(screen.getByRole("columnheader", { name: /^Started( [↑↓])?$/ })).toHaveAttribute("aria-sort", "descending");
    await user.click(screen.getByRole("button", { name: /^Started/ }));
    await waitFor(() => expect(lastRuns(calls).get("order")).toBe("asc"));
    expect(screen.getByRole("columnheader", { name: /^Started( [↑↓])?$/ })).toHaveAttribute("aria-sort", "ascending");
    await user.click(screen.getByRole("button", { name: /^Cost/ }));
    await waitFor(() => expect(lastRuns(calls).get("sort")).toBe("cost"));
    expect(lastRuns(calls).get("order")).toBe("desc");
    await user.click(screen.getByRole("button", { name: /^Took/ }));
    await user.click(screen.getByRole("button", { name: /^Result/ }));
    await waitFor(() => expect(lastRuns(calls).get("sort")).toBe("status"));
  });

  it("pages through the runs", async () => {
    const user = userEvent.setup();
    const { calls } = routes((u) => { const off = Number(u.searchParams.get("offset")); return LIST([ROW(`p${off}`)], { total: 60, offset: off }); });
    render(<RunsTab />);
    await screen.findByText(/Showing 1–25 of 60/);
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText(/Showing 26–50 of 60/);
    expect(lastRuns(calls).get("offset")).toBe("25");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText(/Showing 51–60 of 60/);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText(/Showing 26–50 of 60/);
  });

  it("selects runs and deletes them after a confirmation, saying what could not be deleted", async () => {
    const user = userEvent.setup();
    let rows = [ROW("r1"), ROW("r2"), ROW("run", { status: "running", ended: null })];
    const { calls } = routes(() => LIST(rows, { counts: { running: 1, done: 2, error: 0, stopped: 0, plan: 0 } }), {
      "POST /api/workflows/runs/delete": (init?: RequestInit) => { const ids = JSON.parse(String(init?.body)).ids as string[]; rows = rows.filter((r) => !ids.includes(r.id)); return jsonResponse({ deleted: ids.filter((i) => i !== "r2"), skipped: ids.includes("r2") ? [{ id: "r2", reason: "no such run" }] : [] }); } });
    render(<RunsTab />);
    await screen.findByTestId("runs-table");
    await user.click(screen.getByLabelText("Select all runs on this page"));
    expect(screen.getByRole("button", { name: "Delete 2 selected…" })).toBeInTheDocument();
    expect(screen.getByLabelText("Select run run")).not.toBeChecked();
    await user.click(screen.getByLabelText("Select all runs on this page"));
    expect(screen.queryByRole("button", { name: /selected/ })).toBeNull();
    await user.click(screen.getByLabelText("Select run r1")); await user.click(screen.getByLabelText("Select run r2")); await user.click(screen.getByLabelText("Select run r1"));
    await user.click(screen.getByRole("button", { name: "Delete 1 selected…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Delete 1 selected…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ ids: ["r2"] }));
    await waitFor(() => expect(screen.queryByLabelText("Select run r2")).toBeNull());
  });

  it("deleting the open run closes its panel; a failed delete is shown", async () => {
    const user = userEvent.setup();
    search = "tab=runs&run=r1";
    let fail = true;
    routes(() => LIST([ROW("r1"), ROW("r2")]), { "POST /api/workflows/runs/delete": () => (fail ? jsonResponse({ detail: "give the ids of 1 to 500 runs" }, { status: 422 }) : jsonResponse({ deleted: ["r1"], skipped: [] })) });
    render(<RunsTab />);
    expect(await screen.findByTestId("detail-stub")).toHaveTextContent("detail r1");
    await user.click(await screen.findByLabelText("Select run r1"));
    await user.click(screen.getByRole("button", { name: "Delete 1 selected…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("give the ids of 1 to 500 runs");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Delete 1 selected…" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByTestId("detail-stub")).toBeNull());
    expect(replace).toHaveBeenLastCalledWith("/workflows/?tab=runs");
  });

  it("opens a run in the side panel, follows a run started from it, and closes it", async () => {
    const user = userEvent.setup();
    routes(() => LIST([ROW("r1")]));
    render(<RunsTab />);
    await user.click(await screen.findByRole("button", { name: "Open run r1" }));
    expect(screen.getByTestId("detail-stub")).toHaveTextContent("detail r1");
    expect(replace).toHaveBeenLastCalledWith("/workflows/?tab=runs&run=r1");
    await user.click(screen.getByRole("button", { name: "stub changed" }));
    await user.click(screen.getByRole("button", { name: "stub open new" }));
    expect(screen.getByTestId("detail-stub")).toHaveTextContent("detail new1");
    expect(replace).toHaveBeenLastCalledWith("/workflows/?tab=runs&run=new1");
    await user.click(screen.getByRole("button", { name: "stub close" }));
    expect(screen.queryByTestId("detail-stub")).toBeNull();
  });

  it("starts from the address: a workflow filter and an open run", async () => {
    search = "tab=runs&workflow=wf_b&run=abc";
    const { calls } = routes(() => LIST([ROW("r1")]));
    render(<RunsTab />);
    expect(await screen.findByTestId("detail-stub")).toHaveTextContent("detail abc");
    await waitFor(() => expect(lastRuns(calls).get("workflow")).toBe("wf_b"));
  });

  it("refreshes by itself while a run is working, and stops when none is", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let step = 0;
    const { calls } = routes(() => (step === 0 ? LIST([ROW("r1", { status: "running", ended: null })], { counts: { running: 1, done: 0, error: 0, stopped: 0, plan: 0 } }) : LIST([ROW("r1")])));
    render(<RunsTab />);
    await screen.findByTestId("runs-table");
    step = 1;
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    await waitFor(() => expect(screen.getByText(/Worked/, { selector: ".pill" })).toBeInTheDocument());
    const n = calls.filter((c) => c.url.pathname === "/api/workflows/runs").length;
    await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
    expect(calls.filter((c) => c.url.pathname === "/api/workflows/runs").length).toBe(n);
  });

  it("shows a failed load with a way to try again, and copes with the workflow list failing", async () => {
    const user = userEvent.setup();
    let bad = true;
    routeFetch({ "GET /api/workflows": () => jsonResponse({ detail: "x" }, { status: 500 }), "GET /api/workflows/runs": () => (bad ? jsonResponse({ detail: "the database is busy" }, { status: 500 }) : jsonResponse(LIST([ROW("r1")]))) });
    render(<RunsTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("the database is busy");
    expect(screen.queryByTestId("runs-empty")).toBeNull();
    bad = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByTestId("runs-table");
    expect(screen.queryByRole("alert")).toBeNull();
    bad = true;
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the database is busy");
    expect(screen.getByTestId("runs-table")).toBeInTheDocument();                                   // the last good list stays on screen
  });
});

describe("Workflows page: the Runs tab", () => {
  it("is the third tab and is chosen through the address", async () => {
    const user = userEvent.setup();
    routes(() => LIST([ROW("r1")]), { "GET /api/workflows/apis": () => jsonResponse({ apis: [] }), "GET /api/workflows/templates": () => jsonResponse({ templates: [] }) });
    search = "tab=runs";
    const { unmount } = render(<WorkflowsPage />);
    expect(await screen.findByTestId("runs-tab")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Flows" }));
    expect(replace).toHaveBeenLastCalledWith("/workflows/");
    await user.click(screen.getByRole("tab", { name: "Runs" }));
    expect(replace).toHaveBeenLastCalledWith("/workflows/?tab=runs");
    unmount();
  });
});
