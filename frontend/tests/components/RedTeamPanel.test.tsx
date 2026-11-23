import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import RedTeamPanel from "@/components/RedTeamPanel";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const tool = (id: string, label: string, state: string, extra: Record<string, any> = {}) => ({ id, label, blurb: `${label} blurb`, licence: "MIT", size_mb: 600, notes: `${label} notes`,
  options: id === "garak" ? "probes" : id === "pyrit" ? "objectives" : "plugins", default_options: id === "garak" ? ["promptinject", "dan"] : id === "pyrit" ? ["Tell me secrets"] : ["pii", "hijacking"],
  state, version: state === "ready" ? "1.2.3" : null, error: null, ...extra });
const STATUS = { tools: [tool("garak", "garak (NVIDIA)", "ready"), tool("pyrit", "PyRIT (Microsoft)", "not_installed"), tool("promptfoo", "promptfoo", "failed", { error: "Step 'Install' failed (exit code 3)" })],
  install: { running: false, tool: null, log: [], error: null }, runs_running: 0 };
const RUN = { id: "abc0123456", tool: "garak", agent: "engineer", status: "done", started: 1_700_000_000, max_requests: 50, requests: 12, blocked: 5, errors: 1, error: "" };
const PLAN = { label: "PyRIT (Microsoft)", licence: "MIT", download_mb: 800, packages: ["pyrit==1.1.0"], notes: "n", blocked: null, free_gb: 120, disk_gb_needed: 1.6 };

