import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import McpPage from "@/app/mcp/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/mcp/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const base = { description: "", env_set: {}, default_on: false, builtin: false, used_by: [] as any[], last_test: null as any, url: null as string | null, args: [] as string[],
  command: "uvx", requires_env: [] as string[], transport: "stdio", enabled: true, status: "active", ready: true };
const LIST = [
  { ...base, id: "postgres", label: "PostgreSQL", category: "Catalog", description: "Query Postgres" },
  { ...base, id: "cloudml", label: "Cloud ML", category: "Cloud", builtin: true, enabled: false, ready: false },
  { ...base, id: "trainer", label: "Model trainer", category: "Local models", builtin: true, ready: false },
];

function setup(list = LIST) {
  return routeFetch({
    "GET /api/mcp": () => jsonResponse(list), "GET /api/issues": () => jsonResponse([]),
    "GET /api/browser/settings": () => jsonResponse({ browser: "chromium", headless: true, isolated: true, viewport: "1280x720", allowed_origins: [], blocked_origins: [] }),
    "GET /api/ts/status": () => jsonResponse({ env: { ready: false, home: "", torch: null, python: null, free_gb: null }, hardware: {}, expect: "", install: { state: "idle", step: null, steps: [], percent: 0, progress_text: "", log_tail: [], error: null, queue: [] }, models: [] }),
  });
}

describe("MCP servers page: catalog servers and the ML tools", () => {
  it("lists a server added from the Catalog under Installed", async () => {
    setup();
    render(<McpPage />);
    const installed = await screen.findByRole("group", { name: "Installed" });
    expect(within(installed).getByText("PostgreSQL")).toBeInTheDocument();
  });

  it("says the trainer needs a framework when it is on but nothing is installed", async () => {
    setup();
    render(<McpPage />);
    const row = await screen.findByRole("option", { name: /Model trainer/ });
    expect(within(row).getByText("no framework installed")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Needs setup" })).getByText("Model trainer")).toBeInTheDocument();
  });

  it("puts the trainer and the cloud accounts under their servers' Settings tabs", async () => {
    routeFetch({
      "GET /api/mcp": () => jsonResponse(LIST), "GET /api/catalog/mcp": () => jsonResponse([]), "GET /api/issues": () => jsonResponse([]),
      "GET /api/trainer/status": () => jsonResponse({ frameworks: [], install: { running: false, framework: null, log: [], error: null }, jobs_running: 0 }),
      "GET /api/trainer/jobs": () => jsonResponse([]),
      "GET /api/catalog/cloud": () => jsonResponse({ providers: [], allow_writes: false, enabled: false }),
    });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("option", { name: /Model trainer/ }));
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "Model trainer", level: 3, description: "" }).catch(() => screen.findByText("Model trainer", { selector: "#trainer-h" }))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("option", { name: /Cloud ML/ }));
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    expect(await screen.findByText("Cloud accounts", { selector: "#cloud-h" })).toBeInTheDocument();
  });
});
