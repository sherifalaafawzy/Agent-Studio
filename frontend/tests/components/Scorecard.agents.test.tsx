import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Scorecard from "@/components/Scorecard";
import { jsonResponse, routeFetch } from "../helpers/http";

// Per-agent scorecards: the summary on the overall card, the ranking with its filters, and one agent's own card.
const OVERALL_CHECKS = [
  { id: "guard-input", title: "Guardrails check what goes into models", maps: ["LLM01"], status: "fail", detail: "Guardrails are switched off.", fix: "Turn guardrails on.", link: "/settings/#security", kind: "setting" },
];
const overall = (agents: unknown) => ({
  percent: 45, counts: { pass: 0, warn: 0, fail: 1 }, note: "This measures configuration.", checks: OVERALL_CHECKS, agents,
  frameworks: [{ id: "llm2025", name: "OWASP Top 10 for LLM Applications 2025", url: "u", covered: 1, risks: [{ id: "LLM01", name: "Prompt Injection", status: "fail", checks: ["guard-input"] }] },
    { id: "asi2026", name: "OWASP Top 10 for Agentic Applications 2026", url: "u", covered: 0, risks: [] }],
});
const SUMMARY = { count: 3, average: 61, bands: { good: 1, middling: 1, low: 1 }, lowest: { id: "risky", name: "Risky", percent: 30 } };
const issue = (status: string, title: string) => ({ id: "x", title, status, detail: "d", fix: "f", link: "/agents/?agent=risky#ag-guardrails" });
const ROWS = [
  { id: "risky", name: "Risky", builtin: false, framework: "adk", runtime: "agentcore", tools: 3, percent: 30, band: "low", counts: { pass: 5, warn: 3, fail: 4, na: 10 }, top_issue: issue("fail", "Guardrails check what goes into models") },
  { id: "engineer", name: "Engineer", builtin: true, framework: "adk", runtime: "local", tools: 9, percent: 62, band: "middling", counts: { pass: 12, warn: 5, fail: 0, na: 8 }, top_issue: issue("warn", "Runaway loops and spending are capped") },
  { id: "assistant", name: "Assistant", builtin: true, framework: "adk", runtime: "local", tools: 0, percent: 90, band: "good", counts: { pass: 12, warn: 0, fail: 0, na: 14 }, top_issue: null },
];
const AGENT_CHECKS = [
  { id: "guard-input", title: "Guardrails check what goes into models", maps: ["LLM01"], status: "fail", detail: "Guardrails are switched off for this agent: it runs unchecked.", fix: "Choose the default profiles.", link: "/agents/?agent=risky#ag-guardrails", kind: "setting", scope: "agent" },
  { id: "sandbox-network", title: "Commands run without network access", maps: ["LLM01", "LLM02"], status: "na", detail: "This agent cannot run commands.", fix: "", link: "/settings/#harness", kind: "setting", scope: "agent" },
  { id: "telemetry", title: "Every run is recorded", maps: ["LLM01"], status: "pass", detail: "Runs are stored.", fix: "", link: "/settings/#telemetry", kind: "setting", scope: "shared" },
];
const agentCard = (id: string, name: string, percent: number, builtin = false) => ({
  percent, counts: { pass: 1, warn: 0, fail: 1, na: 1 }, note: "This judges one agent.", checks: AGENT_CHECKS,
  agent: { id, name, builtin, framework: "adk", runtime: id === "risky" ? "agentcore" : "local", tools: 3 },
  frameworks: [
    { id: "llm2025", name: "OWASP Top 10 for LLM Applications 2025", url: "https://genai.owasp.org/llm-top-10/", covered: 2, risks: [
      { id: "LLM01", name: "Prompt Injection", status: "fail", checks: ["guard-input", "sandbox-network", "telemetry"] },
      { id: "LLM02", name: "Sensitive Information Disclosure", status: "na", checks: ["sandbox-network"] }] },
    { id: "asi2026", name: "OWASP Top 10 for Agentic Applications 2026", url: "u", covered: 0, risks: [] }],
});

