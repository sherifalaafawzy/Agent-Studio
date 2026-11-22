import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/agents/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

// Each agent shows its own security score: a badge in the list, a bar in the editor, and the editor can be opened on a section from a scorecard.
const engineer = { id: "engineer", name: "Engineer", description: "", instruction: "", provider: null, tier: "pro", model: null, tools: [], mcp: [], skills: [], sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask" };
const reviewer = { ...engineer, id: "reviewer", name: "Reviewer", tier: "flash", builtin: false };
const SCORES = { agents: [
  { id: "engineer", percent: 62, band: "middling", counts: { pass: 12, warn: 5, fail: 1, na: 8 } },
  { id: "reviewer", percent: 30, band: "low", counts: { pass: 5, warn: 3, fail: 4, na: 10 } }] };

function setup(scores: () => Response = () => jsonResponse(SCORES)) {
  return routeFetch({
    "GET /api/agents": () => jsonResponse([engineer, reviewer]), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]),
    "GET /api/security/agents": scores,
  });
}
const scrolled = vi.fn();
beforeEach(() => { Element.prototype.scrollIntoView = function (this: Element) { scrolled(this.id); }; });
afterEach(() => { vi.unstubAllGlobals(); scrolled.mockReset(); window.history.replaceState(null, "", "/"); });

describe("Agents: security scores", () => {
  it("shows each agent's score as a badge that opens its scorecard, without selecting the agent", async () => {
    setup();
    render(<AgentsPage />);
    const badge = await screen.findByRole("link", { name: /Security score 30 percent: open the scorecard of Reviewer/ });
    expect(badge).toHaveTextContent("30%");
    expect(badge).toHaveAttribute("href", "/security/?agent=reviewer");
    expect(badge.className).toContain("bad");
    expect(screen.getByRole("link", { name: /Security score 62 percent/ }).className).toContain("warn");
    const stop = (e: Event) => e.preventDefault();
    document.addEventListener("click", stop, true);                                        // jsdom cannot navigate: only check the click does not select the agent
    await userEvent.click(badge);
    document.removeEventListener("click", stop, true);
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
  });

  it("shows the selected agent's score and what is open above its settings", async () => {
    setup();
    render(<AgentsPage />);
    const bar = await screen.findByTestId("agent-score");
    expect(within(bar).getByText("Security score 62%")).toBeInTheDocument();
    expect(within(bar).getByText("1 missing · 5 partly")).toBeInTheDocument();
    expect(within(bar).getByRole("link", { name: "View its scorecard →" })).toHaveAttribute("href", "/security/?agent=engineer");
  });

  it("shows no score when the scores cannot be loaded", async () => {
    setup(() => jsonResponse({ detail: "offline" }, { status: 500 }));
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(screen.queryByTestId("agent-score")).toBeNull();
    expect(screen.queryByRole("link", { name: /Security score/ })).toBeNull();
  });

  it("opens the agent and scrolls to the section named in a scorecard's fix link", async () => {
    window.history.replaceState(null, "", "/agents/?agent=reviewer#ag-guardrails");
    setup();
    render(<AgentsPage />);
    await waitFor(() => expect(scrolled).toHaveBeenCalledWith("ag-guardrails"));
    expect(screen.getByDisplayValue("Reviewer")).toBeInTheDocument();
    expect(screen.getByTestId("agent-score")).toHaveTextContent("Security score 30%");
  });

  it("ignores a link to an agent that does not exist", async () => {
    window.history.replaceState(null, "", "/agents/?agent=ghost#ag-tools");
    setup();
    render(<AgentsPage />);
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
    expect(scrolled).not.toHaveBeenCalled();
  });

  it("has an anchor for every section a scorecard can point at", async () => {
    setup();
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    for (const id of ["ag-tools", "ag-mcp", "ag-rag", "ag-memory", "ag-spawn", "ag-acp", "ag-guardrails", "ag-identity", "ag-access", "ag-runtime", "ag-data"]) {
      expect(document.getElementById(id), id).not.toBeNull();
    }
  });
});
