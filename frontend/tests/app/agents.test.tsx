import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/agents/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const engineer = {
  id: "engineer", name: "Engineer", description: "Writes code", instruction: "Be careful", provider: null, tier: "pro", model: null,
  tools: ["read_file"], mcp: [], skills: [], sub_agents: ["reviewer"], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask",
};
const reviewer = {
  id: "reviewer", name: "Reviewer", description: "", instruction: "", provider: "openai", tier: "flash", model: null,
  tools: [], mcp: ["fs"], skills: ["*"], sub_agents: [], builtin: false, can_spawn: true, acp_agents: ["claude_code"], acp_policy: "readonly",
};
const tools = [{ name: "read_file", description: "Read a file", read_only: true }, { name: "write_file", description: "Write a file", read_only: false }];
const mcp = [{ id: "fs", label: "Filesystem", ready: true, enabled: true, status: "ready" }, { id: "git", label: "Git", ready: false, enabled: false, status: "pending_approval" }];
const skills = [{ name: "release", description: "Cut a release" }];
const ext = [{ id: "claude_code", label: "Claude Code", installed: true, enabled: true }, { id: "cursor", label: "Cursor", installed: false, enabled: true }];

function setup(over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/agents": over.agents ?? (() => jsonResponse([engineer, reviewer])),
    "GET /api/tools": () => jsonResponse(tools),
    "GET /api/mcp": () => jsonResponse(mcp),
    "GET /api/skills": () => jsonResponse(skills),
    "GET /api/acp/agents": () => jsonResponse(ext),
    "GET /api/runtimes": () => jsonResponse({ targets: [{ id: "local", label: "This PC", enabled: true, configured: true, remote: false, cost_note: "" }] }),
    "GET /api/sandboxes": () => jsonResponse({ kinds: [{ id: "docker-container", label: "Docker container", available: true, detail: "ready", experimental: false, notice: "" },
      { id: "docker-microvm", label: "Docker Sandbox (micro VM)", available: true, detail: "ready", experimental: true, notice: "Experimental." }] }),
    "GET /api/data/sources": () => jsonResponse({ sources: [] }),
    "PUT /api/agents/engineer": over.put ?? (() => jsonResponse({ ok: true })),
    "POST /api/agents": over.post ?? (() => jsonResponse({ ok: true })),
    "PUT /api/agents/reviewer": () => jsonResponse({ ok: true }),
    "DELETE /api/agents/reviewer": over.del ?? (() => jsonResponse({ ok: true })),
  });
}

const field = (label: RegExp | string) => screen.getByLabelText(label) as HTMLInputElement;
/** The agent list entry in the sidebar (the delegation tree repeats the names, so text queries are ambiguous). */
const pick = (name: string) => [...document.querySelectorAll<HTMLElement>(".ag-side .card > div")].find((d) => d.textContent?.startsWith(name))!;

describe("Agents: directory and delegation tree", () => {
  it("opens on the engineer agent and shows its sub-agents as a tree", async () => {
    setup();
    const { container } = render(<AgentsPage />);
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
    expect(container.querySelector(".ag-side")).toHaveTextContent("Delegation tree");
    expect(container.querySelector(".ag-side")).toHaveTextContent("└ Reviewer");
    expect(screen.getByDisplayValue("Be careful")).toBeInTheDocument();
  });

  it("switches to another agent and shows its settings", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(pick("Reviewer"));
    expect(await screen.findByDisplayValue("Reviewer")).toBeInTheDocument();
    expect(field("Provider")).toHaveValue("openai");
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("reports when the backend is offline", async () => {
    setup({ agents: () => textResponse("x", 500) });
    render(<AgentsPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toBeInTheDocument();
  });

});

describe("Agents: editing", () => {
  it("saves the edited fields to the agent", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    const name = await screen.findByDisplayValue("Engineer");
    await userEvent.clear(name);
    await userEvent.type(name, "Principal Engineer");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const body = calls.find((c) => c.method === "PUT")!.body;
    expect(body).toMatchObject({ id: "engineer", name: "Principal Engineer", tier: "pro", tools: ["read_file"] });
  });

  it("toggles tools, MCP servers and skills", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("checkbox", { name: /write_file/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Filesystem/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "all" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ tools: ["read_file", "write_file"], mcp: ["fs"], skills: ["*"] });
  });

  it("un-ticks 'all' and picks individual skills", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("checkbox", { name: "all" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "all" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /release/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ skills: ["release"] });
  });

  it("sets a custom model, sub-agents, self-spawning and external agents", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.selectOptions(screen.getByLabelText("Model tier"), "custom");
    await userEvent.type(field("Model"), "gpt-x");
    // Reviewer is already a sub-agent of Engineer, so ticking it removes it
    await userEvent.click(screen.getByRole("checkbox", { name: "Reviewer" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Allow this agent to build/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Claude Code/ }));
    await userEvent.selectOptions(screen.getByLabelText(/Their permission requests/), "reads_auto");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({
      tier: "custom", model: "gpt-x", sub_agents: [], can_spawn: true, acp_agents: ["claude_code"], acp_policy: "reads_auto",
    });
  });

  it("sets the provider and description, and clears the provider back to inherit", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.type(field(/Provider/), "anthropic");
    await userEvent.clear(field(/Provider/));
    await userEvent.clear(field(/Description/));
    await userEvent.type(field(/Description/), "Careful coder");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ provider: null, description: "Careful coder" });
  });

  it("shows the external agent that is not installed as unavailable", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(screen.getByText(/Cursor \(not installed\)/)).toBeInTheDocument();
    expect(screen.getByText(/Cursor/).closest("label")).toHaveAttribute("title", "not installed");
    expect(screen.getByText("Claude Code").closest("label")).toHaveAttribute("title", "");
  });

  it("reports a failed save without losing the form", async () => {
    setup({ put: () => jsonResponse({ detail: "name taken" }, { status: 409 }) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("⚠ name taken")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Engineer")).toBeInTheDocument();
  });
});

