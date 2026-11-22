import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SecurityActivity from "@/components/SecurityActivity";
import SecurityPage from "@/app/security/page";
import { jsonResponse, routeFetch } from "../helpers/http";

const G = [{ id: 2, ts: 1_700_000_100, stage: "tool", agent: "", profile: "strict", action: "blocked", category: "", reason: "contains a word on your blocked list", chars: 40 },
  { id: 1, ts: 1_700_000_000, stage: "input", agent: "engineer", profile: "basic", action: "flagged", category: "pii", reason: "contains an email address", chars: 17 }];
const I = [{ id: 2, ts: 1_700_000_200, event: "refused", subject: "spiffe://x/agent/a", actor: "", audience: "", scopes: "", detail: "asked for more" },
  { id: 1, ts: 1_700_000_100, event: "delegated", subject: "spiffe://x/agent/a", actor: "spiffe://x/agent/b", audience: "agent:b", scopes: "files:read", detail: "a -> b" }];
const SCORE = { percent: 50, counts: { pass: 0, warn: 0, fail: 0 }, note: "n", checks: [], frameworks: [{ id: "llm2025", name: "OWASP Top 10 for LLM Applications 2025", url: "u", covered: 0, risks: [] },
  { id: "asi2026", name: "OWASP Top 10 for Agentic Applications 2026", url: "u", covered: 0, risks: [] }] };
const RT = { tools: [], install: { running: false, tool: null, log: [], error: null }, runs_running: 0 };

function net(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/guardrails/events": (_i: any, url: URL) => jsonResponse({ events: url.searchParams.get("action") === "blocked" ? [G[0]] : G }), "GET /api/identity/events": () => jsonResponse({ events: I }),
    "GET /api/security/scorecard": () => jsonResponse(SCORE), "GET /api/redteam/status": () => jsonResponse(RT), "GET /api/redteam/runs": () => jsonResponse({ runs: [] }),
    "GET /api/agents": () => jsonResponse([]), "GET /api/providers": () => jsonResponse([]), ...extra,
  });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); window.location.hash = ""; });

describe("SecurityActivity", () => {
  it("shows what was caught without ever showing the text, and filters by outcome", async () => {
    const c = net();
    render(<SecurityActivity />);
    expect(await screen.findByText(/contains an email address \(17 characters\)/)).toBeInTheDocument();
    expect(screen.getByText("never stored", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("tool · strict")).toBeInTheDocument();
    expect(screen.getAllByText("–").length).toBeGreaterThan(0);                                    // no agent / no audience
    expect(screen.getByText(/spiffe:\/\/x\/agent\/a ← spiffe:\/\/x\/agent\/b/)).toBeInTheDocument();
    expect(screen.getByText("agent:b · files:read")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "blocked");
    await waitFor(() => expect(c.calls.some((x) => x.url.search.includes("action=blocked"))).toBe(true));
    await waitFor(() => expect(screen.queryByText(/contains an email address/)).toBeNull());
  });
  it("says so when nothing is recorded, and shows a load error", async () => {
    net({ "GET /api/guardrails/events": () => jsonResponse({ events: [] }), "GET /api/identity/events": () => jsonResponse({ events: [] }) });
    const { unmount } = render(<SecurityActivity />);
    expect(await screen.findAllByText("Nothing recorded.")).toHaveLength(2);
    unmount();
    vi.unstubAllGlobals();
    net({ "GET /api/guardrails/events": () => jsonResponse({ detail: "offline" }, { status: 500 }) });
    const second = render(<SecurityActivity />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    second.unmount();
    vi.unstubAllGlobals();
    net({ "GET /api/identity/events": () => jsonResponse({ detail: "identity offline" }, { status: 500 }) });
    render(<SecurityActivity />);
    expect(await screen.findByRole("alert")).toHaveTextContent("identity offline");
  });
});

describe("Security page", () => {
  it("opens on the scorecard and switches tabs, remembering the choice in the address", async () => {
    net();
    render(<SecurityPage />);
    expect(await screen.findByTestId("scorecard")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Red team" }));
    expect(await screen.findByTestId("redteam")).toBeInTheDocument();
    expect(window.location.hash).toBe("#redteam");
    await userEvent.click(screen.getByRole("button", { name: "Activity" }));
    expect(await screen.findByTestId("activity")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Scorecard" }));
    expect(await screen.findByTestId("scorecard")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Guardrails and identity settings/ })).toHaveAttribute("href", "/settings/#security");
  });
  it("starts on the tab named in the address, and ignores an unknown one", async () => {
    net();
    window.location.hash = "#activity";
    const { unmount } = render(<SecurityPage />);
    expect(await screen.findByTestId("activity")).toBeInTheDocument();
    unmount();
    window.location.hash = "#nonsense";
    render(<SecurityPage />);
    expect(await screen.findByTestId("scorecard")).toBeInTheDocument();
    expect(within(document.body).queryByTestId("activity")).toBeNull();
  });
  it("opens on one agent's own scorecard when the address names the agent", async () => {
    const card = { percent: 30, counts: { pass: 0, warn: 0, fail: 0, na: 0 }, note: "n", checks: [], agent: { id: "engineer", name: "Engineer", builtin: true, framework: "adk", runtime: "local", tools: 3 },
      frameworks: [{ id: "llm2025", name: "OWASP Top 10 for LLM Applications 2025", url: "u", covered: 0, risks: [] }, { id: "asi2026", name: "OWASP Top 10 for Agentic Applications 2026", url: "u", covered: 0, risks: [] }] };
    net({ "GET /api/security/agents": () => jsonResponse({ summary: { count: 1, average: 30, bands: {}, lowest: null }, agents: [] }), "GET /api/security/agents/engineer": () => jsonResponse(card) });
    window.history.replaceState(null, "", "/security/?agent=engineer");
    render(<SecurityPage />);
    expect(await screen.findByText("Security score: Engineer")).toBeInTheDocument();
    window.history.replaceState(null, "", "/");
  });
});
