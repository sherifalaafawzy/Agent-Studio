import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentInstaller, { shortVersion } from "@/components/AgentInstaller";
import { jsonResponse, routeFetch } from "../helpers/http";

const NPM = { kind: "npm", label: "Install with npm (version 1.0.0, public since 2026-10-01)", command: "npm install -g @openai/codex@1.0.0", safe: true };
const SCRIPT = { kind: "script", label: "Run the vendor's own installer script", command: "irm https://cursor.com/install | iex", safe: false, warning: "Only continue if you trust the vendor." };
const agent = (over: Record<string, unknown> = {}) => ({
  id: "codex", label: "OpenAI Codex", installed: false, path: null, version: null, signin: "Run `codex login` once.", cost: "Your ChatGPT plan. Not covered by Agent Studio's spend limits.",
  note: "Uses the ACP adapter.", manual: "", registry_extra: false, methods: [NPM], ...over,
});
const CAT = {
  registry: { source: "live", fetched_at: 1 },
  agents: [
    agent({ id: "claude", label: "Claude Code", installed: true, version: "2.1.170 (Claude Code)", methods: [{ ...NPM, command: "npm install -g @anthropic-ai/claude-code@2.1.319", label: "Install with npm (version 2.1.319)" }] }),
    agent(),
    agent({ id: "cursor", label: "Cursor", note: "", methods: [SCRIPT] }),
    agent({ id: "openhands", label: "OpenHands", note: "", manual: "OpenHands needs WSL.", methods: [{ kind: "manual", label: "Manual setup", command: "", safe: true, text: "OpenHands needs WSL." }] }),
    agent({ id: "grok", label: "Grok Build", note: "", methods: [{ kind: "manual", label: "Manual setup", command: "", safe: true, text: "No one-click method is available on this PC." }] }),
    agent({ id: "opencode", label: "OpenCode", registry_extra: true, note: "Open coding agent", methods: [{ kind: "archive", label: "Download OpenCode 2.3.2 and check its checksum", command: "download https://x and verify", safe: true }] }),
  ],
};
const idleJob = { state: "idle", agent: "", action: "", log: [] as string[], error: "", running: false };

function setup(routes: Record<string, any> = {}, props: Record<string, unknown> = {}) {
  const onChange = vi.fn();
  const r = routeFetch({ "GET /api/easy/agents": () => jsonResponse(CAT), ...routes });
  render(<AgentInstaller onChange={onChange} {...props} />);
  return { ...r, onChange };
}
const card = (name: string) => screen.getByText(name, { selector: "b" }).closest(".ai-card") as HTMLElement;

afterEach(() => vi.restoreAllMocks());

describe("shortVersion", () => {
  it("keeps just the version number", () => {
    expect(shortVersion("2.1.170 (Claude Code)")).toBe("2.1.170");
    expect(shortVersion("grok 1.0.92 (b06756fd6256) [stable]")).toBe("1.0.92");
    expect(shortVersion("2026.11.19-f005e9d")).toBe("2026.11.19-f005e9d");
    expect(shortVersion("a development build with no number at all")).toBe("a development build with no ");
  });
});

describe("AgentInstaller: the list", () => {
  it("shows each agent's state, who pays, and how to sign in", async () => {
    const { onChange } = setup();
    expect(screen.getByText(/the first check takes a few seconds/)).toBeInTheDocument();
    expect(await screen.findByText("Claude Code")).toBeInTheDocument();
    expect(within(card("Claude Code")).getByText("installed · 2.1.170")).toBeInTheDocument();
    expect(within(card("OpenAI Codex")).getByText("not installed")).toBeInTheDocument();
    expect(within(card("OpenAI Codex")).getByText(/Not covered by Agent Studio's spend limits/)).toBeInTheDocument();
    expect(within(card("OpenAI Codex")).getByText("Run `codex login` once.")).toBeInTheDocument();
    expect(within(card("OpenAI Codex")).getByText("npm install -g @openai/codex@1.0.0")).toBeInTheDocument();
    expect(within(card("Claude Code")).getByRole("button", { name: /Reinstall: Install with npm/ })).toBeInTheDocument();
    expect(within(card("OpenAI Codex")).getByRole("button", { name: /^Install: Install with npm/ })).toBeInTheDocument();
    expect(onChange).toHaveBeenCalledWith(1);
  });

  it("manual-only agents show their instructions and no install button", async () => {
    setup();
    await screen.findByText("OpenHands");
    expect(within(card("OpenHands")).getByText("OpenHands needs WSL.")).toBeInTheDocument();
    expect(within(card("OpenHands")).queryByRole("button")).toBeNull();
    expect(within(card("Grok Build")).getByText("No one-click method is available on this PC.")).toBeInTheDocument();
  });

  it("lists the rest of the official registry on request, and can refresh it", async () => {
    const { calls } = setup({ "POST /api/easy/agents/registry/refresh": () => jsonResponse({ source: "live", count: 46 }) });
    await screen.findByText("Claude Code");
    expect(screen.getByText("More agents from the official registry (1)")).toBeInTheDocument();
    expect(within(card("OpenCode")).getByText("Open coding agent")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh the list" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/easy/agents/registry/refresh")).toBe(true));
    expect(screen.getByText(/Source: live/)).toBeInTheDocument();
  });

  it("can be limited to a few agents, without the registry list", async () => {
    setup({}, { only: ["codex"] });
    await screen.findByText("OpenAI Codex");
    expect(screen.queryByText("Claude Code")).toBeNull();
    expect(screen.queryByText(/More agents from the official registry/)).toBeNull();
  });

  it("explains a failed load and a failed registry refresh", async () => {
    routeFetch({ "GET /api/easy/agents": () => jsonResponse({ detail: "npm is broken" }, { status: 500 }) });
    render(<AgentInstaller />);
    expect(await screen.findByText("npm is broken")).toBeInTheDocument();
    setup({ "POST /api/easy/agents/registry/refresh": () => jsonResponse({ detail: "cdn down" }, { status: 502 }) });
    await userEvent.click(await screen.findByRole("button", { name: "Refresh the list" }));
    expect(await screen.findByText("cdn down")).toBeInTheDocument();
  });
});

