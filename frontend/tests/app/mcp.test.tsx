import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import McpPage from "@/app/mcp/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/mcp/",
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;
const base = { description: "", env_set: {}, default_on: false, builtin: false, used_by: [] as { id: string; name: string }[], last_test: null as any, url: null as string | null, args: [] as string[], command: "npx" as string | null, requires_env: [] as string[] };
const servers = [
  { ...base, id: "fs", label: "Filesystem", description: "Read and write files", transport: "stdio", args: ["-y", "fs-server"], enabled: true, status: "ready", builtin: true, ready: true, default_on: true,
    category: "Files & code", used_by: [{ id: "engineer", name: "Engineer" }], last_test: { ok: true, tools: 2, names: ["read", "write"], ts: now() - 120 } },
  { ...base, id: "playwright", label: "Playwright", description: "Drive a browser", transport: "stdio", enabled: true, status: "ready", builtin: true, ready: true, category: "Web & docs" },
  { ...base, id: "brave", label: "Brave Search", description: "Web search", transport: "stdio", requires_env: ["BRAVE_API_KEY"], env_set: { BRAVE_API_KEY: false }, enabled: true, status: "", builtin: true, ready: false,
    category: "Web & docs" },
  { ...base, id: "remote", label: "Remote API", transport: "http", command: null, url: "https://mcp.test/x", enabled: false, status: "", ready: false,
    category: "Yours", last_test: { ok: false, error: "connection refused", ts: now() } },
  { ...base, id: "flaky", label: "Flaky", transport: "stdio", enabled: true, status: "", ready: true, category: "Yours", last_test: { ok: false, error: "crashed", ts: now() } },
  { ...base, id: "proposed", label: "Proposed", description: "From an agent", transport: "stdio", command: "uvx", args: ["x"], enabled: false, status: "pending_approval", ready: false,
    category: "Agent-proposed" },
];
const catalog = [
  { id: "xero", label: "Xero", domains: ["accounting"], publisher: "Xero", official: true, description: "Accounting data", kind: "npm", package: "x", read_only: "none", writes: "Can create invoices.", data_leaves: "Requests go to Xero.",
    license: "MIT", docs: "https://docs.test/xero", verified: "checked", transport: "stdio", command_line: "npx xero", fields: [{ name: "XERO_TOKEN", label: "Token", secret: true, required: true, where: "env", help: "", placeholder: "", default: "" }],
    setup: "Create a custom connection first.", can_be_read_only: true, installed: false, allow_writes: false, ready: false },
  { id: "time", label: "Time", domains: ["data"], publisher: "Anthropic", official: true, description: "Clock and zones", kind: "uvx", package: "t", read_only: "always", writes: "Nothing.", data_leaves: "Nothing leaves the PC.",
    license: "MIT", docs: "https://docs.test/time", verified: "checked", transport: "stdio", command_line: "uvx t", fields: [], setup: "", can_be_read_only: true, installed: false, allow_writes: false, ready: false },
];
const browser = { browser: "chromium", headless: true, isolated: true, viewport: "1280x720", allowed_origins: ["https://docs.test"], blocked_origins: [] };
const issue = { id: "i1", kind: "mcp", source: "fs", title: "Filesystem server crashed", detail: "stack trace", suggestion: "Restart it", ts: now(), debug_job: null };

type Over = Partial<Record<"list" | "issues" | "put" | "test" | "add" | "openapi" | "browserPut" | "debug" | "resolve" | "catalog" | "install" | "mode", () => Response>>;

function setup(over: Over = {}) {
  return routeFetch({
    "GET /api/mcp": over.list ?? (() => jsonResponse(servers)),
    "GET /api/catalog/mcp": over.catalog ?? (() => jsonResponse(catalog)),
    "GET /api/issues": over.issues ?? (() => jsonResponse([])),
    "GET /api/browser/settings": () => jsonResponse(browser),
    "PUT /api/browser/settings": over.browserPut ?? (() => jsonResponse(browser)),
    "PUT /api/mcp/brave": over.put ?? (() => jsonResponse({ ok: true })),
    "POST /api/mcp/fs/test": over.test ?? (() => jsonResponse({ ok: true, tools: [{ name: "read", description: "Read a file" }, { name: "list", description: "" }] })),
    "POST /api/mcp/remote/test": () => jsonResponse({ ok: false, error: "timeout" }),
    "DELETE /api/mcp/remote": () => jsonResponse({ ok: true }),
    "POST /api/mcp": over.add ?? (() => jsonResponse({ ok: true })),
    "POST /api/mcp/openapi": over.openapi ?? (() => jsonResponse({ label: "Billing", operations: 7 })),
    "POST /api/mcp/notes/test": () => jsonResponse({ ok: true, tools: [{ name: "a", description: "" }] }),
    "POST /api/mcp/billing/test": () => jsonResponse({ ok: false, error: "needs a key" }),
    "POST /api/catalog/mcp/xero/install": over.install ?? (() => jsonResponse({ ok: true })),
    "POST /api/mcp/xero/test": () => jsonResponse({ ok: true, tools: [{ name: "invoices", description: "" }] }),
    "PUT /api/catalog/mcp/xero/mode": over.mode ?? (() => jsonResponse({ ok: true })),
    "POST /api/issues/i1/debug": over.debug ?? (() => jsonResponse({ ok: true })),
    "POST /api/issues/i1/resolve": over.resolve ?? (() => jsonResponse({ ok: true })),
  });
}

