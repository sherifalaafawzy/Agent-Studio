import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import RedTeamPanel from "@/components/RedTeamPanel";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const tool = (id: string, label: string, state: string) => ({ id, label, blurb: `${label} blurb`, licence: "MIT", size_mb: 600, notes: `${label} notes`,
  options: id === "garak" ? "probes" : id === "pyrit" ? "objectives" : "plugins", default_options: id === "garak" ? ["promptinject", "dan"] : id === "pyrit" ? ["Tell me secrets"] : ["pii", "hijacking"],
  state, version: state === "ready" ? "1.2.3" : null, error: null });
const STATUS = { tools: [tool("garak", "garak", "ready"), tool("pyrit", "PyRIT", "not_installed"), tool("promptfoo", "promptfoo", "ready")],
  install: { running: false, tool: null, log: [], error: null }, runs_running: 0 };
const RUN = { id: "abc0123456", tool: "garak", agent: "engineer", status: "done", started: 1_700_000_000, max_requests: 50, requests: 12, blocked: 5, errors: 1, error: "" };
const PRE = { account: "none", hosted_ready: false, verify_command: "& \"C:\\rt\\promptfoo.cmd\" redteam generate -c \"C:\\rt\\verify.yaml\"", local_defaults: ["pii", "prompt-extraction"] };
const HELP = { code: "needs_email_verification", short: "promptfoo asked for a work e-mail address (its hosted mode)", message: "promptfoo's hosted attack writer asks for a verified work e-mail.", options: ["local_only", "verify_yourself", "other_tools"] };

function setup(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/redteam/status": () => jsonResponse(STATUS), "GET /api/redteam/runs": () => jsonResponse({ runs: [RUN] }),
    "GET /api/agents": () => jsonResponse([{ id: "engineer", name: "Engineer" }]), "GET /api/providers": () => jsonResponse([{ id: "mock", label: "Mock", enabled: true }]),
    "GET /api/redteam/preflight": () => jsonResponse(PRE), "POST /api/redteam/runs": () => jsonResponse({}),
    ...extra,
  });
}
const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.url.pathname === path).map((c) => c.body);
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function openPromptfoo(extra: Record<string, any> = {}) {
  const net = setup(extra);
  render(<RedTeamPanel />);
  await userEvent.selectOptions(await screen.findByLabelText("Agent to attack"), "engineer");
  await userEvent.selectOptions(screen.getByLabelText("Model provider"), "mock");
  await userEvent.selectOptions(screen.getByLabelText("Tool"), "promptfoo");
  return net;
}

