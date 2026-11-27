import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DiscoveryPage from "@/app/discovery/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/discovery/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

const levels = [{ id: "public", description: "Safe to share with anyone." }, { id: "internal", description: "For your own organisation." }, { id: "full", description: "Plus instructions." }];
const scopes = { agents: [{ ref: "agent:engineer", name: "Engineer" }], mcp: [{ ref: "mcp:git", name: "Git" }], models: [], skills: [] };
const P = (name: string, value: string) => ({ name: `agentstudio:${name}`, value });
const bom = (over: Record<string, unknown> = {}) => ({
  specVersion: "1.7", serialNumber: "urn:uuid:3e671687-395b-41f5-a30f-a58921a69b79",
  metadata: { timestamp: "2026-11-22T00:00:00Z", properties: [P("limitations", JSON.stringify(["No hashes for Python wheels.", "No vulnerability data."]))] },
  components: [
    { "bom-ref": "pkg:pypi/fastapi@1", name: "fastapi", type: "library", licenses: [{ license: { id: "MIT" } }] },
    { "bom-ref": "model:openai:gpt-5", name: "gpt-5", type: "machine-learning-model", group: "openai", properties: [P("hosted", "true")], licenses: [{ expression: "MIT OR Apache-2.0" }] },
    { "bom-ref": "pkg:huggingface/LiquidAI/d1@x", name: "d1-2B", type: "machine-learning-model", group: "LiquidAI", properties: [P("runs-locally", "true")], licenses: [{ license: { name: "LFM Open License" } }] },
    { "bom-ref": "model:ollama:llama", name: "llama3.2", type: "machine-learning-model", group: "ollama", properties: [P("hosted", "false")] },
  ],
  services: [
    { "bom-ref": "agent:engineer", name: "Engineer", properties: [P("tier", "pro")] },
    { "bom-ref": "mcp:git", name: "Git", properties: [P("enabled", "false")] },
  ],
  dependencies: [{ ref: "a" }, { ref: "b" }, { ref: "c" }],
  ...over,
});
const bomResponse = (b: unknown = bom()) => new Response(JSON.stringify(b), { headers: { "Content-Type": "application/vnd.cyclonedx+json", "X-AIBOM-SHA256": "ab".repeat(32) } });

