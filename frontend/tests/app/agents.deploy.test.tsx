import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/agents/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

const agent = (id: string, name: string) => ({
  id, name, description: "", instruction: "", provider: null, tier: "flash", model: null, tools: [], mcp: [], skills: [], sub_agents: [], builtin: false, can_spawn: false, acp_agents: [], acp_policy: "ask",
});

function setup() {
  return routeFetch({
    "GET /api/agents": () => jsonResponse([agent("engineer", "Engineer"), agent("reviewer", "Reviewer")]),
    "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]), "GET /api/acp/agents": () => jsonResponse([]),
    "GET /api/runtimes": () => jsonResponse({ targets: [] }), "GET /api/sandboxes": () => jsonResponse({ kinds: [] }), "GET /api/data/sources": () => jsonResponse({ sources: [] }),
  });
}

describe("Agents: deploy to Claude Code", () => {
  it("opens the dialog from the editor and from the selected agent in the list", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "Deploy to Claude Code…" }));
    expect(await screen.findByRole("dialog", { name: "Deploy “Engineer” to Claude Code" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("deploy-claude")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Deploy Engineer to Claude Code" }));
    expect(await screen.findByTestId("deploy-claude")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deploy Reviewer to Claude Code" })).not.toBeInTheDocument();
  });

  it("is not offered for an agent that has not been saved yet", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: "Advanced editor" }));
    expect(screen.queryByRole("button", { name: "Deploy to Claude Code…" })).not.toBeInTheDocument();
  });
});