/** Toasts are raised as window events; this page has no Toaster of its own, so tests listen for them. */
function toasts() {
  const seen: { text: string; tone: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
}

const list = () => screen.getByRole("listbox", { name: "MCP servers" });
const row = (name: string) => within(list()).getByRole("option", { name: new RegExp(name) });
const detail = () => within(screen.getByRole("region", { name: "Server details" }));
const modal = () => within(document.querySelector(".modal") as HTMLElement);
async function open(name: string, tab?: string) {
  await screen.findByRole("option", { name: new RegExp(name) });
  await userEvent.click(row(name));
  if (tab) await userEvent.click(detail().getByRole("tab", { name: new RegExp(tab) }));
}

describe("MCP servers: the list", () => {
  it("groups servers by what needs doing and shows each status", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    for (const g of ["Needs setup", "Installed", "Built-in tools", "Available from the catalog"]) expect(screen.getByRole("heading", { name: g })).toBeInTheDocument();
    expect(within(row("Filesystem")).getByText("ready")).toBeInTheDocument();
    expect(within(row("Brave Search")).getByText("needs credentials")).toBeInTheDocument();
    expect(within(row("Remote API")).getByText("off")).toBeInTheDocument();
    expect(within(row("Proposed")).getByText("needs approval")).toBeInTheDocument();
    expect(within(row("Flaky")).getByText("failed last test")).toHaveClass("bad");
    expect(within(row("Remote API")).getByText("data leaves PC")).toBeInTheDocument();
    const setupGroup = screen.getByRole("group", { name: "Needs setup" });
    expect(within(setupGroup).getByText("Brave Search")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Built-in tools" })).getByText("Filesystem")).toBeInTheDocument();
  });

  it("opens the first server by itself and follows the selection", async () => {
    setup();
    render(<McpPage />);
    expect(await detail().findByRole("heading", { name: "Brave Search" })).toBeInTheDocument();
    await userEvent.click(row("Filesystem"));
    expect(detail().getByRole("heading", { name: "Filesystem" })).toBeInTheDocument();
    expect(row("Filesystem")).toHaveAttribute("aria-selected", "true");
    expect(new URL(window.location.href).searchParams.get("server")).toBe("fs");
  });

  it("moves with the arrow keys, Home and End, and opens on Enter or space", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Brave/ });
    await waitFor(() => expect(row("Brave Search")).toHaveAttribute("aria-selected", "true"));
    row("Brave Search").focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(row("Proposed")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(row("Playwright")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");                                                  // already last: stays
    expect(row("Playwright")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Home}");
    expect(row("Brave Search")).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowUp}");
    expect(row("Brave Search")).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(row("Brave Search")).toHaveFocus());                           // focus follows the selection
    row("Remote API").focus();
    await userEvent.keyboard("{Enter}");
    expect(detail().getByRole("heading", { name: "Remote API" })).toBeInTheDocument();
    row("Flaky").focus();
    await userEvent.keyboard(" ");
    expect(detail().getByRole("heading", { name: "Flaky" })).toBeInTheDocument();
  });

  it("filters by state and by search text", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /^Ready/ }));
    expect(screen.queryByRole("option", { name: /Brave Search/ })).toBeNull();
    expect(screen.queryByRole("option", { name: /Xero/ })).toBeNull();                         // catalog servers are not "ready"
    expect(row("Filesystem")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Off/ }));
    expect(row("Remote API")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Proposed/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /^Needs approval/ }));
    expect(row("Proposed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^All/ }));
    await userEvent.type(screen.getByPlaceholderText("Search servers"), "zzz");
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });

  it("filters by access and by area using the catalog's information", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByText("More filters"));
    await userEvent.selectOptions(screen.getByLabelText("Access"), "read");
    expect(row("Time")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Xero/ })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Access"), "write");
    expect(row("Xero")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Time/ })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Access"), "any");
    await userEvent.selectOptions(screen.getByLabelText("Area"), "accounting");
    expect(row("Xero")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Filesystem/ })).toBeNull();
  });

  it("lists the catalog servers that are not added yet, with what they can do", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Available from the catalog/ }));
    expect(within(row("Xero")).getByText("can write")).toBeInTheDocument();
    expect(within(row("Xero")).getByText("data leaves PC")).toBeInTheDocument();
    expect(within(row("Time")).getByText("read-only")).toBeInTheDocument();
    expect(within(row("Time")).queryByText("data leaves PC")).toBeNull();
    await userEvent.click(row("Xero"));
    expect(detail().getByText("Create a custom connection first.")).toBeInTheDocument();
    expect(detail().queryByRole("tab", { name: "Settings" })).toBeNull();                      // nothing to configure until it is added
  });

  it("can collapse a group", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Built-in tools/ }));
    expect(screen.queryByRole("option", { name: /Filesystem/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Built-in tools/ }));
    expect(row("Filesystem")).toBeInTheDocument();
  });

  it("opens the server named in the address", async () => {
    nav.search = "server=remote";
    setup();
    render(<McpPage />);
    expect(await detail().findByRole("heading", { name: "Remote API" })).toBeInTheDocument();
    nav.search = "";
  });

  it("opens a catalog server named in the address once the catalog has loaded", async () => {
    nav.search = "server=xero";
    setup();
    render(<McpPage />);
    expect(await detail().findByRole("heading", { name: "Xero" })).toBeInTheDocument();
    nav.search = "";
  });

  it("falls back to the first server when the address names one that does not exist", async () => {
    nav.search = "server=ghost";
    setup({ catalog: () => textResponse("x", 500) });
    render(<McpPage />);
    expect(await detail().findByRole("heading", { name: "Brave Search" })).toBeInTheDocument();
    nav.search = "";
  });

  it("goes back to the list on a phone-style two-screen layout", async () => {
    setup();
    const { container } = render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    expect(container.querySelector(".mcp-split")).toHaveAttribute("data-view", "list");
    await userEvent.click(row("Filesystem"));
    expect(container.querySelector(".mcp-split")).toHaveAttribute("data-view", "detail");
    await userEvent.click(detail().getByRole("button", { name: /All servers/ }));
    expect(container.querySelector(".mcp-split")).toHaveAttribute("data-view", "list");
  });

  it("moves focus to the server heading on a phone", async () => {
    const mm = vi.fn().mockReturnValue({ matches: true });
    vi.stubGlobal("matchMedia", mm);
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(row("Filesystem"));
    await waitFor(() => expect(detail().getByRole("heading", { name: "Filesystem" })).toHaveFocus());
    vi.unstubAllGlobals();
  });

  it("shows loading placeholders, then reports when the backend is offline", async () => {
    setup({ list: () => textResponse("x", 500) });
    render(<McpPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toBeInTheDocument();
    expect(screen.getByText("No servers to show.")).toBeInTheDocument();
  });

  it("shows the empty prompt, and no detail, when there are no servers at all", async () => {
    setup({ list: () => jsonResponse([]), catalog: () => jsonResponse([]) });
    render(<McpPage />);
    expect(await screen.findByText("Nothing here.")).toBeInTheDocument();
    expect(await screen.findByText("Pick a server on the left to see its settings.")).toBeInTheDocument();
  });

  it("shows a skeleton while the list loads", async () => {
    let release: (r: Response) => void = () => {};
    routeFetch({
      "GET /api/mcp": () => new Promise<Response>((res) => { release = res; }) as any,
      "GET /api/catalog/mcp": () => jsonResponse([]), "GET /api/issues": () => jsonResponse([]),
    });
    render(<McpPage />);
    expect(screen.getByLabelText("Loading servers")).toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    release(jsonResponse(servers));
    expect(await screen.findByRole("option", { name: /Filesystem/ })).toBeInTheDocument();
  });
});

