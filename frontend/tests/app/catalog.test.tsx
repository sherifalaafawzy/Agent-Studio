import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import CatalogPage, { DOMAINS } from "@/app/catalog/page";
import { jsonResponse, routeFetch } from "../helpers/http";

const SKILLS = [
  { name: "bank-reconciliation", description: "Match bank lines.", mcp: [], domain: "accounting", group: "Accounting", source: "builtin", used_by: [] },
  { name: "pipeline-review", description: "Review a pipeline.", mcp: [], domain: "sales", group: "Sales", source: "builtin", used_by: [] },
];
const SERVER = { id: "tavily", label: "Tavily", domains: ["marketing"], publisher: "Tavily", official: true, description: "Web search.", kind: "npm", package: "tavily-mcp@1", read_only: "always", writes: "Nothing.", data_leaves: "Queries go to Tavily.", license: "MIT", docs: "https://x", verified: "npm", transport: "stdio", command_line: "npx", fields: [], setup: "", can_be_read_only: true, installed: false, allow_writes: false, ready: false };

function setup() {
  return routeFetch({
    "GET /api/catalog/skills": () => jsonResponse(SKILLS), "GET /api/agents": () => jsonResponse([]), "GET /api/catalog/mcp": () => jsonResponse([SERVER]),
    "GET /api/trainer/status": () => jsonResponse({ frameworks: [], install: { running: false, framework: null, log: [], error: null }, jobs_running: 0 }),
    "GET /api/trainer/jobs": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]),
    "GET /api/catalog/cloud": () => jsonResponse({ providers: [], allow_writes: false, enabled: false }),
  });
}
beforeEach(() => { window.location.hash = ""; });
afterEach(() => { window.location.hash = ""; });

describe("Catalog page", () => {
  it("opens on the skills, with every domain as a filter", async () => {
    setup();
    render(<CatalogPage />);
    expect(await screen.findByText("bank-reconciliation")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Skills" })).toHaveAttribute("aria-selected", "true");
    expect(DOMAINS).toHaveLength(8);
    const group = screen.getByRole("group", { name: "Domain" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["All", ...DOMAINS.map((d) => d.label)]);
  });

  it("filters by domain (click again to clear) and by search", async () => {
    setup();
    render(<CatalogPage />);
    await screen.findByText("bank-reconciliation");
    await userEvent.click(screen.getByRole("button", { name: "Sales" }));
    expect(screen.getByRole("button", { name: "Sales" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("bank-reconciliation")).toBeNull();
    expect(screen.getByText("pipeline-review")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sales" }));
    expect(screen.getByText("bank-reconciliation")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accounting" }));
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await userEvent.type(screen.getByLabelText("Search the catalog"), "pipeline");
    expect(screen.queryByText("bank-reconciliation")).toBeNull();
  });

  it("switches to MCP servers and to ML tools (which has no domain filter or search)", async () => {
    setup();
    render(<CatalogPage />);
    await userEvent.click(screen.getByRole("tab", { name: "MCP servers" }));
    expect(await screen.findByText("Tavily")).toBeInTheDocument();
    expect(window.location.hash).toBe("#mcp");
    await userEvent.click(screen.getByRole("tab", { name: "ML tools" }));
    expect(await screen.findByRole("heading", { name: "Model trainer" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cloud accounts" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Search the catalog")).toBeNull();
    expect(screen.queryByRole("group", { name: "Domain" })).toBeNull();
  });

  it("opens the tab named in the address", async () => {
    window.location.hash = "#ml";
    setup();
    render(<CatalogPage />);
    expect(await screen.findByRole("heading", { name: "Model trainer" })).toBeInTheDocument();
  });

  it("follows the address when only the # part changes (a link from elsewhere)", async () => {
    setup();
    render(<CatalogPage />);
    await screen.findByText("bank-reconciliation");
    act(() => { window.location.hash = "#mcp"; window.dispatchEvent(new HashChangeEvent("hashchange")); });
    expect(await screen.findByText("Tavily")).toBeInTheDocument();
  });

  it("ignores an unknown tab name in the address", async () => {
    window.location.hash = "#nonsense";
    setup();
    render(<CatalogPage />);
    expect(await screen.findByText("bank-reconciliation")).toBeInTheDocument();
  });
});
