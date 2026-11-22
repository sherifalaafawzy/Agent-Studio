import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/agents/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const engineer: Record<string, any> = {
  id: "engineer", name: "Engineer", description: "Writes code", instruction: "Be careful", provider: null, tier: "pro", model: null,
  tools: ["read_file"], mcp: [], skills: [], sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask",
};
const mcp = [
  { id: "fs", label: "Filesystem", ready: true, enabled: true, status: "ready" },
  { id: "git", label: "Git", ready: false, enabled: false, status: "pending_approval" },
  { id: "web", label: "Web", ready: false, enabled: false, status: "disabled" },
];
const ext = [
  { id: "claude_code", label: "Claude Code", installed: true, enabled: true },
  { id: "kiro", label: "Kiro", installed: true, enabled: false },
  { id: "cursor", label: "Cursor", installed: false, enabled: true },
];

function setup(agents: any[], over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/agents": () => jsonResponse(agents),
    "GET /api/tools": () => jsonResponse([]),
    "GET /api/mcp": () => jsonResponse(mcp),
    "GET /api/skills": () => jsonResponse([]),
    "GET /api/acp/agents": over.acp ?? (() => jsonResponse(ext)),
    "PUT /api/agents/engineer": () => jsonResponse({ ok: true }),
  });
}

describe("Agents: edge cases", () => {
  it("shows a custom-model agent's model name in the delegation tree", async () => {
    setup([{ ...engineer, sub_agents: ["local"] }, { ...engineer, id: "local", name: "Local", tier: "custom", model: "qwen-7b", builtin: false, tools: [], mcp: ["fs", "git"] }]);
    const { container } = render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    const tree = container.querySelector("[data-tour='agent-tree']")!;
    expect(tree).toHaveTextContent("└ Local qwen-7b 0 tools · mcp: fs,git");
    expect(container.querySelector("[data-tour='agent-list']")).toHaveTextContent("custom");
  });

  it("labels MCP servers as ready, pending approval or off", async () => {
    setup([engineer]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(screen.getByText("Filesystem").closest("label")).toHaveTextContent("ready");
    expect(screen.getByText("Git").closest("label")).toHaveTextContent("pending");
    expect(screen.getByText("Web").closest("label")).toHaveTextContent("off");
  });

  it("saves under the id in the toast when the agent has no name", async () => {
    const seen: string[] = [];
    window.addEventListener("as:toast", (e) => seen.push((e as CustomEvent).detail.text), { once: true });
    const { calls } = setup([{ ...engineer, name: "" }]);
    render(<AgentsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(seen).toEqual(["Saved engineer"]);
  });

  it("explains disabled and uninstalled external agents, and tolerates an agent saved without ACP settings", async () => {
    const legacy = { ...engineer };
    delete legacy.acp_agents;
    delete legacy.acp_policy;
    const { calls } = setup([legacy]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(await screen.findByTitle("disabled in Settings")).toHaveTextContent("Kiro");
    expect(screen.getByTitle("not installed")).toHaveTextContent("Cursor (not installed)");
    expect(screen.getByLabelText("Claude Code")).not.toBeChecked();
    expect(screen.getByLabelText(/Their permission requests/)).toHaveValue("ask");

    await userEvent.click(screen.getByLabelText("Claude Code"));
    await userEvent.selectOptions(screen.getByLabelText(/Their permission requests/), "readonly");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ acp_agents: ["claude_code"], acp_policy: "readonly" });
  });

  it("ticks and unticks an external agent on an agent saved without an ACP list", async () => {
    const legacy = { ...engineer };
    delete legacy.acp_agents;
    setup([legacy]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    const box = await screen.findByLabelText("Claude Code");
    await userEvent.click(box);
    expect(box).toBeChecked();
    await userEvent.click(box);
    expect(box).not.toBeChecked();
  });

  it("shows no external agents when they cannot be listed", async () => {
    setup([engineer], { acp: () => textResponse("boom", 500) });
    render(<AgentsPage />);
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Call external agents (ACP)")).toBeInTheDocument());
    expect(screen.queryByLabelText("Claude Code")).toBeNull();
    expect(screen.getByLabelText(/Their permission requests/)).toBeInTheDocument();
  });
});
