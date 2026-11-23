import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("@/components/Nav", () => ({ toast: vi.fn() }));
vi.mock("@/components/science/SearchPanel", () => ({ default: ({ investigationId, onEvidence }: any) => <div data-testid="search-panel">search for {investigationId || "nobody"}<button onClick={onEvidence}>evidence added</button></div> }));
vi.mock("@/components/science/ComputePanel", () => ({ default: () => <div data-testid="compute-panel" /> }));
vi.mock("@/components/science/ComponentsPanel", () => ({ default: () => <div data-testid="components-panel" /> }));
vi.mock("@/components/science/SafetyPanel", () => ({ default: ({ cfg }: any) => <div data-testid="safety-panel">level {cfg.safety_level}</div> }));
vi.mock("@/components/science/ScienceSettings", () => ({ default: ({ onChange, cfg, connectors }: any) => <button onClick={() => onChange({ ...cfg, safety_level: "strict" }, connectors.slice(0, 1))}>fake save</button> }));
vi.mock("@/components/science/InvestigationPanel", () => ({ default: ({ inv, reload }: any) => <div data-testid="inv-panel">{inv.title}<button onClick={reload}>reload</button></div> }));
vi.mock("@/components/science/ReportPanel", () => ({ default: ({ investigationId }: any) => <div data-testid="report-panel">report of {investigationId}</div> }));

import SciencePage from "@/app/science/page";

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

const CFG = { enabled: true, safety_level: "standard", disabled_connectors: [], rate_overrides: {}, contact_email: "", max_calls_per_investigation: 60, cache_minutes: 60, max_investigations: 200, safety_levels: ["standard", "strict"],
  domains: [], labels: {}, key_set: [], policy: { blocked: [], confirm: [], allowed: [], tools_not_offered: [], limits: [] } };
const CONNS = [{ id: "pubmed", label: "PubMed", status: "ready" }, { id: "ads", label: "NASA ADS", status: "needs_key" }, { id: "arxiv", label: "arXiv", status: "off" }];
const INV = (id: string, over: Record<string, unknown> = {}) => ({ id, title: `Study ${id}`, domain: "chemistry", question: "?", status: "open", autonomy: "approve", session_id: "", agent: "", created: 1, updated: Date.now() / 1000 - 600, evidence: 0, claims: 2, hypotheses: 0, source_calls: 0, ...over });
const FULL = (id: string, over: Record<string, unknown> = {}) => ({ ...INV(id), claims_list: [], evidence_list: [], hypotheses_list: [], runs_list: [], ...over });

function routes(list: any[] = [INV("a"), INV("b", { domain: "physics" })], extra: Record<string, any> = {}) {
  return routeFetch({ "GET /api/science/config": () => jsonResponse(CFG), "GET /api/science/connectors": () => jsonResponse(CONNS), "GET /api/science/investigations": () => jsonResponse(list),
    "GET /api/science/investigations/a": () => jsonResponse(FULL("a")), "GET /api/science/investigations/b": () => jsonResponse(FULL("b", { status: "done" })), ...extra });
}