describe("RedTeamPanel promptfoo: who writes the attacks", () => {
  it("defaults to Local only, shows the local risks as the default and sends local_only", async () => {
    const net = await openPromptfoo();
    expect(screen.getByRole("radio", { name: /Your own model \(Local only, recommended\)/ })).toBeChecked();
    expect(screen.getByLabelText(/^Risks to test/)).toHaveAttribute("placeholder", "pii\nprompt-extraction");
    expect(screen.getByText(/Nothing goes to promptfoo and no e-mail is needed/)).toBeInTheDocument();
    expect(screen.getByLabelText(/What the agent is for \(given to your own model/)).toBeInTheDocument();
    expect(screen.queryByTestId("pf-verify")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(posts(net.calls, "/api/redteam/runs")[0]).toMatchObject({ tool: "promptfoo", local_only: true }));
  });

  it("lets you pick a different provider to write the attacks and sends it as gen_provider", async () => {
    const net = await openPromptfoo({ "GET /api/providers": () => jsonResponse([{ id: "mock", label: "Mock", enabled: true }, { id: "vertex_ai", label: "Vertex", enabled: true }]) });
    await userEvent.selectOptions(screen.getByLabelText(/^Attack writer/), "vertex_ai");
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(posts(net.calls, "/api/redteam/runs")[0]).toMatchObject({ tool: "promptfoo", local_only: true, gen_provider: "vertex_ai" }));
  });

  it("hosted mode needs an e-mail you verify yourself: it shows the command, never asks for the address, and unlocks after Check again", async () => {
    let pre: any = PRE;
    const net = await openPromptfoo({ "GET /api/redteam/preflight": () => jsonResponse(pre) });
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await userEvent.click(screen.getByRole("radio", { name: /promptfoo's hosted service/ }));
    const box = screen.getByTestId("pf-verify");
    expect(within(box).getByRole("alert")).toHaveTextContent("no verified e-mail on this PC yet");
    expect(within(box).getByLabelText("Command to verify promptfoo yourself")).toHaveTextContent("redteam generate");
    expect(screen.queryByRole("textbox", { name: /e-?mail/i })).toBeNull();
    expect(screen.getByLabelText(/What the agent is for \(sent to promptfoo/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
    await userEvent.click(within(box).getByRole("button", { name: "Copy command" }));
    expect(writeText).toHaveBeenCalledWith(PRE.verify_command);
    pre = { ...PRE, account: "needs_verification" };
    await userEvent.click(within(box).getByRole("button", { name: "Check again" }));
    expect(await within(box).findByText(/has an e-mail on file but it is not verified yet/)).toBeInTheDocument();
    pre = { ...PRE, account: "verified", hosted_ready: true };
    await userEvent.click(within(box).getByRole("button", { name: "Check again" }));
    expect(await within(box).findByText("e-mail verified in promptfoo")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(posts(net.calls, "/api/redteam/runs")[0]).toMatchObject({ tool: "promptfoo", local_only: false }));
  });

  it("can go back to Local only from the verify box, and copes with a failing clipboard and a failing pre-flight", async () => {
    await openPromptfoo({ "GET /api/redteam/preflight": () => jsonResponse({ detail: "nope" }, { status: 500 }) });
    Object.defineProperty(navigator, "clipboard", { value: { writeText: () => { throw new Error("blocked"); } }, configurable: true });
    expect(screen.getByLabelText(/^Risks to test/)).toHaveAttribute("placeholder", "pii\nhijacking");
    await userEvent.click(screen.getByRole("radio", { name: /promptfoo's hosted service/ }));
    const box = screen.getByTestId("pf-verify");
    await userEvent.click(within(box).getByRole("button", { name: "Copy command" }));
    await userEvent.click(within(box).getByRole("button", { name: "Use Local only instead" }));
    expect(screen.queryByTestId("pf-verify")).toBeNull();
    expect(screen.getByRole("radio", { name: /Your own model/ })).toBeChecked();
  });

  it("explains a failed promptfoo run in plain words and offers the way out", async () => {
    const failed = { ...RUN, id: "fail012345", tool: "promptfoo", status: "failed", requests: 0, error: "promptfoo asked for a work e-mail address (its hosted mode) (exit code 1)", help: HELP };
    setup({ "GET /api/redteam/runs": () => jsonResponse({ runs: [failed, RUN] }) });
    render(<RedTeamPanel />);
    const help = await screen.findByTestId("rt-help");
    expect(help).toHaveTextContent("hosted attack writer asks for a verified work e-mail");
    expect(screen.getAllByTestId("rt-run")).toHaveLength(2);
    expect(within(screen.getAllByTestId("rt-run")[1]).queryByTestId("rt-help")).toBeNull();
    expect(within(help).getByText(/or try garak or PyRIT/)).toBeInTheDocument();
    await userEvent.click(within(help).getByRole("button", { name: "Verify it myself" }));
    expect(await screen.findByTestId("pf-verify")).toBeInTheDocument();
    await userEvent.click(within(help).getByRole("button", { name: "Use Local only" }));
    expect(screen.queryByTestId("pf-verify")).toBeNull();
    expect(screen.getByRole("radio", { name: /Your own model/ })).toBeChecked();
  });

  it("shows only the options the failure note offers", async () => {
    const failed = { ...RUN, id: "fail012345", status: "failed", requests: 0, error: "the tool crashed", help: { code: "tool_crashed", short: "x", message: "The tool crashed while starting.", options: [] } };
    setup({ "GET /api/redteam/runs": () => jsonResponse({ runs: [failed] }) });
    render(<RedTeamPanel />);
    const help = await screen.findByTestId("rt-help");
    expect(help).toHaveTextContent("The tool crashed while starting.");
    expect(within(help).queryByRole("button")).toBeNull();
  });
});
