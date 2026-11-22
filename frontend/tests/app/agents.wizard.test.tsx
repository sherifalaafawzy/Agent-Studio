import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/agents/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));
const wiz = vi.hoisted(() => ({ props: null as any }));
vi.mock("@/components/AgentWizard", () => ({
  default: (p: any) => {
    wiz.props = p;
    return <div data-testid="wizard"><button onClick={p.onCancel}>w-cancel</button><button onClick={p.onAdvanced}>w-advanced</button><button onClick={() => p.onCreated("fresh")}>w-created</button></div>;
  },
}));

const engineer = { id: "engineer", name: "Engineer", description: "", instruction: "", provider: null, tier: "pro", model: null, tools: [], mcp: [], skills: [], sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask" };
const fresh = { ...engineer, id: "fresh", name: "Fresh one", builtin: false };
function setup(agents: object[] = [engineer]) {
  return routeFetch({
    "GET /api/agents": () => jsonResponse(agents), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([{ id: "git", label: "Git", ready: true, enabled: true, status: "ready" }]),
    "GET /api/skills": () => jsonResponse([{ name: "plan", description: "", group: "Eng" }]), "GET /api/acp/agents": () => jsonResponse([{ id: "claude", label: "Claude Code", installed: true, enabled: true }]),
    "GET /api/rag/kbs": () => jsonResponse([{ id: "faq", label: "FAQ" }]),
  });
}
beforeEach(() => { wiz.props = null; window.history.replaceState(null, "", "/agents/"); });

describe("Agents page: the guided creator", () => {
  it("'+ New' opens the wizard with the page's own lists, in place of the editor", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    expect(await screen.findByTestId("wizard")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Engineer")).toBeNull();
    await waitFor(() => expect(wiz.props.kbs).toEqual([{ id: "faq", label: "FAQ" }]));
    expect(wiz.props.agents.map((a: any) => a.id)).toEqual(["engineer"]);
    expect(wiz.props.mcp[0].id).toBe("git");
    expect(wiz.props.skills[0].name).toBe("plan");
    expect(wiz.props.ext[0].id).toBe("claude");
  });

  it("cancel brings the editor back", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: "w-cancel" }));
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
    expect(screen.queryByTestId("wizard")).toBeNull();
  });

  it("'Advanced editor' opens a blank form to create an agent by hand", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: "w-advanced" }));
    expect(screen.getByLabelText(/^Id/)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Create" })).toBeInTheDocument();
  });

  it("after a wizard creates an agent, the list reloads and that agent is selected", async () => {
    let list: object[] = [engineer];
    routeFetch({
      "GET /api/agents": () => jsonResponse(list), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]),
      "GET /api/acp/agents": () => jsonResponse([]), "GET /api/rag/kbs": () => jsonResponse([]),
    });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    list = [engineer, fresh];
    await userEvent.click(await screen.findByRole("button", { name: "w-created" }));
    expect(await screen.findByDisplayValue("Fresh one")).toBeInTheDocument();
    expect(screen.queryByTestId("wizard")).toBeNull();
  });

  it("picking an agent in the list closes the wizard", async () => {
    setup([engineer, fresh]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await screen.findByTestId("wizard");
    await userEvent.click([...document.querySelectorAll<HTMLElement>(".ag-side .card > div")].find((d) => d.textContent?.startsWith("Fresh one"))!);
    expect(await screen.findByDisplayValue("Fresh one")).toBeInTheDocument();
    expect(screen.queryByTestId("wizard")).toBeNull();
  });

  it("opens straight into the wizard from ?wizard=1 (the set-up guide links there)", async () => {
    window.history.replaceState(null, "", "/agents/?wizard=1");
    setup();
    render(<AgentsPage />);
    expect(await screen.findByTestId("wizard")).toBeInTheDocument();
  });
});