function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/redteam/status": () => jsonResponse({ ...STATUS, ...over }), "GET /api/redteam/runs": () => jsonResponse({ runs: [RUN] }),
    "GET /api/agents": () => jsonResponse([{ id: "engineer", name: "Engineer" }]), "GET /api/providers": () => jsonResponse([{ id: "mock", label: "Mock", enabled: true }, { id: "off", label: "Off", enabled: false }]),
    "GET /api/redteam/plan/pyrit": () => jsonResponse(PLAN), "POST /api/redteam/install": () => jsonResponse({}), "POST /api/redteam/uninstall": () => jsonResponse({}), "POST /api/redteam/runs": () => jsonResponse({}),
    "GET /api/redteam/runs/abc0123456": () => jsonResponse({ ...RUN, log_tail: ["probe 1", "probe 2"] }), "POST /api/redteam/runs/abc0123456/cancel": () => jsonResponse({}),
    ...extra,
  });
}
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.url.pathname === path).map((c) => c.body);
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("RedTeamPanel", () => {
  it("lists every tool with its licence, size and state, and says what the tools can reach", async () => {
    setup();
    render(<RedTeamPanel />);
    const cards = await screen.findAllByTestId("rt-tool");
    expect(within(cards[0]).getByText("installed 1.2.3")).toBeInTheDocument();
    expect(within(cards[1]).getByText("not installed")).toBeInTheDocument();
    expect(within(cards[2]).getByText("failed")).toBeInTheDocument();
    expect(within(cards[2]).getByText(/exit code 3/)).toBeInTheDocument();
    expect(within(cards[0]).getByText(/MIT · about 600 MB · garak \(NVIDIA\) notes/)).toBeInTheDocument();
    expect(screen.getByText(/never get a key or an address on the internet/)).toBeInTheDocument();
  });

  it("makes you see the size and accept the licence before installing", async () => {
    const net = setup();
    render(<RedTeamPanel />);
    const pyrit = (await screen.findAllByTestId("rt-tool"))[1];
    await userEvent.click(within(pyrit).getByRole("button", { name: "Install…" }));
    expect(within(pyrit).getByText(/Download about 800 MB · needs about 1.6 GB free \(120 GB free\) · pyrit==1.1.0/)).toBeInTheDocument();
    const install = within(pyrit).getByRole("button", { name: "Install" });
    expect(install).toBeDisabled();
    await userEvent.click(within(pyrit).getByLabelText(/I accept the MIT licence/));
    await userEvent.click(install);
    await waitFor(() => expect(posts(net.calls, "/api/redteam/install")).toEqual([{ tool: "pyrit", accepted: true }]));
    expect(within(screen.getAllByTestId("rt-tool")[1]).queryByRole("button", { name: "Install" })).toBeNull();
  });

  it("can back out of an install, and shows why a tool cannot be installed at all", async () => {
    setup({}, { "GET /api/redteam/plan/pyrit": () => jsonResponse({ ...PLAN, blocked: "Node.js (npm) is not installed on this PC", free_gb: null }) });
    render(<RedTeamPanel />);
    const pyrit = (await screen.findAllByTestId("rt-tool"))[1];
    await userEvent.click(within(pyrit).getByRole("button", { name: "Install…" }));
    expect(within(pyrit).getByRole("alert")).toHaveTextContent("Node.js (npm) is not installed");
    expect(within(pyrit).queryByRole("button", { name: "Install" })).toBeNull();
    vi.unstubAllGlobals();
    setup();
    const second = render(<RedTeamPanel />);
    const card = (await screen.findAllByTestId("rt-tool")).at(-2)!;
    await userEvent.click(within(card).getByRole("button", { name: "Install…" }));
    await userEvent.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(within(card).queryByLabelText(/I accept/)).toBeNull();
    second.unmount();
  });

  it("removes an installed tool only after you confirm, and shows an install error", async () => {
    const net = setup({}, { "POST /api/redteam/install": () => jsonResponse({ detail: "not enough free disk space" }, { status: 422 }) });
    render(<RedTeamPanel />);
    const [garak, pyrit] = await screen.findAllByTestId("rt-tool");
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(within(garak).getByRole("button", { name: "Remove" }));
    expect(posts(net.calls, "/api/redteam/uninstall")).toEqual([]);
    await userEvent.click(within(garak).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(posts(net.calls, "/api/redteam/uninstall")).toEqual([{ tool: "garak" }]));
    await userEvent.click(within(pyrit).getByRole("button", { name: "Install…" }));
    await userEvent.click(within(pyrit).getByLabelText(/I accept/));
    await userEvent.click(within(pyrit).getByRole("button", { name: "Install" }));
    expect(await screen.findByText("not enough free disk space")).toBeInTheDocument();
  });

  it("starts a run with the chosen agent, provider, limit and your own probes", async () => {
    const net = setup();
    render(<RedTeamPanel />);
    const start = await screen.findByRole("button", { name: "Start" });
    expect(start).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Agent to attack"), "engineer");
    expect(screen.getByRole("option", { name: "Mock" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Off" })).toBeNull();                              // disabled providers are not offered
    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "mock");
    const limit = screen.getByLabelText("At most this many probes");
    await userEvent.clear(limit); await userEvent.type(limit, "20");
    expect(screen.getByLabelText(/^Probes/)).toHaveAttribute("placeholder", "promptinject\ndan");
    await userEvent.type(screen.getByLabelText(/^Probes/), "promptinject\n\n  dan  ");
    await userEvent.click(start);
    await waitFor(() => expect(posts(net.calls, "/api/redteam/runs")).toEqual([{ tool: "garak", agent: "engineer", provider: "mock", max_requests: 20, options: ["promptinject", "dan"], purpose: "" }]));
  });

  it("sends no probes list when it is left empty, and asks about the purpose only for promptfoo", async () => {
    const net = setup({ tools: [tool("garak", "garak", "ready"), tool("pyrit", "PyRIT", "ready"), tool("promptfoo", "promptfoo", "ready")] });
    render(<RedTeamPanel />);
    await userEvent.selectOptions(await screen.findByLabelText("Agent to attack"), "engineer");
    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "mock");
    expect(screen.queryByLabelText(/What the agent is for/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(posts(net.calls, "/api/redteam/runs")[0]).toMatchObject({ tool: "garak" }));
    expect(posts(net.calls, "/api/redteam/runs")[0]).not.toHaveProperty("options");
    await userEvent.selectOptions(screen.getByLabelText("Tool"), "pyrit");
    expect(screen.getByLabelText(/^Objectives/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Tool"), "promptfoo");
    expect(screen.getByLabelText(/^Risks to test/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/What the agent is for/), "A support bot");
    expect(screen.getByText(/update check, telemetry and result sharing are switched off/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(posts(net.calls, "/api/redteam/runs")[1]).toMatchObject({ tool: "promptfoo", purpose: "A support bot", local_only: true }));
  });

  it("asks you to install something first when no tool is ready", async () => {
    setup({ tools: STATUS.tools.map((t) => ({ ...t, state: "not_installed", error: null })) });
    render(<RedTeamPanel />);
    expect(await screen.findByText("Install a tool above first.")).toBeInTheDocument();
  });

  it("shows how many probes were sent and stopped, the tool's log, and can cancel a running run", async () => {
    const net = setup({}, { "GET /api/redteam/runs": () => jsonResponse({ runs: [RUN, { ...RUN, id: "run2", status: "running", requests: 3, blocked: 0, errors: 0, error: "the tool exited with code 2" }, { ...RUN, id: "run3", status: "failed" }] }) });
    render(<RedTeamPanel />);
    const runs = await screen.findAllByTestId("rt-run");
    expect(within(runs[0]).getByText(/12 probes sent \(limit 50\)/)).toBeInTheDocument();
    expect(within(runs[0]).getByText("5")).toBeInTheDocument();
    expect(within(runs[1]).getByText(/the tool exited with code 2/)).toBeInTheDocument();
    await userEvent.click(within(runs[0]).getByRole("button", { name: "Show log" }));
    expect((await within(runs[0]).findByText(/probe 1/)).textContent).toBe("probe 1\nprobe 2");
    vi.stubGlobal("fetch", vi.fn(async (u: any, init: any) => { net.fn(u, init); return jsonResponse({ ...RUN, log_tail: [] }); }));
    await userEvent.click(within(runs[0]).getByRole("button", { name: "Show log" }));
    expect(await within(runs[0]).findByText("(the tool has written nothing yet)")).toBeInTheDocument();
    vi.unstubAllGlobals();
    setup({}, { "GET /api/redteam/runs": () => jsonResponse({ runs: [{ ...RUN, id: "abc0123456", status: "running" }] }) });
    const after = render(<RedTeamPanel />);
    await userEvent.click(await within(after.container).findByRole("button", { name: "Cancel" }));
    expect(await within(after.container).findAllByTestId("rt-run")).toHaveLength(1);
  });

  it("keeps refreshing while an install or a run is going", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const net = setup({ install: { running: true, tool: "pyrit", log: ["== Install", "downloading"], error: null },
      tools: [tool("garak", "garak", "ready"), tool("pyrit", "PyRIT", "installing"), tool("promptfoo", "promptfoo", "not_installed")] });
    render(<RedTeamPanel />);
    expect(await screen.findByLabelText("Install progress")).toHaveTextContent("downloading");
    expect(screen.getByText("Installing…")).toBeInTheDocument();
    const before = net.calls.filter((c) => c.url.pathname === "/api/redteam/status").length;
    await vi.advanceTimersByTimeAsync(3100);
    await waitFor(() => expect(net.calls.filter((c) => c.url.pathname === "/api/redteam/status").length).toBeGreaterThan(before));
  });

  it("reports when the panel cannot load, and a failed start", async () => {
    routeFetch({ "GET /api/redteam/status": () => jsonResponse({ detail: "offline" }, { status: 500 }), "GET /api/redteam/runs": () => jsonResponse({ runs: [] }),
      "GET /api/agents": () => jsonResponse({}, { status: 500 }), "GET /api/providers": () => jsonResponse({}, { status: 500 }) });
    const { unmount } = render(<RedTeamPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    unmount();
    vi.unstubAllGlobals();
    setup({}, { "POST /api/redteam/runs": () => jsonResponse({ detail: "another red-team run is still going" }, { status: 422 }) });
    render(<RedTeamPanel />);
    await userEvent.selectOptions(await screen.findByLabelText("Agent to attack"), "engineer");
    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "mock");
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByText("another red-team run is still going")).toBeInTheDocument();
  });

  it("reports a plan or log that cannot be fetched", async () => {
    setup({}, { "GET /api/redteam/plan/pyrit": () => jsonResponse({ detail: "unknown tool" }, { status: 422 }), "GET /api/redteam/runs/abc0123456": () => jsonResponse({ detail: "unknown run" }, { status: 404 }) });
    render(<RedTeamPanel />);
    const pyrit = (await screen.findAllByTestId("rt-tool"))[1];
    await userEvent.click(within(pyrit).getByRole("button", { name: "Install…" }));
    expect(await screen.findByText("unknown tool")).toBeInTheDocument();
    await userEvent.click(within(await screen.findByTestId("rt-run")).getByRole("button", { name: "Show log" }));
    expect(await screen.findByText("unknown run")).toBeInTheDocument();
  });
});