function setup(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/aibom/levels": () => jsonResponse(levels),
    "GET /api/aibom/scopes": () => jsonResponse(scopes),
    "GET /api/aibom": () => bomResponse(),
    "GET /api/aibom/validate": () => jsonResponse({ ok: true, schema: "CycloneDX 1.7", error_count: 0, errors: [], warnings: [] }),
    ...extra,
  });
}
const goTab = (name: string) => userEvent.click(screen.getByRole("tab", { name }));

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Discovery page: AI-BOM", () => {
  it("previews a BOM: counts, services, models with where they run, limits, and the JSON", async () => {
    const { calls } = setup();
    render(<DiscoveryPage />);
    expect(screen.getByText(/Previewing never publishes anything/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    const summary = await screen.findByLabelText("BOM summary");
    expect(summary).toHaveTextContent("4 components");
    expect(summary).toHaveTextContent("2 services");
    expect(summary).toHaveTextContent("3 graph nodes");
    expect(summary).toHaveTextContent("3e671687-395…");
    expect(summary).toHaveTextContent("abababababab…");
    expect(screen.getByText("1 library · 3 machine-learning-model")).toBeInTheDocument();
    expect(calls.some((c) => c.url.pathname === "/api/aibom" && c.url.search === "?level=public&scope=system")).toBe(true);
    expect(screen.getByText("pro")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(screen.getByText("MIT OR Apache-2.0")).toBeInTheDocument();
    expect(screen.getByText("LFM Open License")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
    expect(screen.getAllByText(/runs locally/)).toHaveLength(2);
    expect(screen.getByText(/openai · hosted/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("What this BOM cannot tell you"));
    expect(screen.getByText("No vulnerability data.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show JSON" }));
    expect(screen.getByLabelText("BOM JSON").textContent).toContain('"specVersion": "1.7"');
    await userEvent.click(screen.getByRole("button", { name: "Hide JSON" }));
    expect(screen.queryByLabelText("BOM JSON")).not.toBeInTheDocument();
  });

  it("truncates a huge JSON view and says how to get the rest", async () => {
    setup({ "GET /api/aibom": () => bomResponse(bom({ components: Array.from({ length: 15000 }, (_, i) => ({ "bom-ref": `c${i}`, name: `component-number-${i}`, type: "library" })) })) });
    render(<DiscoveryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    await userEvent.click(await screen.findByRole("button", { name: "Show JSON" }));
    expect(screen.getByLabelText("BOM JSON").textContent).toMatch(/in total: download for the rest/);
  });

  it("handles a BOM with no services, no models and no limitations", async () => {
    setup({ "GET /api/aibom": () => new Response(JSON.stringify({ specVersion: "1.7", serialNumber: "urn:uuid:123456789012345678901234567890123456", metadata: { timestamp: "t" },
      components: [{ "bom-ref": "x", name: "x" }], dependencies: [] })) });          // no X-AIBOM-SHA256 header
    render(<DiscoveryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByText("No models are configured yet.")).toBeInTheDocument();
    expect(screen.getByText("Services (0)")).toBeInTheDocument();
    expect(screen.queryByText("What this BOM cannot tell you")).not.toBeInTheDocument();
    expect(screen.getByText("1 ?")).toBeInTheDocument();
  });

  it("level and scope choices change the request, grouped by kind", async () => {
    const { calls } = setup();
    render(<DiscoveryPage />);
    const scope = await screen.findByLabelText("Scope");
    await waitFor(() => expect(within(scope).getByRole("option", { name: "Engineer" })).toBeInTheDocument());
    expect(within(scope).queryByRole("group", { name: "models" })).not.toBeInTheDocument();            // empty groups are not shown
    expect(within(scope).getByRole("group", { name: "agents" })).toBeInTheDocument();
    await userEvent.selectOptions(scope, "agent:engineer");
    await userEvent.selectOptions(screen.getByLabelText("Level"), "internal");
    expect(screen.getByText("For your own organisation.")).toBeInTheDocument();
    expect(screen.getByLabelText("Scope")).toHaveValue("system");                                           // a level change resets the scope
    await userEvent.selectOptions(screen.getByLabelText("Scope"), "mcp:git");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByLabelText("BOM summary");
    expect(calls.some((c) => c.url.pathname === "/api/aibom" && c.url.search === "?level=internal&scope=mcp%3Agit")).toBe(true);
    expect(screen.getByRole("link", { name: "Download JSON" }).getAttribute("href")).toContain("/api/aibom/download?level=internal&scope=mcp%3Agit");
  });

  it("validates, showing errors and warnings", async () => {
    setup({ "GET /api/aibom/validate": () => jsonResponse({ ok: false, schema: "CycloneDX 1.7", error_count: 2, errors: [{ path: "/components/0/type", message: "bad type" }], warnings: [{ path: "/x", message: "heads up" }] }) });
    render(<DiscoveryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Validate" }));
    const panel = await screen.findByLabelText("BOM validation");
    expect(panel).toHaveTextContent("2 errors");
    expect(panel).toHaveTextContent("/components/0/type: bad type");
    expect(panel).toHaveTextContent("warning · /x: heads up");
  });

  it("says a BOM is valid, and singular errors read correctly", async () => {
    setup();
    render(<DiscoveryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Validate" }));
    expect(await screen.findByText("valid")).toBeInTheDocument();
    setup({ "GET /api/aibom/validate": () => jsonResponse({ ok: false, error_count: 1, errors: [] }) });
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByText("1 error")).toBeInTheDocument();
  });

  it("shows request failures (a message from the server, a bare status, a network error)", async () => {
    setup({ "GET /api/aibom": () => jsonResponse({ detail: "unknown scope 'nope'" }, { status: 422 }), "GET /api/aibom/validate": () => jsonResponse({ detail: "no" }, { status: 422 }) });
    render(<DiscoveryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown scope 'nope'");
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no"));
    setup({ "GET /api/aibom": () => textResponse("<html>", 500), "GET /api/aibom/scopes": () => jsonResponse({ detail: "scopes broke" }, { status: 500 }) });
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500"));
  });

  it("reports a scope list that cannot load", async () => {
    setup({ "GET /api/aibom/scopes": () => jsonResponse({ detail: "scopes broke" }, { status: 500 }) });
    render(<DiscoveryPage />);
    expect(await screen.findByText("scopes broke")).toBeInTheDocument();
  });

  it("copes with the levels list failing to load", async () => {
    routeFetch({ "GET /api/aibom/levels": () => jsonResponse({ detail: "x" }, { status: 500 }), "GET /api/aibom/scopes": () => jsonResponse({}) });
    render(<DiscoveryPage />);
    expect(await screen.findByLabelText("Level")).toBeInTheDocument();
  });
});

// ------------------------------------------------------------------------------------------------ catalog
const entry = (n: number, over: Record<string, unknown> = {}) => ({ identifier: `urn:air:agents.acme-corp.dev:agent-studio:agent:a${n}`, type: "application/vnd.agent-studio.agent+json",
  displayName: `Agent ${n}`, url: "https://agents.acme-corp.dev/.well-known/ard.json", inline: true, digests: [], ...over });
const ok = { ok: true, error_count: 0, errors: [], warnings: [] };
const catalog = (over: Record<string, unknown> = {}) => ({
  manifest: { entries: [{}] }, entries: [entry(1), entry(2, { inline: false, url: "https://agents.acme-corp.dev/ard/bom/system.json", displayName: "System BOM" })],
  reasons: [], publishable: true, validation: { ok: true, manifest: ok, boms: { a: ok, b: ok } }, level: "public", publisher: "agents.acme-corp.dev", signed: false, problems: [], ...over,
});

describe("Discovery page: ARD catalog", () => {
  it("lists entries with copyable URLs and shows validation", async () => {
    const writeText = vi.fn();
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    setup({ "GET /api/discovery/manifest": () => jsonResponse(catalog()) });
    render(<DiscoveryPage />);
    await goTab("ARD catalog");
    expect(await screen.findByText("ready to publish")).toBeInTheDocument();
    expect(screen.getByText("2 BOM files: all valid CycloneDX 1.7")).toBeInTheDocument();
    expect(screen.getByText("not signed")).toBeInTheDocument();
    const list = screen.getByLabelText("Catalog entries");
    expect(within(list).getByText("urn:air:agents.acme-corp.dev:agent-studio:agent:a1")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Copy URL of Agent 1"));
    expect(writeText).toHaveBeenCalledWith("https://agents.acme-corp.dev/.well-known/ard.json");
    expect(screen.getByLabelText("Copy URL of Agent 1")).toHaveTextContent("Copy catalog URL");
    await userEvent.click(screen.getByLabelText("Copy URL of System BOM"));
    expect(writeText).toHaveBeenCalledWith("https://agents.acme-corp.dev/ard/bom/system.json");
    expect(screen.getByRole("link", { name: "Download ard.json" }).getAttribute("href")).toContain("/api/discovery/manifest/download");
    expect(screen.getByRole("link", { name: /Download the whole site/ }).getAttribute("href")).toContain("/api/discovery/export.zip");
  });

  it("explains why nothing is published yet, and honestly describes signing", async () => {
    vi.stubGlobal("navigator", {});
    setup({ "GET /api/discovery/manifest": () => jsonResponse(catalog({ publishable: false, signed: true, reasons: ["publishing is switched off"], problems: ["signing is on but no key exists"],
      validation: { ok: false, manifest: { ok: false, error_count: 1, errors: [{ path: "/entries/0/url", message: "bad" }], warnings: [{ path: "/entries/0/representativeQueries", message: "few" }] }, boms: { a: ok, b: { ok: false, error_count: 3, errors: [] } } } })) });
    render(<DiscoveryPage />);
    await goTab("ARD catalog");
    expect(await screen.findByText("preview only")).toBeInTheDocument();
    expect(screen.getByText("publishing is switched off")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the settings" }).getAttribute("href")).toMatch(/\/settings\/?#discovery/);
    expect(screen.getByText(/signed with your key \(nothing is verified by Agent Studio\)/)).toBeInTheDocument();
    expect(screen.getByText("signing is on but no key exists")).toBeInTheDocument();
    expect(screen.getByLabelText("Catalog validation")).toHaveTextContent("/entries/0/url: bad");
    expect(screen.getByText("2 BOM files: 1 invalid")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Copy URL of Agent 1"));                       // no clipboard available: must not throw
  });

  it("without a publisher there is no catalog to show", async () => {
    setup({ "GET /api/discovery/manifest": () => jsonResponse({ manifest: null, entries: [], validation: null, reasons: ["set the publisher domain"], publishable: false }) });
    render(<DiscoveryPage />);
    await goTab("ARD catalog");
    expect(await screen.findByText("set the publisher domain")).toBeInTheDocument();
    expect(screen.queryByText("Entries")).not.toBeInTheDocument();
  });

  it("shows a single BOM file and a catalog with no validation block", async () => {
    setup({ "GET /api/discovery/manifest": () => jsonResponse(catalog({ validation: { ok: true, manifest: ok, boms: { only: ok } } })) });
    render(<DiscoveryPage />);
    await goTab("ARD catalog");
    expect(await screen.findByText("1 BOM file: all valid CycloneDX 1.7")).toBeInTheDocument();
    setup({ "GET /api/discovery/manifest": () => jsonResponse(catalog({ validation: null, problems: undefined })) });
    await userEvent.click(screen.getByRole("button", { name: "Rebuild" }));
    await waitFor(() => expect(screen.queryByLabelText("Catalog validation")).not.toBeInTheDocument());
  });

  it("shows a load error", async () => {
    setup({ "GET /api/discovery/manifest": () => jsonResponse({ detail: "boom" }, { status: 500 }) });
    render(<DiscoveryPage />);
    await goTab("ARD catalog");
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});

// ------------------------------------------------------------------------------------------------ push
const plan = { method: "PUT", url: "https://dt.acme.dev/api/v1/bom", project: { name: "Agent Studio", version: "0.3.0" }, redaction_level: "public", confirm: "c".repeat(64),
  bom: { specVersion: "1.7", bytes: 2048, sha256: "ab".repeat(32), components: 361, services: 32 }, note: "This is everything that will be sent." };

describe("Discovery page: push", () => {
  it("shows exactly what would be sent, and sends only on confirmation", async () => {
    const { calls } = setup({ "POST /api/aibom/push/preview": () => jsonResponse(plan), "POST /api/aibom/push": () => jsonResponse({ token: "tok-1", sent_to: plan.url }) });
    render(<DiscoveryPage />);
    await goTab("Push");
    await userEvent.click(screen.getByRole("button", { name: "Review what would be sent" }));
    const box = await screen.findByLabelText("Push preview");
    expect(box).toHaveTextContent("PUT");
    expect(box).toHaveTextContent("https://dt.acme.dev/api/v1/bom");
    expect(box).toHaveTextContent("361 components, 32 services");
    expect(box).toHaveTextContent("This is everything that will be sent.");
    expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/aibom/push")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Send to dt.acme.dev" }));
    expect(await screen.findByText(/Sent to https:\/\/dt.acme.dev\/api\/v1\/bom\. Processing token tok-1\./)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/aibom/push")!.body).toEqual({ confirm: plan.confirm });
    expect(screen.queryByLabelText("Push preview")).not.toBeInTheDocument();
  });

  it("reports a send without a processing token, and a refused send", async () => {
    let n = 0;
    setup({ "POST /api/aibom/push/preview": () => jsonResponse(plan),
      "POST /api/aibom/push": () => (n++ === 0 ? jsonResponse({ token: null, sent_to: plan.url }) : jsonResponse({ detail: "the BOM changed since you reviewed it" }, { status: 409 })) });
    render(<DiscoveryPage />);
    await goTab("Push");
    await userEvent.click(screen.getByRole("button", { name: "Review what would be sent" }));
    await userEvent.click(await screen.findByRole("button", { name: /Send to/ }));
    expect(await screen.findByText(`Sent to ${plan.url}.`)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review what would be sent" }));
    await userEvent.click(await screen.findByRole("button", { name: /Send to/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed since you reviewed");
  });

  it("explains a preview that is not possible", async () => {
    setup({ "POST /api/aibom/push/preview": () => jsonResponse({ detail: "set the Dependency-Track address in the settings first" }, { status: 422 }) });
    render(<DiscoveryPage />);
    await goTab("Push");
    await userEvent.click(screen.getByRole("button", { name: "Review what would be sent" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Dependency-Track address");
    expect(screen.queryByLabelText("Push preview")).not.toBeInTheDocument();
  });
});

describe("Discovery page: data and agents", () => {
  it("has a tab for data products and which agent does what", async () => {
    setup({ "GET /api/data/descriptors": () => jsonResponse({ agents: [{ id: "a", name: "Analyst", kind: "agent", purpose: "Answers.", framework: "adk", runtime: "local", sandbox: "default", data_sources: [], data_products: [] }], products: [], sources: [] }) });
    render(<DiscoveryPage />);
    await goTab("Data & agents");
    expect(await screen.findByTestId("data-products")).toBeInTheDocument();
    expect(screen.getByText("Analyst")).toBeInTheDocument();
  });
});
