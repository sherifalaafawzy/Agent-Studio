import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import McpPage from "@/app/mcp/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/mcp/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;
const base = {
  description: "", env_set: {} as Record<string, boolean>, default_on: false, builtin: false, used_by: [] as any[], last_test: null as any, url: null as string | null,
  args: [] as string[] | null, command: "npx" as string | null, requires_env: [] as string[], transport: "stdio", enabled: true, status: "", ready: true, category: "Yours",
};
const issue = { id: "i1", kind: "mcp", source: "fs", title: "Filesystem server crashed", detail: "", suggestion: "Restart it", ts: now(), debug_job: null as string | null };

function setup(list: any[], over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/mcp": () => jsonResponse(list),
    "GET /api/catalog/mcp": over.catalog ?? (() => jsonResponse([])),
    "GET /api/issues": over.issues ?? (() => jsonResponse([])),
    "PUT /api/mcp/keyed": () => jsonResponse({ ok: true }),
    "PUT /api/mcp/sw1": () => jsonResponse({ ok: true }),
    "PUT /api/mcp/sw2": () => jsonResponse({ ok: true }),
    "POST /api/issues/i1/resolve": over.resolve ?? (() => jsonResponse({ ok: true })),
  });
}

const detail = () => within(screen.getByRole("region", { name: "Server details" }));
const pick = async (label: string) => { await userEvent.click(await screen.findByRole("option", { name: new RegExp(label) })); };

describe("MCP servers: edge cases", () => {
  it("dates the last successful test in minutes or hours", async () => {
    setup([
      { ...base, id: "a", label: "Alpha", last_test: { ok: true, tools: 1, names: ["x"], ts: now() - 1000 } },
      { ...base, id: "b", label: "Beta", last_test: { ok: true, tools: 4, names: [], ts: now() - 7200 } },
    ]);
    render(<McpPage />);
    await pick("Alpha");
    expect(detail().getByText(/1 tools · tested 17 min ago/)).toBeInTheDocument();
    await pick("Beta");
    expect(detail().getByText(/4 tools · tested 2 h ago/)).toBeInTheDocument();
  });

  it("says a test that just finished was run just now", async () => {
    setup([{ ...base, id: "a", label: "Alpha", last_test: { ok: true, tools: 2, names: ["x"], ts: now() - 5 } }]);
    render(<McpPage />);
    await pick("Alpha");
    expect(detail().getByText(/2 tools · tested just now/)).toBeInTheDocument();
  });

  it("announces and sends the new state when a server is switched off or on", async () => {
    const seen: string[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail.text);
    window.addEventListener("as:toast", on);
    const { calls } = setup([
      { ...base, id: "sw1", label: "Switch one", enabled: true },
      { ...base, id: "sw2", label: "Switch two", enabled: false, ready: false },
    ]);
    render(<McpPage />);
    await pick("Switch one");
    await userEvent.click(detail().getByRole("checkbox", { name: "Switch one on or off" }));
    await pick("Switch two");
    await userEvent.click(detail().getByRole("checkbox", { name: "Switch two on or off" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    window.removeEventListener("as:toast", on);
    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.url.pathname, c.body])).toEqual([
      ["/api/mcp/sw1", { enabled: false }],
      ["/api/mcp/sw2", { enabled: true }],
    ]);
    expect(seen).toEqual(["Switch one off", "Switch two on"]);
  });

  it("describes how a local server without arguments runs", async () => {
    setup([{ ...base, id: "a", label: "Alpha", args: null }]);
    render(<McpPage />);
    await pick("Alpha");
    await userEvent.click(detail().getByRole("tab", { name: "Settings" }));
    expect(detail().getByText(/^local · npx$/)).toBeInTheDocument();
  });

  it("words each switch by what it will do", async () => {
    setup([
      { ...base, id: "on", label: "Enabled one", enabled: true },
      { ...base, id: "pend", label: "Pending", enabled: false, ready: false, status: "pending_approval", category: "Agent-proposed" },
      { ...base, id: "off", label: "Disabled one", enabled: false, ready: false },
    ]);
    render(<McpPage />);
    await pick("Enabled one");
    expect(detail().getByTitle("Turn off")).toBeInTheDocument();
    await pick("Pending");
    expect(detail().getByTitle("Approve & turn on")).toBeInTheDocument();
    await pick("Disabled one");
    expect(detail().getByTitle("Turn on")).toBeInTheDocument();
  });

  it("marks a stored credential, hides the marker while it is being replaced, and saves the new value", async () => {
    const seen: string[] = [];
    window.addEventListener("as:toast", (e) => seen.push((e as CustomEvent).detail.text), { once: true });
    const { calls } = setup([{ ...base, id: "keyed", label: "Keyed", requires_env: ["API_KEY"], env_set: { API_KEY: true } }]);
    render(<McpPage />);
    await pick("Keyed");
    await userEvent.click(detail().getByRole("tab", { name: "Settings" }));
    const c = detail();
    expect(c.getByText("stored")).toBeInTheDocument();
    const input = c.getByPlaceholderText("replace…");
    expect(c.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(input, "s3cret");
    expect(c.queryByText("stored")).toBeNull();
    await userEvent.click(c.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((x) => x.method === "PUT")).toBe(true));
    expect(calls.find((x) => x.method === "PUT")!.body).toEqual({ env: { API_KEY: "s3cret" }, enabled: true });
    expect(seen).toEqual(["Keyed: API_KEY saved"]);
    await waitFor(() => expect(c.queryByPlaceholderText("replace…")).toHaveValue(""));
  });

  it("disables the debug button while an agent is already debugging an issue", async () => {
    setup([{ ...base, id: "a", label: "Alpha" }], { issues: () => jsonResponse([{ ...issue, debug_job: "job-1" }]) });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    const btn = await screen.findByRole("button", { name: "Debugging…" });
    expect(btn).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Debug with an agent" })).toBeNull();
  });

  it("omits the issues bar when issues cannot be loaded", async () => {
    setup([{ ...base, id: "a", label: "Alpha" }], { issues: () => textResponse("x", 500) });
    render(<McpPage />);
    await screen.findByRole("option", { name: /Alpha/ });
    expect(screen.queryByText("Needs attention")).toBeNull();
  });

  it("still lists the servers when the catalog cannot be loaded, without catalog groups", async () => {
    setup([{ ...base, id: "a", label: "Alpha" }], { catalog: () => textResponse("x", 500) });
    render(<McpPage />);
    await screen.findByRole("option", { name: /Alpha/ });
    expect(screen.queryByRole("group", { name: "Available from the catalog" })).toBeNull();
  });

  it("still refreshes the lists when an issue cannot be marked resolved", async () => {
    const { calls } = setup([{ ...base, id: "a", label: "Alpha" }], { issues: () => jsonResponse([issue]), resolve: () => textResponse("nope", 500) });
    render(<McpPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Needs attention/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Mark resolved" }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/mcp" && c.method === "GET")).toHaveLength(2));
    expect(calls.filter((c) => c.url.pathname === "/api/issues")).toHaveLength(2);
    expect(screen.getByText("Filesystem server crashed")).toBeInTheDocument();
  });

  it("polls for new issues while the page is open", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const { calls } = setup([{ ...base, id: "a", label: "Alpha" }]);
    render(<McpPage />);
    await screen.findByRole("option", { name: /Alpha/ });
    const before = calls.filter((c) => c.url.pathname === "/api/issues").length;
    await vi.advanceTimersByTimeAsync(10100);
    expect(calls.filter((c) => c.url.pathname === "/api/issues").length).toBeGreaterThan(before);
    vi.useRealTimers();
  });
});