describe("AgentInstaller: installing", () => {
  it("runs a safe install, follows it to the end, then refreshes the list", async () => {
    let installed = false;
    const { calls } = setup({
      "GET /api/easy/agents": () => jsonResponse({ ...CAT, agents: CAT.agents.map((a) => (a.id === "codex" ? { ...a, installed, version: installed ? "0.171.0" : null } : a)) }),
      "POST /api/easy/agents/install": () => jsonResponse({ ...idleJob, state: "running", agent: "codex", action: "install", running: true, log: ["$ npm install -g @openai/codex@1.0.0"] }),
      "GET /api/easy/agents/status": () => { installed = true; return jsonResponse({ ...idleJob, state: "done", agent: "codex", action: "install", log: ["$ npm install -g @openai/codex@1.0.0", "added 1 package"] }); },
    });
    await userEvent.click(await screen.findByRole("button", { name: /^Install: Install with npm \(version 1\.0\.0/ }));
    expect(calls.find((c) => c.url.pathname === "/api/easy/agents/install")!.body).toEqual({ agent: "codex", method: "npm", confirm_command: "" });
    expect(await within(card("OpenAI Codex")).findByText("working…")).toBeInTheDocument();
    expect(await within(card("OpenAI Codex")).findByText("Done.", {}, { timeout: 4500 })).toBeInTheDocument();
    await waitFor(() => expect(within(card("OpenAI Codex")).getByText(/installed · 0.171.0/)).toBeInTheDocument());
    await userEvent.click(within(card("OpenAI Codex")).getByText("Details"));
    expect(within(card("OpenAI Codex")).getByText(/added 1 package/)).toBeInTheDocument();
  });

  it("shows why an install failed, and only on that agent's card", async () => {
    setup({
      "POST /api/easy/agents/install": () => jsonResponse({ ...idleJob, state: "running", agent: "codex", action: "install", running: true }),
      "GET /api/easy/agents/status": () => jsonResponse({ ...idleJob, state: "failed", agent: "codex", action: "install", error: "The installer stopped with code 1.", log: ["npm ERR!"] }),
    });
    await userEvent.click(await screen.findByRole("button", { name: /^Install: Install with npm/ }));
    expect(await within(card("OpenAI Codex")).findByText("The installer stopped with code 1.", {}, { timeout: 4500 })).toBeInTheDocument();
    expect(within(card("Claude Code")).queryByText("The installer stopped with code 1.")).toBeNull();
  });

  it("shows a refusal from the server", async () => {
    setup({ "POST /api/easy/agents/install": () => jsonResponse({ detail: "another install is already running" }, { status: 409 }) });
    await userEvent.click(await screen.findByRole("button", { name: /^Install: Install with npm/ }));
    expect(await screen.findByText("another install is already running")).toBeInTheDocument();
  });

  it("a vendor script needs a visible command, a ticked box, and then runs exactly that command", async () => {
    const { calls } = setup({ "POST /api/easy/agents/install": () => jsonResponse({ ...idleJob, state: "running", agent: "cursor", action: "install", running: true }),
      "GET /api/easy/agents/status": () => jsonResponse({ ...idleJob, state: "done", agent: "cursor", action: "install" }) });
    const open = await screen.findByRole("button", { name: /Install: Run the vendor's own installer script/ });
    await userEvent.click(open);
    const c = card("Cursor");
    expect(within(c).getByText(/This runs a script from the internet on your PC/)).toBeInTheDocument();
    expect(within(c).getByText("irm https://cursor.com/install | iex", { selector: "pre" })).toBeInTheDocument();
    const run = within(c).getByRole("button", { name: "Run it" });
    expect(run).toBeDisabled();
    await userEvent.click(within(c).getByRole("checkbox"));
    await userEvent.click(run);
    expect(calls.find((x) => x.url.pathname === "/api/easy/agents/install")!.body).toEqual({ agent: "cursor", method: "script", confirm_command: "irm https://cursor.com/install | iex" });
    await waitFor(() => expect(within(card("Cursor")).queryByText(/This runs a script/)).toBeNull());                // the panel closes and the box is cleared
  });

  it("the script panel can be closed again, and the command copied to run by hand", async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    const { calls } = setup();
    const open = await screen.findByRole("button", { name: /Install: Run the vendor's own installer script/ });
    await userEvent.click(open);
    await userEvent.click(screen.getByRole("button", { name: "Copy the command to run it yourself" }));
    expect(write).toHaveBeenCalledWith("irm https://cursor.com/install | iex");
    await userEvent.click(open);
    expect(screen.queryByText(/This runs a script/)).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("copying works even when the browser has no clipboard", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /Install: Run the vendor's own installer script/ }));
    await userEvent.click(screen.getByRole("button", { name: "Copy the command to run it yourself" }));
    expect(screen.getByText(/This runs a script/)).toBeInTheDocument();
  });
});

describe("AgentInstaller: removing", () => {
  it("removes an installed agent and reports it", async () => {
    const { calls } = setup({
      "POST /api/easy/agents/uninstall": () => jsonResponse({ ...idleJob, state: "running", agent: "claude", action: "uninstall", running: true }),
      "GET /api/easy/agents/status": () => jsonResponse({ ...idleJob, state: "done", agent: "claude", action: "uninstall" }),
    });
    await userEvent.click(await within(await screen.findByText("Claude Code").then(() => card("Claude Code"))).findByRole("button", { name: "Remove" }));
    expect(calls.find((c) => c.url.pathname === "/api/easy/agents/uninstall")!.body).toEqual({ agent: "claude" });
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/easy/agents").length).toBeGreaterThanOrEqual(2), { timeout: 4500 });
  });

  it("explains agents it cannot remove, and failed removals show up as failures", async () => {
    setup({ "POST /api/easy/agents/uninstall": () => jsonResponse({ detail: "Agent Studio can only remove agents it installed with npm or uv." }, { status: 409 }) });
    await userEvent.click(await within(await screen.findByText("Claude Code").then(() => card("Claude Code"))).findByRole("button", { name: "Remove" }));
    expect(await screen.findByText(/can only remove agents it installed with npm or uv/)).toBeInTheDocument();
  });

  it("shows a failed removal in the toast and keeps polling through a failed status call", async () => {
    const toasts: { text: string; tone: string }[] = [];
    window.addEventListener("as:toast", (e) => toasts.push((e as CustomEvent).detail));
    let n = 0;
    setup({
      "POST /api/easy/agents/uninstall": () => jsonResponse({ ...idleJob, state: "running", agent: "claude", action: "uninstall", running: true }),
      "GET /api/easy/agents/status": () => { n++; return n === 1 ? jsonResponse({ detail: "blip" }, { status: 500 }) : jsonResponse({ ...idleJob, state: "failed", agent: "claude", action: "uninstall", error: "The uninstall command failed." }); },
    });
    await userEvent.click(await within(await screen.findByText("Claude Code").then(() => card("Claude Code"))).findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(toasts.some((t) => t.text === "Could not uninstall claude" && t.tone === "err")).toBe(true), { timeout: 6000 });
  });

  it("an installed agent with no version number, and a method with a note, show plainly", async () => {
    setup({ "GET /api/easy/agents": () => jsonResponse({ ...CAT, agents: [agent({ installed: true, version: null, methods: [{ ...NPM, note: "Cannot be pinned to 14 days." }] })] }) });
    expect(await screen.findByText("installed")).toBeInTheDocument();
    expect(screen.getByText(/Cannot be pinned to 14 days\./)).toBeInTheDocument();
  });

  it("keeps polling while a job is still running", async () => {
    let polls = 0;
    setup({
      "POST /api/easy/agents/install": () => jsonResponse({ ...idleJob, state: "running", agent: "codex", action: "install", running: true }),
      "GET /api/easy/agents/status": () => { polls++; return jsonResponse({ ...idleJob, state: polls < 2 ? "running" : "done", agent: "codex", action: "install", running: polls < 2, log: ["x"] }); },
    });
    await userEvent.click(await screen.findByRole("button", { name: /^Install: Install with npm/ }));
    await waitFor(() => expect(polls).toBeGreaterThanOrEqual(2), { timeout: 4500 });
  });
});
