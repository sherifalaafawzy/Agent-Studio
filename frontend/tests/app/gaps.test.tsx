import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import ExternalPage from "@/app/external/page";
import McpPage from "@/app/mcp/page";
import RunsPage from "@/app/runs/page";
import SkillsPage from "@/app/skills/page";
import Nav from "@/components/Nav";
import { startTour } from "@/components/TourRunner";
import { jsonResponse, routeFetch, sseResponse, textResponse } from "../helpers/http";

// Small gap-filling tests: the handlers and branches the page suites did not reach.

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));
vi.mock("@/components/TourRunner", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/components/TourRunner")>()), startTour: vi.fn() }));

const now = () => Date.now() / 1000;

describe("gaps: agents page", () => {
  it("edits the instruction and saves it", async () => {
    const agent = {
      id: "engineer", name: "Engineer", description: "", instruction: "Old text", provider: null, tier: "pro", model: null,
      tools: [], mcp: [], skills: [], sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask",
    };
    const { calls } = routeFetch({
      "GET /api/agents": () => jsonResponse([agent]),
      "GET /api/tools": () => jsonResponse([]),
      "GET /api/mcp": () => jsonResponse([]),
      "GET /api/skills": () => jsonResponse([]),
      "GET /api/acp/agents": () => jsonResponse([]),
      "PUT /api/agents/engineer": () => jsonResponse({ ok: true }),
    });
    render(<AgentsPage />);
    const box = await screen.findByDisplayValue("Old text");
    await userEvent.clear(box);
    await userEvent.type(box, "New instruction");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ instruction: "New instruction" });
  });
});

describe("gaps: external agents page", () => {
  it("replaces a permission card with its decision when the agent reports it", async () => {
    const frames = [
      { type: "permission", request_id: "r9", title: "Run npm test", kind: "execute", input: "npm test", options: [{ id: "allow", name: "Allow", kind: "allow_once" }] },
      { type: "resolved", request_id: "r9", decision: "allowed" },
    ].map((e) => `data: ${JSON.stringify(e)}\n\n`);
    routeFetch({
      "GET /api/acp/agents": () => jsonResponse([{ id: "claude_code", label: "Claude Code", installed: true, enabled: true, notes: "", command: "claude" }]),
      "POST /api/acp/chat": () => sseResponse(frames),
    });
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await userEvent.type(screen.getByPlaceholderText(/Message the agent/), "go");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(container).toHaveTextContent("↳ Run npm test: allowed"));
    expect(screen.queryByText("Permission requested")).toBeNull();
  });
});

describe("gaps: MCP servers page", () => {
  const servers = [
    { id: "fs", label: "Filesystem", description: "", transport: "stdio", command: "npx", args: [], url: null, requires_env: [], env_set: {}, enabled: true, default_on: false,
      status: "ready", builtin: true, ready: true, category: "Files & code", used_by: [], last_test: null },
    { id: "playwright", label: "Playwright", description: "", transport: "stdio", command: "npx", args: [], url: null, requires_env: [], env_set: {}, enabled: true, default_on: false,
      status: "ready", builtin: true, ready: true, category: "Web & docs", used_by: [], last_test: null },
  ];
  const browser = { browser: "chromium", headless: true, isolated: true, viewport: "1280x720", allowed_origins: [], blocked_origins: [] };

  it("shows a failed test as an error when the test request itself fails", async () => {
    routeFetch({
      "GET /api/mcp": () => jsonResponse(servers),
      "GET /api/issues": () => jsonResponse([]),
      "GET /api/browser/settings": () => jsonResponse(browser),
      "POST /api/mcp/fs/test": () => textResponse("boom", 500),
    });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("tab", { name: /Health/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Test/ }));
    expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  });

  it("adds a local server with a description and a custom command", async () => {
    const { calls } = routeFetch({
      "GET /api/mcp": () => jsonResponse(servers),
      "GET /api/issues": () => jsonResponse([]),
      "GET /api/browser/settings": () => jsonResponse(browser),
      "POST /api/mcp": () => jsonResponse({ ok: true }),
    });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Add server/ }));
    const dialog = document.querySelector(".modal") as HTMLElement;
    await userEvent.click(within(dialog).getByRole("button", { name: /A server of my own/ }));
    await userEvent.type(within(dialog).getByPlaceholderText("my-server"), "tools");
    await userEvent.type(within(dialog).getByPlaceholderText("My server"), "Tools");
    await userEvent.type(within(dialog).getByLabelText(/What it/), "Helper tools");
    const command = within(dialog).getByPlaceholderText("npx / uvx / node / python");
    await userEvent.clear(command);
    await userEvent.type(command, "uvx");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/mcp")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/mcp")!.body).toMatchObject({ command: "uvx", description: "Helper tools" });
  });

  it("sets the base URL for an OpenAPI import", async () => {
    const { calls } = routeFetch({
      "GET /api/mcp": () => jsonResponse(servers),
      "GET /api/issues": () => jsonResponse([]),
      "GET /api/browser/settings": () => jsonResponse(browser),
      "POST /api/mcp/openapi": () => jsonResponse({ label: "Billing", operations: 1 }),
    });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Add server/ }));
    const dialog = document.querySelector(".modal") as HTMLElement;
    await userEvent.click(within(dialog).getByRole("button", { name: /From an OpenAPI file/ }));
    await userEvent.type(within(dialog).getByPlaceholderText("billing-api"), "billing");
    await userEvent.type(within(dialog).getByPlaceholderText("Billing API"), "Billing");
    await userEvent.type(within(dialog).getByLabelText(/OpenAPI file/), "C:/x.yaml");
    await userEvent.type(within(dialog).getByPlaceholderText("https://api.example.com/v1"), "https://api.test/v1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add API" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/mcp/openapi")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/mcp/openapi")!.body).toMatchObject({ base_url: "https://api.test/v1" });
  });

  it("changes the viewport, the profile setting and the allowed sites", async () => {
    const { calls } = routeFetch({
      "GET /api/mcp": () => jsonResponse(servers),
      "GET /api/issues": () => jsonResponse([]),
      "GET /api/browser/settings": () => jsonResponse(browser),
      "PUT /api/browser/settings": () => jsonResponse(browser),
    });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("option", { name: /Playwright/ }));
    await userEvent.click(await screen.findByRole("tab", { name: "Settings" }));
    await screen.findByText("Browser use");
    await userEvent.selectOptions(screen.getByLabelText("Viewport"), "1920x1080");
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.viewport === "1920x1080")).toBe(true));
    await userEvent.click(screen.getByRole("checkbox", { name: /Clean profile each time/ }));
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.isolated === false)).toBe(true));
    await userEvent.type(screen.getByLabelText(/Only these sites/), "https://docs.test");
    await userEvent.click(screen.getByRole("button", { name: "Save sites" }));
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.allowed_origins?.includes("https://docs.test"))).toBe(true));
  });
});