describe("MCP servers: one server", () => {
  it("explains what a server is, how it runs and who uses it", async () => {
    setup();
    render(<McpPage />);
    await open("Filesystem");
    const d = detail();
    expect(d.getByText("Read and write files")).toBeInTheDocument();
    expect(d.getByText("On this PC, as a local command")).toBeInTheDocument();
    expect(d.getByText("Engineer")).toBeInTheDocument();
    expect(d.getByText("default")).toBeInTheDocument();
    expect(d.getByText("built-in")).toBeInTheDocument();
    expect(d.getByText(/2 tools · tested 2 min ago/)).toBeInTheDocument();
    await open("Remote API");
    expect(detail().getByText("On a remote server (HTTP)")).toBeInTheDocument();
    expect(detail().getByText(/No agent yet/)).toBeInTheDocument();
    expect(detail().getByText("added by you")).toBeInTheDocument();
  });

  it("shows the catalog facts for an added catalog server", async () => {
    setup({ catalog: () => jsonResponse([{ ...catalog[0], installed: true, ready: true }, catalog[1]]), list: () => jsonResponse([...servers, { ...base, id: "xero", label: "Xero", transport: "stdio", enabled: true, ready: true, status: "", category: "Catalog" }]) });
    render(<McpPage />);
    await open("Xero");
    const d = detail();
    expect(d.getByText("Can create invoices.")).toBeInTheDocument();
    expect(d.getByText("Requests go to Xero.")).toBeInTheDocument();
    expect(d.getByText("Xero · official")).toBeInTheDocument();
    expect(d.getByRole("link", { name: "https://docs.test/xero" })).toHaveAttribute("href", "https://docs.test/xero");
  });

  it("labels a community catalog server as such", async () => {
    setup({ catalog: () => jsonResponse([{ ...catalog[0], official: false, installed: true, ready: true, domains: ["robotics"] }, catalog[1]]), list: () => jsonResponse([...servers, { ...base, id: "xero", label: "Xero", enabled: true, ready: true, status: "", category: "Catalog" }]) });
    render(<McpPage />);
    await open("Xero");
    expect(detail().getByText("Xero · community")).toBeInTheDocument();
    await userEvent.click(screen.getByText("More filters"));
    expect(screen.getByRole("option", { name: "robotics" })).toBeInTheDocument();           // an area without a nicer name keeps its id
  });

  it("goes back to read-only without asking and says so", async () => {
    const t = toasts();
    const installed = { ...catalog[0], read_only: "enforced", installed: true, ready: true, allow_writes: true };
    const { calls } = setup({ catalog: () => jsonResponse([installed, catalog[1]]), list: () => jsonResponse([...servers, { ...base, id: "xero", label: "Xero", enabled: true, ready: true, status: "", category: "Catalog" }]) });
    render(<McpPage />);
    await open("Xero", "Settings");
    await userEvent.click(detail().getByRole("button", { name: "Make read-only" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Back to read-only")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/catalog/mcp/xero/mode")!.body).toEqual({ allow_writes: false });
    t.stop();
  });

  it("shows no tools when the last test recorded none by name", async () => {
    setup({ list: () => jsonResponse([{ ...base, id: "nameless", label: "Nameless", ready: true, last_test: { ok: true, tools: 3, ts: now() } }]) });
    render(<McpPage />);
    await open("Nameless", "Tools");
    expect(detail().getByText("No tools listed yet.")).toBeInTheDocument();
  });

  it("switches a server off from its toggle", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await open("Brave Search");
    await userEvent.click(detail().getByRole("checkbox", { name: /on or off/ }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/mcp/brave")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/mcp/brave")!.body).toEqual({ enabled: false });
  });

  it("saves a credential and turns the server on", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await open("Brave Search", "Settings");
    const save = detail().getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.type(detail().getByPlaceholderText("paste value"), "sk-test");
    await userEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/mcp/brave")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/mcp/brave")!.body).toEqual({ env: { BRAVE_API_KEY: "sk-test" }, enabled: true });
  });

  it("reports a failed credential save", async () => {
    const t = toasts();
    setup({ put: () => jsonResponse({ detail: "vault locked" }, { status: 423 }) });
    render(<McpPage />);
    await open("Brave Search", "Settings");
    await userEvent.type(detail().getByPlaceholderText("paste value"), "sk");
    await userEvent.click(detail().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "vault locked")).toBe(true));
    t.stop();
  });

  it("says a server needs no settings when it has none", async () => {
    setup();
    render(<McpPage />);
    await open("Filesystem", "Settings");
    expect(detail().getByText(/needs no credentials or settings/)).toBeInTheDocument();
    expect(detail().getByText(/local · npx -y fs-server/)).toBeInTheDocument();           // the exact command sits under Advanced
  });

  it("runs a test on the Health tab and lists the tools it found", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await open("Filesystem", "Health & logs");
    await userEvent.click(detail().getByRole("button", { name: /Test/ }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/mcp/fs/test")).toBe(true));
    expect(await detail().findByText("Works: 2 tools.")).toBeInTheDocument();
    await userEvent.click(detail().getByRole("tab", { name: "Tools" }));
    expect(detail().getByText("list", { selector: "code" })).toBeInTheDocument();
    expect(detail().getByText("Read a file")).toBeInTheDocument();
  });

  it("lists the tools from the last test, and runs a test from the Tools tab", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await open("Filesystem", "Tools");
    expect(detail().getByText("write", { selector: "code" })).toBeInTheDocument();          // from the stored last test
    await userEvent.click(detail().getByRole("button", { name: "List tools" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/mcp/fs/test")).toBe(true));
    expect(await detail().findByText("Read a file")).toBeInTheDocument();
  });

  it("shows an empty Tools tab and a disabled test for a server that is not ready", async () => {
    setup();
    render(<McpPage />);
    await open("Remote API", "Tools");
    expect(detail().getByText("No tools listed yet.")).toBeInTheDocument();
    expect(detail().getByRole("button", { name: "List tools" })).toBeDisabled();
    await userEvent.click(detail().getByRole("tab", { name: "Health & logs" }));
    expect(detail().getByText("Last test: connection refused")).toBeInTheDocument();
    expect(detail().getByRole("button", { name: /Test/ })).toBeDisabled();
  });

  it("says a server has not been tested yet, and shows a passing last test", async () => {
    setup();
    render(<McpPage />);
    await open("Playwright", "Health & logs");
    expect(detail().getByText("Not tested yet.")).toBeInTheDocument();
    await open("Filesystem", "Health & logs");
    expect(detail().getByText(/Last test passed: 2 tools/)).toBeInTheDocument();
  });

  it("shows a failed test result in place", async () => {
    setup({ test: () => jsonResponse({ ok: false, error: "spawn failed" }) });
    render(<McpPage />);
    await open("Filesystem", "Health & logs");
    await userEvent.click(detail().getByRole("button", { name: /Test/ }));
    expect(await detail().findByText("spawn failed")).toBeInTheDocument();
  });

  it("shows a test that could not even be sent", async () => {
    setup({ test: () => textResponse("x", 500) });
    render(<McpPage />);
    await open("Filesystem", "Health & logs");
    await userEvent.click(detail().getByRole("button", { name: /Test/ }));
    expect(await detail().findByText("HTTP 500")).toBeInTheDocument();
  });

  it("deletes a server you added, after confirmation, from the Advanced section", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = setup();
    render(<McpPage />);
    await open("Remote API", "Settings");
    await userEvent.click(detail().getByTitle("Delete"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(window.confirm).toHaveBeenCalledWith("Delete MCP server “Remote API”?");
  });

  it("reports a deletion that fails", async () => {
    const t = toasts();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    routeFetch({ "GET /api/mcp": () => jsonResponse(servers), "GET /api/catalog/mcp": () => jsonResponse([]), "GET /api/issues": () => jsonResponse([]), "DELETE /api/mcp/remote": () => jsonResponse({ detail: "in use" }, { status: 409 }) });
    render(<McpPage />);
    await open("Remote API", "Settings");
    await userEvent.click(detail().getByTitle("Delete"));
    await waitFor(() => expect(t.seen.some((x) => x.text === "in use" && x.tone === "err")).toBe(true));
    t.stop();
  });

  it("keeps a server you added when deletion is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls } = setup();
    render(<McpPage />);
    await open("Remote API", "Settings");
    await userEvent.click(detail().getByTitle("Delete"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("does not offer to delete a built-in server", async () => {
    setup();
    render(<McpPage />);
    await open("Filesystem", "Settings");
    expect(detail().queryByTitle("Delete")).toBeNull();
  });

  it("explains an agent-proposed server before it is switched on", async () => {
    setup();
    render(<McpPage />);
    await open("Proposed");
    expect(detail().getByText(/An agent proposed this server/)).toBeInTheDocument();
    expect(detail().getByTitle("Approve & turn on")).toBeInTheDocument();
    expect(detail().getByText("agent-proposed")).toBeInTheDocument();
  });

  it("changes a catalog server to read-only and back, asking before it may change things", async () => {
    const t = toasts();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const installed = { ...catalog[0], read_only: "enforced", installed: true, ready: true };
    const { calls } = setup({ catalog: () => jsonResponse([installed, catalog[1]]), list: () => jsonResponse([...servers, { ...base, id: "xero", label: "Xero", enabled: true, ready: true, status: "", category: "Catalog" }]) });
    render(<McpPage />);
    await open("Xero", "Settings");
    await userEvent.click(detail().getByRole("button", { name: "Allow writes…" }));
    expect(confirm).toHaveBeenCalled();
    expect(calls.some((c) => c.method === "PUT" && c.url.pathname === "/api/catalog/mcp/xero/mode")).toBe(false);
    confirm.mockReturnValue(true);
    await userEvent.click(detail().getByRole("button", { name: "Allow writes…" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Writes allowed")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/catalog/mcp/xero/mode")!.body).toEqual({ allow_writes: true });
    t.stop();
  });

  it("returns to read-only without asking, and reports a failure", async () => {
    const t = toasts();
    const installed = { ...catalog[0], read_only: "enforced", installed: true, ready: true, allow_writes: true };
    setup({ catalog: () => jsonResponse([installed, catalog[1]]), mode: () => jsonResponse({ detail: "nope" }, { status: 400 }),
      list: () => jsonResponse([...servers, { ...base, id: "xero", label: "Xero", enabled: true, ready: true, status: "", category: "Catalog" }]) });
    render(<McpPage />);
    await open("Xero", "Settings");
    await userEvent.click(detail().getByRole("button", { name: "Make read-only" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "nope" && x.tone === "err")).toBe(true));
    t.stop();
  });

  it("re-saves a catalog server's settings from its Settings tab", async () => {
    const t = toasts();
    const installed = { ...catalog[0], installed: true, ready: true };
    const { calls } = setup({ catalog: () => jsonResponse([installed, catalog[1]]), list: () => jsonResponse([...servers, { ...base, id: "xero", label: "Xero", enabled: true, ready: true, status: "", category: "Catalog" }]) });
    render(<McpPage />);
    await open("Xero", "Settings");
    await userEvent.click(detail().getByText("Change this server's saved settings"));
    await userEvent.type(detail().getByLabelText(/Token/), "tok");
    await userEvent.click(detail().getByLabelText(/I understand this server can change things/));
    await userEvent.click(detail().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/catalog/mcp/xero/install")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/catalog/mcp/xero/install")!.body).toEqual({ values: { XERO_TOKEN: "tok" }, allow_writes: true });
    await waitFor(() => expect(t.seen.some((x) => x.text === "Xero added")).toBe(true));
    t.stop();
  });
});

describe("MCP servers: issues raised by the harness", () => {
  it("shows one line with the count, and the issue with its suggestion once opened", async () => {
    setup({ issues: () => jsonResponse([issue]) });
    render(<McpPage />);
    const bar = await screen.findByRole("button", { name: /Needs attention/ });
    expect(screen.queryByText("Restart it")).toBeNull();
    await userEvent.click(bar);
    expect(screen.getByText("Filesystem server crashed")).toBeInTheDocument();
    expect(screen.getByText("Restart it")).toBeInTheDocument();
    expect(screen.getByText("stack trace")).toBeInTheDocument();
    await userEvent.click(bar);
    expect(screen.queryByText("Restart it")).toBeNull();
  });

  it("marks the server's row and lists the issue on its Health tab", async () => {
    setup({ issues: () => jsonResponse([issue]) });
    render(<McpPage />);
    await screen.findByRole("button", { name: /Needs attention/ });
    expect(await within(row("Filesystem")).findByText("issue")).toBeInTheDocument();
    await open("Filesystem", "Health & logs");
    expect(detail().getByText("Filesystem server crashed")).toBeInTheDocument();
    expect(detail().getByText(/Use the .Needs attention. bar/)).toBeInTheDocument();
  });

  it("opens the server an issue is about", async () => {
    setup({ issues: () => jsonResponse([{ ...issue, id: "i1" }]) });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open server" }));
    expect(detail().getByRole("heading", { name: "Filesystem" })).toBeInTheDocument();
  });

  it("does nothing when the issue is about something that is not a server here", async () => {
    setup({ issues: () => jsonResponse([{ ...issue, source: "elsewhere" }]) });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    await userEvent.click(screen.getByRole("button", { name: "Open server" }));
    expect(detail().getByRole("heading", { name: "Brave Search" })).toBeInTheDocument();
  });

  it("starts a debugging agent on the issue", async () => {
    const t = toasts();
    const { calls } = setup({ issues: () => jsonResponse([issue]) });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Debug with an agent" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/issues/i1/debug")).toBe(true));
    await waitFor(() => expect(t.seen.some((x) => x.text.startsWith("Debugging started"))).toBe(true));
    t.stop();
  });

  it("reports a failure to start debugging", async () => {
    const t = toasts();
    setup({ issues: () => jsonResponse([issue]), debug: () => jsonResponse({ detail: "no agent available" }, { status: 503 }) });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Debug with an agent" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "no agent available" && x.tone === "err")).toBe(true));
    t.stop();
  });

  it("marks an issue resolved and drops it from the list", async () => {
    let resolved = false;
    const { calls } = setup({
      issues: () => jsonResponse(resolved ? [] : [issue]),
      resolve: () => { resolved = true; return jsonResponse({ ok: true }); },
    });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Mark resolved" }));
    await waitFor(() => expect(screen.queryByText("Filesystem server crashed")).toBeNull());
    expect(calls.some((c) => c.url.pathname === "/api/issues/i1/resolve")).toBe(true);
  });
});

describe("MCP servers: browser settings", () => {
  it("lives under the Playwright server's Settings tab and changes the browser and the window mode", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await open("Playwright", "Settings");
    await detail().findByText("Browser use");
    await userEvent.selectOptions(detail().getByLabelText("Browser"), "firefox");
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.browser === "firefox")).toBe(true));
    await userEvent.click(detail().getByRole("checkbox", { name: /Show the window/ }));
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.headless === false)).toBe(true));
  });

  it("saves the site allow and block lists, one per line", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await open("Playwright", "Settings");
    await detail().findByText("Browser use");
    await userEvent.type(detail().getByLabelText(/Never these sites/), "https://ads.test{enter}  https://spam.test");
    await userEvent.click(detail().getByRole("button", { name: "Save sites" }));
    await waitFor(() => expect(calls.some((c) => (c.body as any)?.blocked_origins?.length)).toBe(true));
    const body = calls.find((c) => (c.body as any)?.blocked_origins?.length)!.body as any;
    expect(body.allowed_origins).toEqual(["https://docs.test"]);
    expect(body.blocked_origins).toEqual(["https://ads.test", "https://spam.test"]);
  });

  it("leaves the browser panel out when its settings cannot be loaded", async () => {
    routeFetch({ "GET /api/mcp": () => jsonResponse(servers), "GET /api/catalog/mcp": () => jsonResponse([]), "GET /api/issues": () => jsonResponse([]), "GET /api/browser/settings": () => textResponse("x", 500) });
    render(<McpPage />);
    await open("Playwright", "Settings");
    await waitFor(() => expect(detail().getByText(/needs no credentials or settings|Advanced/)).toBeInTheDocument());
    expect(screen.queryByText("Browser use")).toBeNull();
  });

  it("reports a rejected browser setting", async () => {
    const t = toasts();
    setup({ browserPut: () => jsonResponse({ detail: "unknown browser" }, { status: 400 }) });
    render(<McpPage />);
    await open("Playwright", "Settings");
    await detail().findByText("Browser use");
    await userEvent.selectOptions(detail().getByLabelText("Browser"), "webkit");
    await waitFor(() => expect(t.seen.some((x) => x.text === "unknown browser")).toBe(true));
    t.stop();
  });
});