describe("Science Lab page", () => {
  it("lists investigations, filters by area, shows the data-source status and opens one", async () => {
    routes();
    const user = userEvent.setup();
    render(<SciencePage />);
    expect(await screen.findByText("Study a")).toBeInTheDocument();
    expect(screen.getByText("safety: standard")).toBeInTheDocument();
    expect(screen.getByTestId("search-panel")).toHaveTextContent("search for nobody");
    await user.click(screen.getByRole("button", { name: "Physics" }));
    expect(screen.queryByText("Study a")).toBeNull();
    expect(screen.getByText("Study b")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Biology" }));
    expect(screen.getByText(/No investigations in this area/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All" }));
    const dots = screen.getByText("Data sources");
    await user.click(dots);
    expect(screen.getByText("needs a key")).toBeInTheDocument();
    await user.click(screen.getByText("Study a"));
    expect(await screen.findByTestId("inv-panel")).toHaveTextContent("Study a");
    expect(screen.getByRole("listitem", { current: true })).toHaveTextContent("Study a");
    await user.click(screen.getByRole("tab", { name: "Search" }));
    expect(screen.getByTestId("search-panel")).toHaveTextContent("search for a");
    await user.click(screen.getByRole("tab", { name: "Report" }));
    expect(screen.getByTestId("report-panel")).toHaveTextContent("report of a");
    await user.click(screen.getByRole("tab", { name: "Compute" }));
    expect(screen.getByTestId("compute-panel")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Safety" }));
    expect(screen.getByTestId("safety-panel")).toHaveTextContent("level standard");
    await user.click(screen.getByRole("tab", { name: "Components" }));
    expect(screen.getByTestId("components-panel")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    await user.click(screen.getByRole("button", { name: "fake save" }));
    await user.click(screen.getByRole("tab", { name: "Safety" }));
    expect(screen.getByTestId("safety-panel")).toHaveTextContent("level strict");
  });

  it("starts an investigation and opens it", async () => {
    const created = INV("n1", { title: "New one" });
    const { calls } = routes([], { "POST /api/science/investigations": () => jsonResponse(created), "GET /api/science/investigations/n1": () => jsonResponse(FULL("n1", { title: "New one" })) });
    const user = userEvent.setup();
    render(<SciencePage />);
    expect(await screen.findByText(/No investigations yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New investigation" }));
    expect(screen.getByRole("button", { name: "Start" })).toBeDisabled();
    await user.type(screen.getByLabelText("Investigation title"), "New one");
    await user.selectOptions(screen.getByLabelText("Area"), "chemistry");
    await user.type(screen.getByLabelText("Question"), "Why?");
    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByTestId("inv-panel")).toHaveTextContent("New one");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ title: "New one", domain: "chemistry", question: "Why?" });
    expect(screen.queryByTestId("science-new")).toBeNull();
  });

  it("shows why a new investigation was refused and can be cancelled", async () => {
    routes([], { "POST /api/science/investigations": () => jsonResponse({ detail: "Blocked: no" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<SciencePage />);
    await user.click(await screen.findByRole("button", { name: "New investigation" }));
    await user.type(screen.getByLabelText("Investigation title"), "x");
    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(await within(screen.getByTestId("science-new")).findByRole("alert")).toHaveTextContent("Blocked: no");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("science-new")).toBeNull();
  });

  it("marks done, reopens and deletes after asking", async () => {
    const { calls } = routes(undefined, { "PUT /api/science/investigations/a": () => jsonResponse(INV("a")), "DELETE /api/science/investigations/a": () => jsonResponse({ ok: true }) });
    const user = userEvent.setup();
    render(<SciencePage />);
    await user.click(await screen.findByText("Study a"));
    await user.click(await screen.findByRole("button", { name: "Mark as done" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ status: "done" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Study a");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(screen.getAllByRole("button", { name: "Delete" }).at(-1)!);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.getByText("Pick an investigation on the left, or start one.")).toBeInTheDocument());
  });

  it("reopens a finished investigation and refreshes after evidence is added", async () => {
    const { calls } = routes(undefined, { "PUT /api/science/investigations/b": () => jsonResponse(INV("b")) });
    const user = userEvent.setup();
    render(<SciencePage />);
    await user.click(await screen.findByText("Study b"));
    await user.click(await screen.findByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ status: "open" }));
    await user.click(screen.getByRole("tab", { name: "Search" }));
    const before = calls.filter((c) => c.url.pathname === "/api/science/investigations/b").length;
    await user.click(screen.getByRole("button", { name: "evidence added" }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/science/investigations/b").length).toBeGreaterThan(before));
  });

  it("asks to pick an investigation for the investigation and report tabs, and says when the lab is off", async () => {
    routeFetch({ "GET /api/science/config": () => jsonResponse({ ...CFG, enabled: false }), "GET /api/science/connectors": () => jsonResponse(CONNS), "GET /api/science/investigations": () => jsonResponse([]) });
    const user = userEvent.setup();
    render(<SciencePage />);
    expect(await screen.findByText(/switched off/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Investigation" }));
    expect(screen.getByText("Pick an investigation on the left, or start one.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Report" }));
    expect(screen.getByText("Pick an investigation to see its report.")).toBeInTheDocument();
  });

  it("shows load and delete errors", async () => {
    routeFetch({ "GET /api/science/config": () => jsonResponse({ detail: "backend is down" }, { status: 500 }), "GET /api/science/connectors": () => jsonResponse(CONNS), "GET /api/science/investigations": () => jsonResponse([INV("a")]),
      "GET /api/science/investigations/a": () => jsonResponse({ detail: "no such investigation" }, { status: 404 }) });
    const user = userEvent.setup();
    render(<SciencePage />);
    expect(await screen.findByText("backend is down")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    expect(screen.queryByTestId("science-settings")).toBeNull();
  });

  it("shows when an investigation cannot be loaded, and when deleting or reopening fails", async () => {
    routeFetch({ "GET /api/science/config": () => jsonResponse(CFG), "GET /api/science/connectors": () => jsonResponse(CONNS), "GET /api/science/investigations": () => jsonResponse([INV("a"), INV("c")]),
      "GET /api/science/investigations/a": () => jsonResponse(FULL("a")), "GET /api/science/investigations/c": () => jsonResponse({ detail: "no such investigation" }, { status: 404 }),
      "PUT /api/science/investigations/a": () => jsonResponse({ detail: "cannot change" }, { status: 422 }), "DELETE /api/science/investigations/a": () => jsonResponse({ detail: "no such investigation" }, { status: 404 }) });
    const user = userEvent.setup();
    render(<SciencePage />);
    await user.click(await screen.findByText("Study c"));
    expect(await screen.findByText("no such investigation")).toBeInTheDocument();
    await user.click(screen.getByText("Study a"));
    await user.click(await screen.findByRole("button", { name: "Mark as done" }));
    expect(await screen.findByText("cannot change")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(screen.getAllByRole("button", { name: "Delete" }).at(-1)!);
    await waitFor(() => expect(screen.getAllByText("no such investigation").length).toBeGreaterThan(0));
  });
});