function net(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/security/scorecard": () => jsonResponse(overall(SUMMARY)),
    "GET /api/security/agents": () => jsonResponse({ summary: SUMMARY, agents: ROWS }),
    "GET /api/security/agents/risky": () => jsonResponse(agentCard("risky", "Risky", 30)),
    "GET /api/security/agents/engineer": () => jsonResponse(agentCard("engineer", "Engineer", 62, true)),
    ...extra,
  });
}
const rowNames = () => screen.getAllByTestId("rank-row").map((r) => within(r).getByRole("button", { name: /Open the scorecard/ }).getAttribute("aria-label")!.replace("Open the scorecard of ", ""));
const perAgent = async () => { await userEvent.click(await screen.findByRole("tab", { name: "Per agent" })); await screen.findByTestId("agent-ranking"); };

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); window.history.replaceState(null, "", "/"); });

describe("Scorecard: per agent", () => {
  it("the overall card summarises the agents and offers the per-agent view", async () => {
    net();
    render(<Scorecard />);
    const box = await screen.findByTestId("agents-summary");
    expect(within(box).getByText(/3 agents · average 61%/)).toBeInTheDocument();
    expect(within(box).getByText("Risky")).toBeInTheDocument();
    expect(within(box).getByText(/judges the app's settings/)).toBeInTheDocument();
    await userEvent.click(within(box).getByRole("button", { name: "See each agent →" }));
    expect(await screen.findByTestId("agent-ranking")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Per agent" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Overall" }));
    expect(await screen.findByTestId("agents-summary")).toBeInTheDocument();
  });

  it("ranks the agents weakest first with their top issue, and filters them", async () => {
    net();
    render(<Scorecard />);
    await perAgent();
    expect(screen.getByText(/3 agents · average 61% · 1 below 50% · 1 at 80% or more/)).toBeInTheDocument();
    expect(rowNames()).toEqual(["Risky", "Engineer", "Assistant"]);
    const rows = screen.getAllByTestId("rank-row");
    expect(rows[0]).toHaveAttribute("data-band", "low");
    expect(within(rows[0]).getByText("30%")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Missing")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Guardrails check what goes into models")).toBeInTheDocument();
    expect(within(rows[0]).getByText("runs on agentcore")).toBeInTheDocument();
    expect(within(rows[0]).getByText("4 missing · 3 partly")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Nothing to fix")).toBeInTheDocument();
    expect(within(rows[1]).getByText("built in")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Sort"), "best");
    expect(rowNames()).toEqual(["Assistant", "Engineer", "Risky"]);
    await userEvent.selectOptions(screen.getByLabelText("Sort"), "name");
    expect(rowNames()).toEqual(["Assistant", "Engineer", "Risky"]);
    await userEvent.selectOptions(screen.getByLabelText("Sort"), "worst");
    await userEvent.selectOptions(screen.getByLabelText("Show"), "attention");
    expect(rowNames()).toEqual(["Risky"]);
    await userEvent.selectOptions(screen.getByLabelText("Show"), "fix");
    expect(rowNames()).toEqual(["Risky", "Engineer"]);
    await userEvent.click(screen.getByLabelText("Only my own agents"));
    expect(rowNames()).toEqual(["Risky"]);
    await userEvent.click(screen.getByLabelText("Only my own agents"));
    await userEvent.selectOptions(screen.getByLabelText("Show"), "all");
    await userEvent.type(screen.getByLabelText("Find an agent"), "assis");
    expect(rowNames()).toEqual(["Assistant"]);
    await userEvent.clear(screen.getByLabelText("Find an agent"));
    await userEvent.type(screen.getByLabelText("Find an agent"), "zzz");
    expect(screen.getByText("No agent matches.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All agents (Markdown)" })).toHaveAttribute("href", "http://127.0.0.1:8765/api/security/agents?format=md");
    expect(screen.getByRole("link", { name: "All agents (JSON)" })).toHaveAttribute("href", "http://127.0.0.1:8765/api/security/agents?format=full");
  });

  it("opens one agent's card: its own score, not-applicable checks, whole-app checks and exports", async () => {
    const c = net();
    render(<Scorecard />);
    await perAgent();
    await userEvent.click(screen.getByRole("button", { name: "Open the scorecard of Risky" }));
    expect(await screen.findByText("Security score: Risky")).toBeInTheDocument();
    expect(screen.getByLabelText("Score 30 percent")).toBeInTheDocument();
    expect(screen.getByText("1 in place · 0 partly · 1 missing · 1 not applicable")).toBeInTheDocument();
    expect(screen.getByTestId("agent-facts")).toHaveTextContent("Your agent · adk framework · runs on agentcore · 3 tools");
    expect(window.location.search).toBe("?agent=risky");
    expect(screen.getByRole("link", { name: "Download report (Markdown)" })).toHaveAttribute("href", "http://127.0.0.1:8765/api/security/agents/risky?format=md");
    expect(screen.getByRole("link", { name: "Download (JSON)" })).toHaveAttribute("href", "http://127.0.0.1:8765/api/security/agents/risky?download=1");
    expect(screen.getByRole("link", { name: "Edit this agent →" })).toHaveAttribute("href", "/agents/?agent=risky");

    await userEvent.click(screen.getByRole("button", { name: /LLM01/ }));
    const d = screen.getByTestId("risk-detail");
    expect(within(d).getByText("Not applicable")).toBeInTheDocument();
    expect(within(d).getByText("This agent cannot run commands.")).toBeInTheDocument();
    expect(within(d).getByText("whole app")).toBeInTheDocument();
    expect(within(d).getByText(/What to do: Choose the default profiles\./)).toBeInTheDocument();
    expect(within(d).getByRole("link", { name: "Open →" })).toHaveAttribute("href", "/agents/?agent=risky#ag-guardrails");
    expect(screen.getByRole("button", { name: /LLM02/ })).toHaveAttribute("data-status", "na");

    await userEvent.selectOptions(screen.getByLabelText("Agent"), "engineer");
    expect(await screen.findByText("Security score: Engineer")).toBeInTheDocument();
    expect(window.location.search).toBe("?agent=engineer");
    expect(screen.getByTestId("agent-facts")).toHaveTextContent("Built-in agent · adk framework · runs on this PC");
    const before = c.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(c.calls.length).toBeGreaterThanOrEqual(before + 3));
    await userEvent.click(screen.getByRole("button", { name: "← All agents" }));
    expect(await screen.findByTestId("agent-ranking")).toBeInTheDocument();
    expect(window.location.search).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Open the scorecard of Assistant" }));       // no card route for it: the error is shown
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("opens straight on one agent when it is named in the address", async () => {
    net();
    render(<Scorecard initialAgent="engineer" />);
    expect(await screen.findByText("Security score: Engineer")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Per agent" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect((screen.getByLabelText("Agent") as HTMLSelectElement).value).toBe("engineer"));
    expect(await screen.findByText(/^Built-in agent/)).toBeInTheDocument();
  });

  it("names the agent in the picker even when the list cannot be loaded, and says why", async () => {
    net({ "GET /api/security/agents": () => jsonResponse({ detail: "agents offline" }, { status: 500 }) });
    render(<Scorecard initialAgent="engineer" />);
    expect(await screen.findByText("Security score: Engineer")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("agents offline");
    expect(within(screen.getByLabelText("Agent")).getByRole("option", { name: "engineer" })).toBeInTheDocument();
  });

  it("says when the agents are still loading, and shows no summary when there are no agents", async () => {
    let release: (r: Response) => void = () => {};
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse(overall({ count: 0, average: null, bands: {}, lowest: null })),
      "GET /api/security/agents": () => new Promise<Response>((r) => { release = r; }) });
    render(<Scorecard />);
    await screen.findByLabelText("Score 45 percent");
    expect(screen.queryByTestId("agents-summary")).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: "Per agent" }));
    expect(await screen.findByText("Loading agents…")).toBeInTheDocument();
    release(jsonResponse({ summary: SUMMARY, agents: ROWS }));
    expect(await screen.findByTestId("agent-ranking")).toBeInTheDocument();
  });

  it("keeps the ranking usable when there are no agents", async () => {
    routeFetch({ "GET /api/security/scorecard": () => jsonResponse(overall(undefined)),
      "GET /api/security/agents": () => jsonResponse({ summary: { count: 0, average: null, bands: {}, lowest: null }, agents: [] }) });
    render(<Scorecard />);
    await userEvent.click(await screen.findByRole("tab", { name: "Per agent" }));
    expect(await screen.findByText("No agent matches.")).toBeInTheDocument();
    expect(screen.queryByText(/agents · average/)).toBeNull();
  });
});