describe("MCP servers: adding", () => {
  it("offers three ways to add a server", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    expect(screen.getByText("Add an MCP server")).toBeInTheDocument();
    expect(screen.getByText(/Step 1 of 3/)).toBeInTheDocument();
    for (const n of ["From the catalog", "A server of my own", "From an OpenAPI file"]) expect(modal().getByRole("button", { name: new RegExp(n) })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText("Add an MCP server")).toBeNull();
  });

  it("adds a local command with its arguments and environment, then tests it", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /A server of my own/ }));
    await userEvent.type(screen.getByPlaceholderText("my-server"), "notes");
    await userEvent.type(screen.getByPlaceholderText("My server"), "Notes");
    await userEvent.type(screen.getByPlaceholderText(/@scope\/server/), "-y{enter}notes-server");
    await userEvent.click(screen.getByText("Advanced: environment"));
    await userEvent.type(screen.getByRole("textbox", { name: /Environment/ }), "TOKEN=abc=1{enter}bogus line");
    await userEvent.click(modal().getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/mcp")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/mcp")!.body).toMatchObject({
      id: "notes", label: "Notes", transport: "stdio", command: "npx", args: ["-y", "notes-server"], url: null, env: { TOKEN: "abc=1" }, enabled: true,
    });
    expect(await screen.findByText(/Step 3 of 3/)).toBeInTheDocument();
    await userEvent.click(modal().getByRole("button", { name: /Test it/ }));
    expect(await modal().findByText(/Works: 1 tools \(a\)/)).toBeInTheDocument();
    await userEvent.click(modal().getByRole("button", { name: "Open server" }));
    expect(screen.queryByText("Add an MCP server")).toBeNull();
  });

  it("adds a remote server by URL, left switched off if asked", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /A server of my own/ }));
    await userEvent.click(modal().getByRole("button", { name: "Remote URL" }));
    await userEvent.type(screen.getByPlaceholderText("my-server"), "remote2");
    await userEvent.type(screen.getByPlaceholderText("https://example.com/mcp"), "https://mcp.example/v1");
    await userEvent.click(modal().getByLabelText("Switch it on right away"));
    await userEvent.click(modal().getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/mcp")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.pathname === "/api/mcp")!.body).toMatchObject({ id: "remote2", transport: "http", command: null, url: "https://mcp.example/v1", enabled: false });
  });

  it("shows why a server could not be added and stays open", async () => {
    setup({ add: () => jsonResponse({ detail: "id already used" }, { status: 409 }) });
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /A server of my own/ }));
    await userEvent.type(screen.getByPlaceholderText("my-server"), "fs");
    await userEvent.click(modal().getByRole("button", { name: "Add server" }));
    expect(await screen.findByText("id already used")).toBeInTheDocument();
    expect(screen.getByText("Add an MCP server")).toBeInTheDocument();
  });

  it("goes back a step and closes without adding", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /A server of my own/ }));
    await userEvent.click(modal().getByRole("button", { name: "Back" }));
    expect(screen.getByText(/Step 1 of 3/)).toBeInTheDocument();
    await userEvent.click(modal().getByRole("button", { name: /From an OpenAPI file/ }));
    await userEvent.click(modal().getByRole("button", { name: "Back" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Add an MCP server")).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("imports an API from an OpenAPI document", async () => {
    const t = toasts();
    const { calls } = setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /From an OpenAPI file/ }));
    await userEvent.type(screen.getByPlaceholderText("billing-api"), "billing");
    await userEvent.type(screen.getByPlaceholderText("Billing API"), "Billing");
    await userEvent.type(screen.getByLabelText(/OpenAPI file/), "C:/specs/billing.yaml");
    await userEvent.click(screen.getByText("Advanced: server URL and sign-in"));
    await userEvent.type(screen.getByPlaceholderText("Bearer …"), "Bearer t");
    await userEvent.click(modal().getByLabelText(/Allow changes/));
    await userEvent.click(modal().getByRole("button", { name: "Add API" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/mcp/openapi")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/mcp/openapi")!.body).toMatchObject({
      id: "billing", label: "Billing", spec: "C:/specs/billing.yaml", base_url: null, allow_write: true, headers: { Authorization: "Bearer t" },
    });
    await waitFor(() => expect(t.seen.some((x) => x.text === "Added Billing: 7 operations")).toBe(true));
    await userEvent.click(await modal().findByRole("button", { name: /Test it/ }));
    expect(await modal().findByText("needs a key")).toBeInTheDocument();
    t.stop();
  });

  it("reports a failed OpenAPI import", async () => {
    setup({ openapi: () => jsonResponse({ detail: "spec is not valid YAML" }, { status: 422 }) });
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /From an OpenAPI file/ }));
    await userEvent.type(screen.getByPlaceholderText("billing-api"), "billing");
    await userEvent.type(screen.getByPlaceholderText("Billing API"), "Billing");
    await userEvent.type(screen.getByLabelText(/OpenAPI file/), "https://x.test/spec.json");
    await userEvent.click(modal().getByRole("button", { name: "Add API" }));
    expect(await screen.findByText("spec is not valid YAML")).toBeInTheDocument();
  });

  it("adds a server from the catalog: pick it, fill in its settings, confirm it, test it", async () => {
    const { calls } = setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /From the catalog/ }));
    await userEvent.type(modal().getByLabelText("Search the catalog"), "xe");
    expect(modal().queryByText("Clock and zones")).toBeNull();
    await userEvent.click(modal().getByRole("button", { name: "Choose" }));
    expect(modal().getByText("Create a custom connection first.")).toBeInTheDocument();
    const add = modal().getByRole("button", { name: "Add server" });
    expect(add).toBeDisabled();                                                              // writes need an explicit tick
    await userEvent.type(modal().getByLabelText(/Token/), "tok");
    await userEvent.click(modal().getByLabelText(/I understand this server can change things/));
    await userEvent.click(add);
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/catalog/mcp/xero/install")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/catalog/mcp/xero/install")!.body).toEqual({ values: { XERO_TOKEN: "tok" }, allow_writes: true });
    expect(await screen.findByText(/Step 3 of 3/)).toBeInTheDocument();
    await userEvent.click(modal().getByRole("button", { name: /Test it/ }));
    expect(await modal().findByText(/Works: 1 tools \(invoices\)/)).toBeInTheDocument();
  });

  it("filters the catalog picker by area, steps back from a server, and shows what is already added", async () => {
    setup({ catalog: () => jsonResponse([{ ...catalog[0], installed: true }, catalog[1]]) });
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /From the catalog/ }));
    expect(modal().getByRole("button", { name: "Added" })).toBeDisabled();
    await userEvent.selectOptions(modal().getByLabelText("Area"), "accounting");
    expect(modal().queryByText("Clock and zones")).toBeNull();
    await userEvent.type(modal().getByLabelText("Search the catalog"), "nothing matches this");
    expect(modal().getByText("No servers match.")).toBeInTheDocument();
    await userEvent.clear(modal().getByLabelText("Search the catalog"));
    await userEvent.selectOptions(modal().getByLabelText("Area"), "");
    await userEvent.click(modal().getAllByRole("button", { name: "Choose" })[0]);
    await userEvent.click(modal().getByRole("button", { name: "← Back" }));
    expect(modal().getByLabelText("Search the catalog")).toBeInTheDocument();
    await userEvent.click(modal().getByRole("button", { name: "← Back" }));
    expect(screen.getByText(/Step 1 of 3/)).toBeInTheDocument();
  });

  it("starts at the settings when you add a catalog server from its own page", async () => {
    setup();
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Available from the catalog/ }));
    await userEvent.click(row("Time"));
    await userEvent.click(detail().getByRole("button", { name: "Add this server" }));
    expect(modal().getByText("Time")).toBeInTheDocument();
    expect(modal().getByRole("button", { name: "Add server" })).toBeEnabled();                // a read-only server needs no confirm
    await userEvent.click(modal().getByRole("button", { name: "← Back" }));
    expect(screen.getByText(/Step 1 of 3/)).toBeInTheDocument();
  });

  it("opens the new server and refreshes the list when the flow finishes", async () => {
    let added = false;
    const { calls } = setup({ list: () => jsonResponse(added ? [...servers, { ...base, id: "notes", label: "Notes", enabled: true, ready: true, status: "", category: "Yours" }] : servers),
      add: () => { added = true; return jsonResponse({ ok: true }); } });
    render(<McpPage />);
    await screen.findByRole("option", { name: /Filesystem/ });
    await userEvent.click(screen.getByRole("button", { name: /Add server/ }));
    await userEvent.click(modal().getByRole("button", { name: /A server of my own/ }));
    await userEvent.type(screen.getByPlaceholderText("my-server"), "notes");
    await userEvent.type(screen.getByPlaceholderText("My server"), "Notes");
    await userEvent.click(modal().getByRole("button", { name: "Add server" }));
    await userEvent.click(await modal().findByRole("button", { name: "Open server" }));
    expect(await detail().findByRole("heading", { name: "Notes" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/mcp").length).toBeGreaterThan(1);
  });
});
