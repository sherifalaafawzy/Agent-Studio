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

const card = (over: Record<string, unknown> = {}) => ({ description: "Local models", env_set: {}, default_on: false, builtin: true, used_by: [] as any[], last_test: null as any, url: null as string | null,
  args: ["-m", "app.mcp_ts"], command: "{ts_python}", requires_env: [] as string[], transport: "stdio", enabled: false, status: "active", ready: false, category: "Local models",
  id: "timeseries", label: "Time-series forecasting (local models)", ...over });
const tsStatus = { env: { ready: false, home: "h", torch: null, python: null, free_gb: 50 }, hardware: { nvidia: false, gpu: null, cuda: null, driver: null, torch_build: "CPU build", variant: "cpu" },
  expect: "CPU expectation", install: { state: "idle", step: null, steps: [], percent: 0, progress_text: "", log_tail: [], error: null, queue: [] }, models: [] as any[] };

function setup(list: any[]) {
  return routeFetch({ "GET /api/mcp": () => jsonResponse(list), "GET /api/issues": () => jsonResponse([]), "GET /api/browser/settings": () => jsonResponse({ browser: "chromium", headless: true, isolated: true, viewport: "1280x720", allowed_origins: [], blocked_origins: [] }),
    "GET /api/ts/status": () => jsonResponse(tsStatus) });
}

const row = async () => await screen.findByRole("option", { name: /Time-series forecasting/ });

describe("MCP page: time-series forecasting", () => {
  it("shows the forecasting panel under the server's Settings tab and the server in the Local models row", async () => {
    setup([card()]);
    render(<McpPage />);
    expect(within(await row()).getByText("off")).toBeInTheDocument();
    expect(document.querySelector("[data-tour='ts-card']")).toBeNull();                       // not until the Settings tab is open
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    expect(await waitFor(() => { const e = document.querySelector("[data-tour='ts-card']"); expect(e).toBeTruthy(); return e as HTMLElement; })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Time-series forecasting (local models)" })).toBeInTheDocument();
  });

  it("says no model is installed once the server is switched on but nothing is installed", async () => {
    setup([card({ enabled: true })]);
    render(<McpPage />);
    expect(within(await row()).getByText("no model installed")).toBeInTheDocument();
  });

  it("is ready once a model is installed and the server is on", async () => {
    setup([card({ enabled: true, ready: true })]);
    render(<McpPage />);
    expect(within(await row()).getByText("ready")).toBeInTheDocument();
  });

  it("a pending-approval server keeps its own status", async () => {
    setup([card({ enabled: true, status: "pending_approval" })]);
    render(<McpPage />);
    expect(within(await row()).getByText("needs approval")).toBeInTheDocument();
  });
});
