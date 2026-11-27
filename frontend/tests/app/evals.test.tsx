import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import EvalsPage from "@/app/evals/page";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const SETS = [{ id: "smoke", name: "Smoke", description: "d", case_count: 2, last_run: { id: "r1", status: "done", pass_rate: 90 } },
  { id: "other", name: "Other", description: "", case_count: 1, last_run: { id: "r0", status: "error" } }, { id: "fresh", name: "Fresh", description: "", case_count: 1, last_run: null }];
const FULL = { id: "smoke", name: "Smoke", description: "d", cases: [
  { id: "greet", prompt: "say hello", expect: [{ type: "contains", value: "hello" }, { type: "json_valid" }, { type: "max_chars", value: 50 }] }, { id: "two", prompt: "second", expect: [] }] };
const RUNNING = { id: "r2", set_id: "smoke", agent: "assistant", status: "running", started: 1_700_000_000, total: 2, done: 0 };
const DONE = { ...RUNNING, status: "done", done: 2, passed: 1, failed: 0, errors: 1, pass_rate: 50, cost_usd: 0.0123, results: [
  { id: "greet", prompt: "say hello", reply: "hello there", ok: true, checks: [{ type: "contains", ok: true, detail: "contains 'hello'" }], cost_usd: 0.001, ms: 12, blocked: false },
  { id: "two", prompt: "second", reply: "", ok: false, checks: [], cost_usd: 0, ms: 3, blocked: true, error: "RuntimeError: provider down" },
  { id: "three", prompt: "third", reply: "no", ok: false, checks: [{ type: "equals", ok: false, detail: "equals the expected text" }], cost_usd: 0.002, ms: 5, blocked: false }] };

