import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/agents/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

const engineer = { id: "engineer", name: "Engineer", description: "", instruction: "", provider: null, tier: "pro", model: null, tools: [], mcp: [], skills: [], sub_agents: [],
  builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask" };

describe("Agents: memory", () => {
  it("lets an agent be given memory and sends it with the agent", async () => {
    const net = routeFetch({ "GET /api/agents": () => jsonResponse([engineer]), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]),
      "GET /api/acp/agents": () => jsonResponse([]), "GET /api/rag/kbs": () => jsonResponse([]), "PUT /api/agents/engineer": () => jsonResponse({ ok: true }) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(screen.getByText(/starts every chat fresh/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Facts" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(net.calls.find((c) => c.method === "PUT")!.body).toMatchObject({ memory: { types: ["semantic"], auto_save: "review", auto_recall: true } });
  });

  it("shows an agent's existing memory settings", async () => {
    routeFetch({ "GET /api/agents": () => jsonResponse([{ ...engineer, memory: { types: ["episodic"], read_scopes: ["agent"], write_scopes: ["agent"], auto_recall: false, auto_save: "off" } }]),
      "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]), "GET /api/acp/agents": () => jsonResponse([]), "GET /api/rag/kbs": () => jsonResponse([]) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(screen.getByRole("checkbox", { name: "Past chats" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: /When it learns something new/ })).toHaveValue("off");
  });
});