describe("Agents: creating and deleting", () => {
  it("creates a new agent from a blank form", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: "Advanced editor" }));         // the guided creator opens first
    expect(field("Id (a-z, 0-9, _)")).toBeEnabled();
    await userEvent.type(field(/^Id/), "analyst");
    await userEvent.type(field("Name"), "Analyst");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/agents")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/agents")!.body).toMatchObject({ id: "analyst", name: "Analyst", tier: "flash", framework: "adk", api_access: false, a2a: false });
  });

  it("saves the framework and the API / A2A switches chosen in the editor", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(await screen.findByLabelText(/API access/));
    await userEvent.click(screen.getByLabelText(/A2A \(other agents/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ api_access: true, a2a: true });
  });

  it("saves where the agent runs, its sandbox and the data it may read", async () => {
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await screen.findByTestId("agent-runtime");
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/data/sources")).toBe(true));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sandbox for its commands" }), "docker-microvm");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ sandbox: "docker-microvm" });
  });

  it("reports a failed create", async () => {
    setup({ post: () => jsonResponse({ detail: "id exists" }, { status: 409 }) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: "Advanced editor" }));         // the guided creator opens first
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("⚠ id exists")).toBeInTheDocument();
  });

  it("deletes a custom agent after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(pick("Reviewer"));
    await screen.findByDisplayValue("Reviewer");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(window.confirm).toHaveBeenCalledWith("Delete agent reviewer?");
  });

  it("keeps the agent when deletion is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls } = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(pick("Reviewer"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("reports a failed delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ del: () => jsonResponse({ detail: "in use" }, { status: 409 }) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(pick("Reviewer"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("⚠ in use")).toBeInTheDocument();
  });

  it("cannot delete a built-in agent", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(within(document.body).queryByRole("button", { name: "Delete" })).toBeNull();
  });
});


describe("Agents: AG-UI switches", () => {
  const quiet = { ...reviewer, id: "quiet", name: "Quiet", agui: { components: false, endpoint: true } };
  const closed = { ...reviewer, id: "closed", name: "Closed", agui: { components: true, endpoint: false } };
  const off = { ...reviewer, id: "off", name: "Off", agui: { components: false, endpoint: false } };

  it("marks agents that have something switched off in the list", async () => {
    setup({ agents: () => jsonResponse([engineer, quiet, closed, off]) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    const badges = screen.getAllByTestId("agui-badge").map((b) => b.textContent);
    expect(badges).toEqual(["no components", "no AG-UI stream", "AG-UI off"]);               // the engineer has everything on: no badge
  });

  it("has an AG-UI section in the editor and saves what you change", async () => {
    const { calls } = setup({ agents: () => jsonResponse([engineer, quiet]) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(pick("Quiet"));
    await screen.findByDisplayValue("Quiet");
    const section = screen.getByTestId("agent-agui");
    expect(within(section).getByLabelText("Rich components in answers")).not.toBeChecked();
    expect(within(section).getByLabelText("AG-UI stream for other apps")).toBeChecked();
    await userEvent.click(within(section).getByLabelText("AG-UI stream for other apps"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.pathname === "/api/agents/quiet")).toBe(true));
    expect((calls.find((c) => c.method === "PUT" && c.url.pathname === "/api/agents/quiet")!.body as { agui: unknown }).agui).toEqual({ components: false, endpoint: false });
  });

  it("shows older agents (no stored setting) as everything on", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    const section = screen.getByTestId("agent-agui");
    expect(within(section).getByLabelText("Rich components in answers")).toBeChecked();
    expect(within(section).getByLabelText("AG-UI stream for other apps")).toBeChecked();
    expect(screen.queryByTestId("agui-badge")).toBeNull();
  });
});