function setup(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/evals/sets": () => jsonResponse({ sets: SETS, exports: {} }), "GET /api/evals/sets/smoke": () => jsonResponse(FULL),
    "GET /api/evals/runs": () => jsonResponse({ runs: [{ ...DONE, id: "r1", pass_rate: 90 }] }), "GET /api/agents": () => jsonResponse([{ id: "assistant", name: "Assistant" }]),
    "GET /api/providers": () => jsonResponse([{ id: "mock", label: "Mock", enabled: true }, { id: "off", label: "Off", enabled: false }]),
    "PUT /api/evals/sets": (init: any) => jsonResponse({ ...FULL, ...JSON.parse(init.body), id: JSON.parse(init.body).id ?? "set-abc123" }),
    "POST /api/evals/runs": () => jsonResponse(RUNNING), "GET /api/evals/runs/r2": () => jsonResponse(DONE), "GET /api/evals/runs/r1": () => jsonResponse({ ...DONE, id: "r1" }),
    "POST /api/evals/runs/r2/cancel": () => jsonResponse({ ...RUNNING, status: "cancelled" }), "DELETE /api/evals/sets/smoke": () => jsonResponse({ ok: true }),
    ...extra,
  });
}
const sent = (calls: Call[], m: string, path: string) => calls.filter((c) => c.method === m && c.url.pathname === path).map((c) => c.body);
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Evaluations page", () => {
  it("lists test sets with their last result", async () => {
    setup();
    render(<EvalsPage />);
    expect(await screen.findByText("Smoke")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
    expect(screen.getByText("0%")).toBeInTheDocument();                                           // a last run that failed without a rate
    expect(screen.getByText(/Every prompt uses model credits/)).toBeInTheDocument();
  });

  it("says what to do while no set is open, and lets the keyboard open one", async () => {
    setup();
    render(<EvalsPage />);
    const row = (await screen.findByText("Smoke")).closest(".ag-item") as HTMLElement;
    expect(screen.getByTestId("evals-empty")).toHaveTextContent("Pick a test set on the left");
    expect(row).toHaveAttribute("role", "button");
    expect(row).toHaveAttribute("aria-pressed", "false");
    fireEvent.keyDown(row, { key: "Tab" });                                                         // not a choosing key
    expect(screen.getByTestId("evals-empty")).toBeInTheDocument();
    fireEvent.keyDown(row, { key: "Enter" });
    await waitFor(() => expect(screen.queryByTestId("evals-empty")).toBeNull());
    expect(row).toHaveAttribute("aria-pressed", "true");
  });

  it("opens a set with its cases and checks, and edits them", async () => {
    const net = setup();
    render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    const cases = await screen.findAllByTestId("case");
    expect(within(cases[0]).getByLabelText("Prompt")).toHaveValue("say hello");
    expect(within(cases[0]).getAllByLabelText("Check")).toHaveLength(3);
    expect(within(cases[0]).getAllByLabelText("Value")).toHaveLength(2);                         // "is valid JSON" has no value
    await userEvent.type(within(cases[0]).getByLabelText("Prompt"), " now");
    await userEvent.selectOptions(within(cases[0]).getAllByLabelText("Check")[1], "regex");
    await userEvent.type(within(cases[0]).getAllByLabelText("Value")[1], "h.llo");
    fireEvent.change(within(cases[0]).getAllByLabelText("Value")[2], { target: { value: "99" } });
    await userEvent.click(within(cases[0]).getAllByRole("button", { name: "Remove check" })[0]);
    await userEvent.click(within(cases[1]).getByRole("button", { name: "Add a check" }));
    await userEvent.click(within(cases[1]).getByRole("button", { name: "Remove case" }));
    await userEvent.click(screen.getByRole("button", { name: "Add a case" }));
    await userEvent.type(screen.getAllByLabelText("Prompt")[1], "a new one");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(net.calls, "PUT", "/api/evals/sets")).toHaveLength(1));
    const body = sent(net.calls, "PUT", "/api/evals/sets")[0];
    expect(body.id).toBe("smoke");
    expect(body.cases).toEqual([{ id: "greet", prompt: "say hello now", expect: [{ type: "regex", value: "h.llo" }, { type: "max_chars", value: 99 }] }, { id: "c2", prompt: "a new one", expect: [] }]);
  });

  it("creates a new set (the id is optional) and deletes one only after you confirm", async () => {
    const net = setup();
    render(<EvalsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "+ New" }));
    expect(screen.queryByTestId("run-panel")).toBeNull();
    await userEvent.type(screen.getByLabelText("Name"), "Brand new");
    await userEvent.type(screen.getByLabelText(/^Id/), "brand-new");
    await userEvent.clear(screen.getByLabelText(/^Id/));                                          // left empty: the server makes one from the name
    await userEvent.type(screen.getByLabelText("Description"), "my description");
    await userEvent.type(screen.getAllByLabelText("Prompt")[0], "hi");
    await userEvent.click(screen.getByRole("button", { name: "Add a check" }));
    await userEvent.type(screen.getByLabelText("Value"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(net.calls, "PUT", "/api/evals/sets")[0]).toMatchObject({ name: "Brand new", description: "my description" }));
    expect(sent(net.calls, "PUT", "/api/evals/sets")[0]).not.toHaveProperty("id");
    expect(await screen.findByTestId("run-panel")).toBeInTheDocument();                           // saved: it can be run now
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(net.calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("runs a set against an agent, follows it until it finishes, and shows each case", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const net = setup();
    render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    const panel = await screen.findByTestId("run-panel");
    const start = within(panel).getByRole("button", { name: "Start run" });
    expect(start).toBeDisabled();
    await userEvent.selectOptions(within(panel).getByLabelText("Agent"), "assistant");
    await userEvent.selectOptions(within(panel).getByLabelText("Model provider"), "mock");
    await userEvent.type(within(panel).getByLabelText(/Model \(optional\)/), "mock-cheap");
    await userEvent.selectOptions(within(panel).getByLabelText(/Judge model/), "mock");
    await userEvent.click(start);
    await waitFor(() => expect(sent(net.calls, "POST", "/api/evals/runs")).toEqual([{ set_id: "smoke", agent: "assistant", provider: "mock", model: "mock-cheap", judge: { provider: "mock" } }]));
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Stop" })).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(1600);
    const result = await screen.findByTestId("run-result");
    await waitFor(() => expect(within(result).getByText("done")).toBeInTheDocument());
    expect(within(result).getByText(/2\/2 cases · 50% passed · 1 passed, 0 failed, 1 errors · \$0.0123/)).toBeInTheDocument();
    expect(within(result).getByText("passed")).toBeInTheDocument();
    expect(within(result).getByText("error")).toBeInTheDocument();
    expect(within(result).getByText("failed")).toBeInTheDocument();
    expect(within(result).getByText("RuntimeError: provider down")).toBeInTheDocument();
    expect(within(result).getByText(/stopped by a guardrail/)).toBeInTheDocument();
    expect(within(result).getByText(/equals the expected text/)).toBeInTheDocument();
    expect(within(result).getByRole("link", { name: "Export for csv" })).toHaveAttribute("href", "http://127.0.0.1:8765/api/evals/export?kind=run&id=r2&format=csv");
    expect(within(panel).getByRole("link", { name: "Export for promptfoo" })).toHaveAttribute("href", "http://127.0.0.1:8765/api/evals/export?kind=set&id=smoke&format=promptfoo");
    expect(within(panel).queryByRole("button", { name: "Stop" })).toBeNull();
    await waitFor(() => expect(net.calls.filter((c) => c.url.pathname === "/api/evals/runs" && c.url.search === "?set_id=smoke").length).toBeGreaterThan(1));   // the history refreshes too
  });

  it("keeps waiting when a progress check fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const net = setup({ "GET /api/evals/runs/r2": () => jsonResponse({ detail: "busy" }, { status: 503 }) });
    render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    const panel = await screen.findByTestId("run-panel");
    await userEvent.selectOptions(within(panel).getByLabelText("Agent"), "assistant");
    await userEvent.selectOptions(within(panel).getByLabelText("Model provider"), "mock");
    await userEvent.click(within(panel).getByRole("button", { name: "Start run" }));
    await vi.advanceTimersByTimeAsync(1600);
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/evals/runs/r2")).toBe(true));
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("can stop a running evaluation, and shows when it stopped on a spend limit", async () => {
    const net = setup({ "GET /api/evals/runs/r2": () => jsonResponse({ ...RUNNING }) });
    render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    const panel = await screen.findByTestId("run-panel");
    await userEvent.selectOptions(within(panel).getByLabelText("Agent"), "assistant");
    await userEvent.selectOptions(within(panel).getByLabelText("Model provider"), "mock");
    await userEvent.click(within(panel).getByRole("button", { name: "Start run" }));
    await userEvent.click(await within(panel).findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/evals/runs/r2/cancel")).toBe(true));
    expect(await screen.findByText("cancelled")).toBeInTheDocument();
    vi.unstubAllGlobals();
    setup({ "GET /api/evals/runs": () => jsonResponse({ runs: [{ ...DONE, id: "r1", status: "error", stopped: "the spend cap blocked a model call", results: undefined }] }),
      "GET /api/evals/runs/r1": () => jsonResponse({ ...DONE, id: "r1", status: "error", stopped: "the spend cap blocked a model call", results: undefined }) });
    const again = render(<EvalsPage />);
    await userEvent.click((await within(again.container).findAllByText("Smoke")).at(-1)!);
    await userEvent.click((await within(again.container).findAllByRole("button", { name: "Open" })).at(-1)!);
    expect(await within(again.container).findByText(/Stopped: the spend cap blocked a model call/)).toBeInTheDocument();
  });

  it("opens an earlier run from the history", async () => {
    setup();
    render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    expect(await screen.findByTestId("run-result")).toBeInTheDocument();
    expect(screen.getByText(/Past runs/)).toBeInTheDocument();
  });

  it("shows the server's reasons for failures", async () => {
    setup({ "PUT /api/evals/sets": () => jsonResponse({ detail: "case 1: write a prompt" }, { status: 422 }), "POST /api/evals/runs": () => jsonResponse({ detail: "another evaluation is still running" }, { status: 422 }),
      "DELETE /api/evals/sets/smoke": () => jsonResponse({ detail: "not found" }, { status: 404 }), "GET /api/evals/runs/r1": () => jsonResponse({ detail: "gone" }, { status: 404 }) });
    render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    expect(await screen.findByText("case 1: write a prompt")).toBeInTheDocument();
    const panel = await screen.findByTestId("run-panel");
    await userEvent.selectOptions(within(panel).getByLabelText("Agent"), "assistant");
    await userEvent.selectOptions(within(panel).getByLabelText("Model provider"), "mock");
    await userEvent.click(within(panel).getByRole("button", { name: "Start run" }));
    expect(await screen.findByText("another evaluation is still running")).toBeInTheDocument();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("not found")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    expect(await screen.findByText("gone")).toBeInTheDocument();
  });

  it("reports when a set or the list cannot be loaded", async () => {
    setup({ "GET /api/evals/sets/smoke": () => jsonResponse({ detail: "set is gone" }, { status: 404 }) });
    const { unmount } = render(<EvalsPage />);
    await userEvent.click(await screen.findByText("Smoke"));
    expect(await screen.findByText("set is gone")).toBeInTheDocument();
    unmount();
    vi.unstubAllGlobals();
    routeFetch({ "GET /api/evals/sets": () => jsonResponse({ detail: "offline" }, { status: 500 }), "GET /api/agents": () => jsonResponse({}, { status: 500 }), "GET /api/providers": () => jsonResponse({}, { status: 500 }) });
    render(<EvalsPage />);
    expect(await screen.findByText("offline")).toBeInTheDocument();
    expect(screen.getByText("None yet.")).toBeInTheDocument();
  });
});
