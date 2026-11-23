import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../../helpers/http";

vi.mock("@/components/uikit/UiBlock", () => ({ default: ({ spec }: any) => <div data-testid="uiblock">{spec.type}:{spec.title}:{spec.series ? spec.series[0].values.length : spec.rows?.length}</div> }));
vi.mock("@/components/Nav", () => ({ toast: vi.fn() }));

import SearchPanel from "@/components/science/SearchPanel";
import ComputePanel from "@/components/science/ComputePanel";
import InvestigationPanel from "@/components/science/InvestigationPanel";
import ReportPanel from "@/components/science/ReportPanel";
import SafetyPanel from "@/components/science/SafetyPanel";
import ComponentsPanel from "@/components/science/ComponentsPanel";
import ScienceSettings from "@/components/science/ScienceSettings";
import { toast } from "@/components/Nav";
import type { Component, Config, Connector, InvFull } from "@/lib/science";

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

const CONN = (id: string, over: Partial<Connector> = {}): Connector => ({ id, label: id.toUpperCase(), domain: "biology", summary: `${id} summary`, interval_s: 0.5, limits: "2 per second", licence: "CC0", ops: ["search", "lookup"],
  needs_key: false, key_set: false, key_help: "", enabled: true, status: "ready", cache: true, verified: true, ...over });
const CONNECTORS = [CONN("pubmed"), CONN("alphafold", { ops: ["lookup"], verified: false }), CONN("ads", { domain: "physics", needs_key: true, status: "needs_key", key_help: "Free ADS token" }), CONN("arxiv", { domain: "physics", status: "off", enabled: false })];
const CFG: Config = { enabled: true, safety_level: "standard", disabled_connectors: ["arxiv"], rate_overrides: {}, contact_email: "", max_calls_per_investigation: 60, cache_minutes: 60, max_investigations: 200,
  safety_levels: ["standard", "strict"], domains: [], labels: {}, key_set: [], policy: { blocked: ["Making hazards"], confirm: ["Wet-lab with hazards"], allowed: ["Education"], tools_not_offered: ["Retrosynthesis"], limits: ["Can be bypassed."] } };

