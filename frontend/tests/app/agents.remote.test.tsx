import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/agents/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/AgentWizard", () => ({ default: (p: any) => <div data-testid="wizard"><button onClick={p.onCancel}>w-cancel</button></div> }));

const engineer = { id: "engineer", name: "Engineer", description: "", instruction: "", provider: null, tier: "pro", model: null, tools: [], mcp: [], skills: [], sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask" };
const CARD = { name: "Travel Agent", description: "Plans trips.", version: "1", skills: [{ id: "plan", name: "Plan a trip" }], auth: [], interfaces: [], input_modes: [], output_modes: [], streaming: false };
const REMOTE = { id: "travel", label: "Travel", url: "https://agents.example.com/travel/", enabled: true, has_token: false, auth_kind: "bearer", auth_header: "", delegates: [],
  network_scope: "internet", scope_label: "data leaves this PC", card: CARD, name: "Travel Agent", description: "Plans trips." };
const LAN = { ...REMOTE, id: "lan", label: "Office bot", url: "http://10.0.0.4/a/", network_scope: "network", scope_label: "on this network", enabled: false };

function setup(remotes: object[] = [REMOTE, LAN]) {
  let list = remotes;
  return routeFetch({
    "GET /api/agents": () => jsonResponse([engineer]), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]),
    "GET /api/acp/agents": () => jsonResponse([]), "GET /api/rag/kbs": () => jsonResponse([]),
    "GET /api/access/remotes": () => jsonResponse(list),
    "POST /api/access/remotes/preview": () => jsonResponse({ ok: true, card: CARD, scope: "internet" }),
    "POST /api/access/remotes/connect": () => { list = [...list, { ...REMOTE, id: "new_agent", label: "New agent" }]; return jsonResponse(list[list.length - 1]); },
    "DELETE /api/access/remotes/travel": () => { list = list.filter((r: any) => r.id !== "travel"); return jsonResponse({ ok: true }); },
  });
}
beforeEach(() => { window.history.replaceState(null, "", "/agents/"); });

describe("Agents page: remote A2A agents", () => {
  it("lists remote agents next to the agents, badged and labelled by where they run", async () => {
    setup();
    render(<AgentsPage />);
    const rows = await screen.findAllByTestId("remote-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Remote A2A")).toBeInTheDocument();
    expect(within(rows[0]).getByText("data leaves this PC")).toBeInTheDocument();
    expect(within(rows[1]).getByText("on this network")).toBeInTheDocument();
    expect(screen.getByText("Remote A2A agents")).toBeInTheDocument();
  });

  it("shows no remote section when there are none, even if the list cannot be loaded", async () => {
    routeFetch({ "GET /api/agents": () => jsonResponse([engineer]), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(screen.queryByText("Remote A2A agents")).toBeNull();
  });

  it("opens a remote agent's panel when it is clicked and goes back to an agent when that is clicked", async () => {
    setup();
    render(<AgentsPage />);
    await userEvent.click((await screen.findAllByTestId("remote-row"))[0]);
    expect(await screen.findByTestId("remote-panel")).toHaveTextContent("Plan a trip");
    expect(screen.queryByText("Delegation tree")).toBeNull();
    await userEvent.click(screen.getAllByText("Engineer")[0]);
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
    expect(screen.queryByTestId("remote-panel")).toBeNull();
  });

  it("'+ New' asks first: the wizard, or connecting an A2A agent", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    expect(await screen.findByTestId("create-choice")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /New agent \(wizard\)/ }));
    expect(await screen.findByTestId("wizard")).toBeInTheDocument();
    expect(screen.queryByTestId("create-choice")).toBeNull();
  });

  it("connects an A2A agent from the choice and lands on its panel", async () => {
    const net = setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /Connect an A2A agent/ }));
    await userEvent.type(await screen.findByLabelText(/^Agent address/), "https://agents.example.com/new/");
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    await userEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByTestId("remote-panel")).toHaveTextContent("New agent");
    expect(net.calls.some((c) => c.url.pathname === "/api/access/remotes/connect")).toBe(true);
    expect(screen.getAllByTestId("remote-row")).toHaveLength(3);
  });

  it("cancelling the connect form returns to the editor", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /Connect an A2A agent/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
  });

  it("removing a remote agent closes its panel", async () => {
    setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AgentsPage />);
    await userEvent.click((await screen.findAllByTestId("remote-row"))[0]);
    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByTestId("remote-panel")).toBeNull());
    expect(screen.getAllByTestId("remote-row")).toHaveLength(1);
  });
});