describe("gaps: runs inspector", () => {
  it("orders the tool table by how often each tool was used", async () => {
    nav.search = "s=sess-1";
    const result = (tool: string) => ({ agent: "engineer", type: "result" as const, tool, ts: now() - 5, ms: 10, ok: true, result: "" });
    const root = {
      run_id: "root", parent: null, agent: "engineer", spawned: false, task: "t", status: "done", started: now() - 60, ended: now() - 50,
      steps: [result("read_file"), result("grep"), result("grep")], texts: [], calls: [], result: "done", cost_usd: 0,
    };
    routeFetch({
      "GET /api/chat/sessions/sess-1": () => jsonResponse({ session: { id: "sess-1", title: "Many tools", provider: "p", agent: "engineer", model: null, workspace: null, created: now() - 100, updated: now(), messages: 1 }, messages: [{ role: "bot", text: "ok", meta: { runs: [root] } }] }),
    });
    const { container } = render(<RunsPage />);
    await screen.findByRole("heading", { name: "Many tools" });
    await userEvent.click(screen.getByRole("button", { name: "Tools" }));
    const rows = container.querySelectorAll(".insp-side tbody tr");
    expect(rows[0]).toHaveTextContent("grep");
    expect(rows[1]).toHaveTextContent("read_file");
    nav.search = "";
  });
});

describe("gaps: skills page", () => {
  it("writes the instructions body of a new skill", async () => {
    const { calls } = routeFetch({
      "GET /api/skills": () => jsonResponse([]),
      "GET /api/mcp": () => jsonResponse([]),
      "POST /api/skills": () => jsonResponse({ name: "newone", description: "d", mcp: [], source: "user", body: "x" }),
    });
    render(<SkillsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /New skill/ }));
    await userEvent.type(screen.getByPlaceholderText("release-checklist"), "newone");
    await userEvent.type(screen.getByPlaceholderText("the user asks to cut a release or bump versions"), "d");
    const body = document.querySelector("textarea") as HTMLTextAreaElement;
    await userEvent.clear(body);
    await userEvent.type(body, "Step one");
    await userEvent.click(screen.getByRole("button", { name: "Create skill" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ body: "Step one" });
  });
});

describe("gaps: navigation", () => {
  it("starts the welcome tour from the sidebar", async () => {
    nav.path = "/";
    nav.search = "";
    routeFetch({ "GET /api/chat/sessions": () => jsonResponse([]) });
    render(<Nav />);
    await userEvent.click(screen.getByRole("button", { name: "Take the guided tour" }));
    expect(startTour).toHaveBeenCalledWith("welcome");
  });
});