describe("SearchPanel", () => {
  const REC = { id: "111", title: "Paper 111", summary: "Smith J. J Fixture", url: "https://pubmed.ncbi.nlm.nih.gov/111/", kind: "paper", fields: { doi: "10.1/x", year: "2020" } };
  const OK = { ok: true, records: [REC, { ...REC, id: "222", title: "", url: "javascript:alert(1)", fields: {} }], run_id: "run_1", provenance: { connector: "pubmed", label: "PubMed", retrieved_at: Date.now() / 1000, licence: "NCBI", requests: 2, query: "p53", note: "" } };

  it("asks a source, shows provenance, and adds a result as evidence", async () => {
    const { calls } = routeFetch({ "POST /api/science/search": () => jsonResponse(OK), "POST /api/science/investigations/inv1/evidence": () => jsonResponse({ id: "ev1" }) });
    const onEvidence = vi.fn();
    const user = userEvent.setup();
    render(<SearchPanel connectors={CONNECTORS} investigationId="inv1" onEvidence={onEvidence} />);
    await user.type(screen.getByLabelText("Search text"), "p53");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    const res = await screen.findByTestId("science-results");
    expect(res).toHaveTextContent("2 from PubMed");
    expect(res).toHaveTextContent("2 request(s)");
    expect(screen.getByText("Paper 111").closest("[role=listitem]")).toHaveTextContent("doi:10.1/x");
    expect(screen.getAllByText("Open at the source ↗")).toHaveLength(1);       // the javascript: link is not rendered
    expect(calls[0].body).toMatchObject({ source: "pubmed", operation: "search", query: "p53", investigation_id: "inv1" });
    await user.click(screen.getAllByText("Add as evidence")[0]);
    await waitFor(() => expect(onEvidence).toHaveBeenCalled());
    expect(calls[1].body).toEqual({ kind: "source", run_id: "run_1", ref: "111" });
    await user.click(screen.getByRole("button", { name: "Table" }));
    expect(screen.getByTestId("uiblock")).toHaveTextContent("table:Results:2");
    await user.click(screen.getByRole("button", { name: "Cards" }));
  });

  it("shows the sources' own trouble in plain words and never a blank result", async () => {
    const answers: any[] = [{ ok: false, error: "PubMed is rate-limiting requests right now", error_kind: "rate_limited" }, { ok: false, blocked: true, safety: { reason: "Blocked: no.", help: "Education is fine." } },
      { ok: true, records: [], run_id: "r", provenance: { connector: "pubmed", label: "PubMed", retrieved_at: 1, licence: "NCBI", requests: 0, query: "x", note: "No data found: nothing matched." } }];
    routeFetch({ "POST /api/science/search": () => jsonResponse(answers.shift()) });
    const user = userEvent.setup();
    render(<SearchPanel connectors={CONNECTORS} investigationId="" onEvidence={() => undefined} />);
    await user.type(screen.getByLabelText("Search text"), "q{Enter}");
    expect(await screen.findByTestId("science-search-error")).toHaveTextContent("Wait a minute");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await waitFor(() => expect(screen.getByTestId("science-search-error")).toHaveTextContent("Education is fine."));
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/No data found: nothing matched/)).toBeInTheDocument();
    expect(screen.queryByText("Add as evidence")).toBeNull();
  });

  it("explains sources that are off or need a key, adapts the operations and reports a failed request", async () => {
    routeFetch({ "POST /api/science/search": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    const user = userEvent.setup();
    render(<SearchPanel connectors={CONNECTORS} investigationId="inv1" onEvidence={() => undefined} />);
    await user.selectOptions(screen.getByLabelText("Data source"), "ads");
    expect(screen.getByText(/needs a free key \(Free ADS token\)/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search text"), "x");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Data source"), "arxiv");
    expect(screen.getByText(/switched off in the Settings tab/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Data source"), "alphafold");
    expect(screen.queryByLabelText("Operation")).toBeNull();                   // it only supports lookup
    expect(screen.getByText(/not yet verified from the source/)).toBeInTheDocument();
    expect(screen.getByLabelText("Search text")).toHaveAttribute("placeholder", "P04637");
    await user.selectOptions(screen.getByLabelText("Data source"), "pubmed");
    await user.selectOptions(screen.getByLabelText("Operation"), "lookup");
    expect(screen.getByText("Identifier")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByTestId("science-search-error")).toHaveTextContent("boom");
  });

  it("reports when adding evidence fails", async () => {
    routeFetch({ "POST /api/science/search": () => jsonResponse(OK), "POST /api/science/investigations/inv1/evidence": () => jsonResponse({ detail: "not returned by this call" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<SearchPanel connectors={CONNECTORS} investigationId="inv1" onEvidence={() => undefined} />);
    await user.type(screen.getByLabelText("Search text"), "p53");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await user.click((await screen.findAllByText("Add as evidence"))[0]);
    await waitFor(() => expect(toast).toHaveBeenCalledWith("not returned by this call", "err"));
  });
});

describe("ComputePanel", () => {
  it("computes locally, labels the result, and charts a table", async () => {
    const answers: Record<string, any> = {
      calc: { label: "computed here", kind: "calc", result: 12.5, expression: "2*pi*2" },
      table: { label: "computed here", kind: "table", x: [0, 1, 2], y: [0, 0.84, 0.91] },
      formula_mass: { label: "computed here", kind: "formula_mass", formula: "H2O", molar_mass_g_per_mol: 18.015, atoms: { H: 2, O: 1 }, percent: { H: 11.2, O: 88.8 }, source: "IUPAC" },
      constant: { label: "computed here", kind: "constant", symbol: "c", name: "speed of light", value: 299792458, unit: "m/s", exact: true },
    };
    const { calls } = routeFetch({ "GET /api/science/constants": () => jsonResponse({ source: "CODATA 2022", constants: [{ symbol: "c", name: "speed of light", value: 1, unit: "m/s", exact: true }, { symbol: "h", name: "Planck", value: 1, unit: "J s", exact: true }] }),
      "POST /api/science/calc": (init) => jsonResponse(answers[JSON.parse(String(init?.body)).kind]) });
    const user = userEvent.setup();
    render(<ComputePanel />);
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByTestId("science-compute-result")).toHaveTextContent("computed here");
    expect(screen.getByText("12.5")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Table and chart" }));
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByTestId("uiblock")).toHaveTextContent("chart.line:sin(x):3");
    await user.click(screen.getByRole("tab", { name: "Molar mass" }));
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByText(/Atoms: H2 O1/)).toBeInTheDocument();
    expect(screen.getByText("Source: IUPAC")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Constants" }));
    await waitFor(() => expect(screen.getByLabelText("Constant")).toHaveValue("c"));
    await user.selectOptions(screen.getByLabelText("Constant"), "h");
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByText("CODATA 2022")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body.kind)).toEqual(["calc", "table", "formula_mass", "constant"]);
  });

  it("shows other kinds' inputs, rejects bad variables before sending, and shows the server's reason", async () => {
    const { calls } = routeFetch({ "GET /api/science/constants": () => jsonResponse({ source: "x", constants: [] }), "POST /api/science/calc": () => jsonResponse({ detail: "division by zero" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<ComputePanel />);
    fireEvent.change(screen.getByLabelText("Variables"), { target: { value: "[1]" } });
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(screen.getByRole("alert")).toHaveTextContent("JSON object");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    await user.clear(screen.getByLabelText("Variables"));
    await user.click(screen.getByRole("button", { name: "Compute" }));
    expect(await screen.findByText("division by zero")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Units" }));
    expect(screen.getByLabelText("From unit")).toHaveValue("eV");
    expect(screen.getByLabelText("To unit")).toHaveValue("J");
    await user.click(screen.getByRole("tab", { name: "DNA / RNA sequence" }));
    expect(screen.getByLabelText("Sequence")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Derivative" }));
    expect(screen.getByLabelText("Start")).toBeInTheDocument();
    expect(screen.queryByLabelText("End")).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Integral" }));
    expect(screen.getByLabelText("End")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Constants" }));
    expect(screen.getByLabelText("Constant")).toBeInTheDocument();
  });
});

const INV = (over: Partial<InvFull> = {}): InvFull => ({
  id: "inv1", title: "Aspirin", domain: "chemistry", question: "What is it?", status: "open", autonomy: "approve", session_id: "", agent: "", created: 1, updated: 2, evidence: 2, claims: 3, hypotheses: 2, source_calls: 1,
  claims_list: [
    { id: "c1", text: "Established fact", evidence: ["e1", "e2"], conflicting: false, label: "established", label_text: "Established: two or more independent sources agree", flags: [] },
    { id: "c2", text: "Bad cite", evidence: ["e3"], conflicting: false, label: "unsupported", label_text: "Unsupported: no usable evidence", flags: ["a citation could not be verified: doi:10.404/x"] },
    { id: "c3", text: "Odd", evidence: [], conflicting: false, label: "weird", label_text: "Weird", flags: [] }],
  evidence_list: [
    { id: "e1", inv_id: "inv1", kind: "source", source: "pubmed", ref: "pmid:1", title: "Paper one", url: "https://pubmed.ncbi.nlm.nih.gov/1/", tool: "source:pubmed", query: "aspirin", quote: "", value: "", run_id: "r", retrieved_at: 1700000000, verified: 1, verified_note: "" },
    { id: "e2", inv_id: "inv1", kind: "computed", source: "sandbox", ref: "", title: "", url: "", tool: "python", query: "", quote: "", value: "4", run_id: "r2", retrieved_at: 1700000000, verified: -1, verified_note: "" },
    { id: "e3", inv_id: "inv1", kind: "source", source: "crossref", ref: "doi:10.404/x", title: "", url: "javascript:x", tool: "", query: "", quote: "", value: "", run_id: "r", retrieved_at: 0, verified: 0, verified_note: "" }],
  hypotheses_list: [{ id: "h1", text: "H one", elo: 1216, wins: 1, losses: 0 }, { id: "h2", text: "H two", elo: 1184, wins: 0, losses: 1 }],
  runs_list: [{ id: "r", tool: "source:pubmed", created: 1, record: { query: "aspirin", ok: true } }, { id: "r2", tool: "python", created: 2, record: { code_sha256: "abcdef1234567890" } }, { id: "r3", tool: "source:x", created: 3, record: { ok: false, error: "down" } }], ...over });

describe("InvestigationPanel", () => {
  it("shows each claim with the label its evidence earned, its flags and its evidence", () => {
    render(<InvestigationPanel inv={INV()} reload={() => undefined} />);
    const est = screen.getByTestId("claim-established");
    expect(est).toHaveTextContent("Established: two or more independent sources agree");
    expect(est).toHaveTextContent("Paper one (pubmed, verified)");
    expect(est).toHaveTextContent("(sandbox, computed)");
    expect(screen.getByTestId("claim-unsupported")).toHaveTextContent("a citation could not be verified: doi:10.404/x");
    expect(screen.getByTestId("claim-weird")).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "Evidence" })).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[1]).toHaveTextContent("verified");
    expect(rows[3]).toHaveTextContent("could not verify");
    expect(within(rows[3]).queryByRole("link")).toBeNull();                      // a javascript: url is never linked
    expect(within(rows[1]).getByRole("link")).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText(/Record of every call and run \(3\)/)).toBeInTheDocument();
    expect(screen.getByText(/failed: down/)).toBeInTheDocument();
  });

  it("records claims, hypotheses and judgements, and checks citations", async () => {
    const { calls } = routeFetch({
      "POST /api/science/investigations/inv1/claims": () => jsonResponse({ id: "c9" }), "POST /api/science/investigations/inv1/hypotheses": () => jsonResponse({ id: "h9" }),
      "POST /api/science/investigations/inv1/compare": () => jsonResponse([]), "POST /api/science/investigations/inv1/verify": () => jsonResponse([{ verified: 1 }]),
      "POST /api/science/investigations/inv1/evidence": () => jsonResponse({ id: "e9" }), "DELETE /api/science/investigations/inv1/claims/c1": () => jsonResponse({ ok: true }) });
    const reload = vi.fn();
    const user = userEvent.setup();
    render(<InvestigationPanel inv={INV()} reload={reload} />);
    await user.type(screen.getByLabelText("Claim"), "New claim");
    await user.click(screen.getByLabelText("Evidence Paper one"));
    await user.click(screen.getByLabelText("Evidence Paper one"));
    await user.click(screen.getByLabelText("Evidence Paper one"));
    await user.click(screen.getByLabelText("The evidence disagrees"));
    await user.click(screen.getByRole("button", { name: "Add claim" }));
    await waitFor(() => expect(calls.at(-1)!.body).toEqual({ text: "New claim", evidence_ids: ["e1"], conflicting: true }));
    await user.type(screen.getByLabelText("New hypothesis"), "H three");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.at(-1)!.url.pathname).toMatch(/hypotheses$/));
    const first = screen.getByLabelText("Select hypothesis: H one"), second = screen.getByLabelText("Select hypothesis: H two");
    await user.click(second);
    await user.click(first);
    await user.click(screen.getByRole("button", { name: "First beats second" }));
    await waitFor(() => expect(calls.at(-1)!.body).toEqual({ winner: "h2", loser: "h1" }));
    await user.click(screen.getByRole("button", { name: /Check citations/ }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Checked 1 citation(s)"));
    await user.type(screen.getByLabelText("Your own reasoning"), "I think so");
    await user.click(screen.getByRole("button", { name: "Add as inference" }));
    await waitFor(() => expect(calls.at(-1)!.body).toMatchObject({ kind: "inference", quote: "I think so" }));
    await user.click(screen.getAllByRole("button", { name: "Remove claim" })[0]);
    await waitFor(() => expect(calls.at(-1)!.method).toBe("DELETE"));
    expect(reload.mock.calls.length).toBeGreaterThanOrEqual(5);
  });

  it("explains an empty investigation and reports failures as toasts", async () => {
    routeFetch({ "POST /api/science/investigations/inv1/claims": () => jsonResponse({ detail: "no such evidence" }, { status: 422 }), "POST /api/science/investigations/inv1/verify": () => jsonResponse([]),
      "POST /api/science/investigations/inv1/hypotheses": () => jsonResponse({ detail: "Blocked" }, { status: 422 }), "POST /api/science/investigations/inv1/evidence": () => jsonResponse({ detail: "bad" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<InvestigationPanel inv={INV({ question: "", claims_list: [], evidence_list: [], hypotheses_list: [], runs_list: [] })} reload={() => undefined} />);
    expect(screen.getByText("No question written yet.")).toBeInTheDocument();
    expect(screen.getByText("No hypotheses yet.")).toBeInTheDocument();
    expect(screen.getByText(/A claim needs evidence/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Check citations/ })).toBeDisabled();
    await user.type(screen.getByLabelText("Claim"), "x");
    await user.click(screen.getByRole("button", { name: "Add claim" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("no such evidence", "err"));
    await user.type(screen.getByLabelText("New hypothesis"), "x");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Blocked", "err"));
    await user.type(screen.getByLabelText("Your own reasoning"), "x");
    await user.click(screen.getByRole("button", { name: "Add as inference" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("bad", "err"));
  });

  it("reports a failed citation check and a failed claim removal", async () => {
    routeFetch({ "POST /api/science/investigations/inv1/verify": () => jsonResponse({ detail: "down" }, { status: 502 }), "DELETE /api/science/investigations/inv1/claims/c1": () => jsonResponse({ detail: "no such claim" }, { status: 404 }) });
    const user = userEvent.setup();
    render(<InvestigationPanel inv={INV()} reload={() => undefined} />);
    await user.click(screen.getByRole("button", { name: /Check citations/ }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("down", "err"));
    await user.click(screen.getAllByRole("button", { name: "Remove claim" })[0]);
    await waitFor(() => expect(toast).toHaveBeenCalledWith("no such claim", "err"));
  });
});

describe("ReportPanel", () => {
  const REP = { markdown: "# Aspirin\n\nA **finding**.", label_counts: { single_source: 1, unsupported: 2, established: 1 }, unsupported: 2 };
  it("shows the report with label counts and keeps it as artifacts", async () => {
    const { calls } = routeFetch({ "GET /api/science/investigations/inv1/report": () => jsonResponse(REP), "POST /api/science/investigations/inv1/notebook": () => jsonResponse({ files: [{ name: "a" }, { name: "b" }, { name: "c" }] }) });
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<ReportPanel investigationId="inv1" refreshKey={0} />);
    expect(await screen.findByRole("heading", { name: "Aspirin" })).toBeInTheDocument();
    expect(screen.getByTestId("science-report")).toHaveTextContent("single source: 1");
    expect(screen.getByRole("note")).toHaveTextContent("2 claim(s) have no usable evidence");
    await user.click(screen.getByRole("button", { name: "Keep as artifacts" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Kept 3 files on the Artifacts page"));
    await user.click(screen.getByRole("button", { name: "Copy Markdown" }));
    expect(writeText).toHaveBeenCalledWith(REP.markdown);
    expect(calls.map((c) => c.method)).toContain("POST");
  });

  it("explains errors", async () => {
    routeFetch({ "GET /api/science/investigations/inv1/report": () => jsonResponse({ detail: "no such investigation" }, { status: 404 }) });
    render(<ReportPanel investigationId="inv1" refreshKey={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("no such investigation");
  });

  it("reports a failed save", async () => {
    routeFetch({ "GET /api/science/investigations/inv1/report": () => jsonResponse({ ...REP, unsupported: 0, label_counts: {} }), "POST /api/science/investigations/inv1/notebook": () => jsonResponse({ detail: "artifacts are switched off" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<ReportPanel investigationId="inv1" refreshKey={0} />);
    await user.click(await screen.findByRole("button", { name: "Keep as artifacts" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("artifacts are switched off", "err"));
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("SafetyPanel", () => {
  it("shows the policy, tests a request and raises the level (never lowers it)", async () => {
    const answers = [{ action: "block", category: "toxin", reason: "Blocked: no.", help: "Education is fine." }, { action: "confirm", category: "wetlab", reason: "Confirm.", help: "x" }, { action: "allow", category: "", reason: "", help: "" }];
    routeFetch({ "POST /api/science/screen": () => jsonResponse(answers.shift()), "PUT /api/science/config": () => jsonResponse({ ...CFG, safety_level: "strict" }) });
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<SafetyPanel cfg={CFG} onChange={onChange} />);
    expect(screen.getByText("Making hazards")).toBeInTheDocument();
    expect(screen.getByText("Retrosynthesis")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Can be bypassed.");
    await user.type(screen.getByLabelText("Request to check"), "something");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByTestId("screen-result")).toHaveTextContent("Blocked: Blocked: no. Education is fine.");
    await user.click(screen.getByRole("button", { name: "Check" }));
    await waitFor(() => expect(screen.getByTestId("screen-result")).toHaveTextContent("Needs a person's confirmation"));
    await user.click(screen.getByRole("button", { name: "Check" }));
    await waitFor(() => expect(screen.getByTestId("screen-result")).toHaveTextContent("Allowed"));
    await user.click(screen.getByRole("button", { name: /Raise to strict/ }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ safety_level: "strict" })));
    expect(toast).toHaveBeenCalledWith("Safety level raised to strict");
  });

  it("shows a strict level as final and reports errors", async () => {
    routeFetch({ "POST /api/science/screen": () => jsonResponse({ detail: "nope" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<SafetyPanel cfg={{ ...CFG, safety_level: "strict" }} onChange={() => undefined} />);
    expect(screen.getByRole("button", { name: "Already strict" })).toBeDisabled();
    await user.type(screen.getByLabelText("Request to check"), "x");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
  });

  it("reports a failed raise", async () => {
    routeFetch({ "PUT /api/science/config": () => jsonResponse({ detail: "cannot" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<SafetyPanel cfg={CFG} onChange={() => undefined} />);
    await user.click(screen.getByRole("button", { name: /Raise to strict/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot");
  });
});

const COMP = (id: string, over: Partial<Component> = {}): Component => ({ id, name: id.toUpperCase(), kind: "self-hostable", summary: `${id} does things`, can_install: true, reasons: [], licence: "Apache-2.0", licence_verified: true,
  licence_source: "PyPI", hardware: "CPU", disk: "1 GB", keys: "A key", egress: "To the model provider", cost: "Free", caution: "", link: "https://example.org/x", steps: ["Create env", "Install"], state: "not_installed",
  installed_version: "", error: "", installing: false, command: "", version: "1", package: id, ...over });

describe("ComponentsPanel", () => {
  it("shows the plan and needs the licence tick before Install works", async () => {
    routeFetch({ "GET /api/science/components": () => jsonResponse({ installing: "", log: [], components: [COMP("paperqa")] }) });
    const user = userEvent.setup();
    render(<ComponentsPanel />);
    expect(await screen.findByTestId("component-paperqa")).toHaveTextContent("Apache-2.0");
    expect(screen.getByTestId("component-paperqa")).toHaveTextContent("confirmed: PyPI");
    expect(screen.getByText(/Claude Science and Google's co-scientist are not part of this lab/)).toBeInTheDocument();
    expect(screen.getByText("Plan: Create env → Install.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install" })).toBeDisabled();
    expect(screen.getByRole("link", { name: /Project page/ })).toHaveAttribute("rel", "noopener noreferrer");
    await user.click(screen.getByLabelText("I accept the Apache-2.0 licence"));
    expect(screen.getByRole("button", { name: "Install" })).toBeEnabled();
  });

  it("installs after the licence is accepted, polls while installing and removes", async () => {
    const states = [{ installing: "", log: [], components: [COMP("paperqa")] }];
    let installing = false, ready = false;
    routeFetch({
      "GET /api/science/components": () => jsonResponse(ready ? { installing: "", log: [], components: [COMP("paperqa", { state: "ready", installed_version: "1.0", command: "py -m paperqa" })] } : installing ? { installing: "paperqa", log: ["a", "b"], components: [COMP("paperqa", { installing: true })] } : states[0]),
      "POST /api/science/components/paperqa/install": () => { installing = true; setTimeout(() => { ready = true; }, 50); return jsonResponse({ installing: "paperqa", log: ["x"], components: [COMP("paperqa", { installing: true })] }); },
      "DELETE /api/science/components/paperqa": () => { ready = false; installing = false; return jsonResponse(states[0]); } });
    const user = userEvent.setup();
    render(<ComponentsPanel />);
    await screen.findByTestId("component-paperqa");
    await user.click(screen.getByLabelText("I accept the Apache-2.0 licence"));
    await user.click(screen.getByRole("button", { name: "Install" }));
    expect(await screen.findByRole("button", { name: "Installing…" })).toBeDisabled();
    expect(screen.getByLabelText("Install log")).toHaveTextContent("x");
    expect(await screen.findByText("Start it with:", {}, { timeout: 9000 })).toBeInTheDocument();
    expect(screen.getByText("installed 1.0")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Removed"));
  }, 20000);

  it("lists what cannot be installed with the reason, shows failures and reports errors", async () => {
    routeFetch({ "GET /api/science/components": () => jsonResponse({ installing: "", log: [], components: [COMP("biomni", { can_install: false, reasons: ["Not installable yet: needs conda."], licence_verified: false, caution: "Runs LLM-written code.", link: "http://insecure" }),
      COMP("edison", { kind: "hosted", can_install: false, reasons: ["Hosted service."], steps: [], hardware: "", disk: "", keys: "", egress: "", cost: "" }), COMP("paperqa", { state: "failed", error: "pip failed\nline 2" })] }) });
    render(<ComponentsPanel />);
    const b = await screen.findByTestId("component-biomni");
    expect(b).toHaveTextContent("Not installable yet: needs conda.");
    expect(b).toHaveTextContent("(not confirmed)");
    expect(b).toHaveTextContent("⚠ Runs LLM-written code.");
    expect(within(b).queryByRole("link")).toBeNull();
    expect(within(b).queryByRole("button", { name: "Install" })).toBeNull();
    expect(screen.getByTestId("component-edison")).toHaveTextContent("hosted service");
    expect(screen.getByTestId("component-paperqa")).toHaveTextContent("pip failed");
  });

  it("shows a load error and an install error", async () => {
    routeFetch({ "GET /api/science/components": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<ComponentsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });

  it("reports a refused install and a refused removal", async () => {
    routeFetch({ "GET /api/science/components": () => jsonResponse({ installing: "", log: [], components: [COMP("paperqa"), COMP("tooluniverse", { state: "ready", installed_version: "1", command: "" })] }),
      "POST /api/science/components/paperqa/install": () => jsonResponse({ detail: "it cannot be installed: x" }, { status: 422 }), "DELETE /api/science/components/tooluniverse": () => jsonResponse({ detail: "busy" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<ComponentsPanel />);
    await screen.findByTestId("component-paperqa");
    await user.click(screen.getByLabelText("I accept the Apache-2.0 licence"));
    await user.click(screen.getByRole("button", { name: "Install" }));
    expect(await screen.findByText("it cannot be installed: x")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByText("busy")).toBeInTheDocument();
  });
});

describe("ScienceSettings", () => {
  it("saves switches, slower request intervals, write-only keys and budgets; the level can only go up", async () => {
    const { calls } = routeFetch({ "PUT /api/science/keys/ads": () => jsonResponse({ ok: true, connectors: [CONN("ads", { key_set: true, status: "ready", domain: "physics" })] }), "PUT /api/science/config": (init) => jsonResponse({ ...CFG, ...JSON.parse(String(init?.body)) }) });
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ScienceSettings cfg={CFG} connectors={CONNECTORS} onChange={onChange} />);
    expect(screen.getByRole("option", { name: "standard" })).not.toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Safety level"), "strict");
    await user.click(screen.getByLabelText("Use PUBMED"));
    await user.click(screen.getByLabelText("Use ARXIV"));
    const gap = screen.getByLabelText("Seconds between requests to PUBMED");
    await user.clear(gap); await user.type(gap, "2");
    const keep = screen.getByLabelText("Seconds between requests to ALPHAFOLD");
    await user.clear(keep); await user.type(keep, "0.5");
    await user.type(screen.getByLabelText("Key for ADS"), "adskey");
    await user.type(screen.getByLabelText("Contact email"), "me@example.org");
    await user.clear(screen.getByLabelText("Calls per investigation")); await user.type(screen.getByLabelText("Calls per investigation"), "9");
    await user.clear(screen.getByLabelText("Cache minutes")); await user.type(screen.getByLabelText("Cache minutes"), "5");
    await user.clear(screen.getByLabelText("Most investigations")); await user.type(screen.getByLabelText("Most investigations"), "7");
    await user.click(screen.getByRole("button", { name: "Save Science settings" }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const cfgCall = calls.find((c) => c.url.pathname === "/api/science/config")!;
    expect(cfgCall.body).toMatchObject({ safety_level: "strict", disabled_connectors: ["pubmed"], contact_email: "me@example.org", max_calls_per_investigation: 9, cache_minutes: 5, max_investigations: 7 });
    expect(cfgCall.body.rate_overrides).toEqual({ pubmed: 2 });
    expect(JSON.stringify(cfgCall.body)).not.toContain("adskey");
    expect(calls.find((c) => c.url.pathname === "/api/science/keys/ads")!.body).toEqual({ key: "adskey" });
    expect(toast).toHaveBeenCalledWith("Science settings saved");
    expect(screen.getByLabelText("Key for ADS")).toHaveValue("");                // the key box is cleared after saving
  });

  it("does not offer a lower level than the current one and shows the server's reason on a failed save", async () => {
    routeFetch({ "PUT /api/science/config": () => jsonResponse({ detail: "unknown science setting" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<ScienceSettings cfg={{ ...CFG, safety_level: "strict" }} connectors={[CONN("pubmed", { key_help: "Optional key", key_set: true })]} onChange={() => undefined} />);
    expect(screen.getByRole("option", { name: "standard" })).toBeDisabled();
    expect(screen.getByLabelText("Key for PUBMED")).toHaveAttribute("placeholder", "set (type to replace)");
    await user.click(screen.getByLabelText("The Science Lab is on"));
    await user.click(screen.getByRole("button", { name: "Save Science settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown science setting");
  });

  it("shows no key box for sources that need none", () => {
    render(<ScienceSettings cfg={CFG} connectors={[CONN("arxiv")]} onChange={() => undefined} />);
    expect(screen.getByText("none needed")).toBeInTheDocument();
  });
});
