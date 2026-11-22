import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentAccess from "@/components/AgentAccess";
import FrameworksPanel from "@/components/FrameworksPanel";
import HarnessRules from "@/components/HarnessRules";
import AccessSettings from "@/components/AccessSettings";
import { jsonResponse, routeFetch } from "../helpers/http";

const COLS = [{ id: "tools", label: "Tools", means: "the tools" }, { id: "mcp", label: "MCP servers", means: "mcp" }];
const fw = (over: Record<string, unknown> = {}) => ({ id: "adk", label: "Google ADK", blurb: "The built-in engine.", strengths: ["Sub-agents"], licence: "Apache-2.0", native: true, caps: { tools: "yes", mcp: "yes" },
  packages: [], size_mb: 0, notes: "Runs inside.", billing: "Caps apply.", models: "any provider", state: "ready", version: null, error: null, ...over });
const MATRIX = (over: Record<string, unknown> = {}) => ({ columns: COLS, install: { running: false, framework: null, log: [], error: null }, frameworks: [
  fw(), fw({ id: "langgraph", label: "LangGraph", native: false, licence: "MIT", caps: { tools: "yes", mcp: "no" }, packages: ["langgraph==1.2.20"], size_mb: 250, state: "not_installed", strengths: ["Graphs"] }),
  fw({ id: "strands", label: "Strands Agents", native: false, caps: { tools: "partial", mcp: "no" }, state: "ready", version: "1.64.0" }),
  fw({ id: "pydantic_ai", label: "Pydantic AI", native: false, caps: { tools: "yes", mcp: "no" }, state: "failed", error: "pip failed" }),
  fw({ id: "openai_agents", label: "OpenAI Agents SDK", native: false, caps: { tools: "yes", mcp: "no" }, state: "installing" }),
], ...over });

const agent = (over: Record<string, unknown> = {}) => ({ framework: "adk", api_access: false, a2a: false, tools: ["read_file"], mcp: ["m"], skills: [], sub_agents: [], acp_agents: [], rag: [], can_spawn: false, ...over });

describe("AgentAccess (agent editor)", () => {
  it("shows frameworks, warns about what is lost and toggles API/A2A", async () => {
    const onChange = vi.fn();
    const net = routeFetch({ "GET /api/frameworks": () => jsonResponse(MATRIX()), "POST /api/frameworks/compat": () => jsonResponse({ framework: "langgraph", ready: false, warnings: ["LangGraph does not use the MCP servers"] }) });
    const { rerender } = render(<AgentAccess agent={agent({ framework: "langgraph", api_access: true })} onChange={onChange} />);
    expect(await screen.findByText(/does not use the MCP servers/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("not installed yet");
    expect(screen.getByText(/Callers need a key/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Agent framework"), "strands");
    expect(onChange).toHaveBeenCalledWith({ framework: "strands" });
    await userEvent.click(screen.getByLabelText(/API access/));
    expect(onChange).toHaveBeenCalledWith({ api_access: false });
    await userEvent.click(screen.getByLabelText(/A2A/));
    expect(onChange).toHaveBeenCalledWith({ a2a: true });
    expect(screen.getByRole("option", { name: "LangGraph (not installed)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Strands Agents (installed)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Google ADK (built in)" })).toBeInTheDocument();
    expect(net.calls.find((c) => c.url.pathname === "/api/frameworks/compat")!.body).toMatchObject({ framework: "langgraph", tools: ["read_file"], mcp: ["m"] });
    net.fn.mockClear();
    rerender(<AgentAccess agent={agent()} onChange={onChange} />);
    await waitFor(() => expect(net.fn).toHaveBeenCalled());
  });

  it("copes when the backend is not reachable", async () => {
    routeFetch({});
    render(<AgentAccess agent={agent()} onChange={() => {}} />);
    expect(await screen.findByLabelText("Agent framework")).toHaveValue("adk");
    expect(screen.queryByTestId("compat-warnings")).toBeNull();
    expect(screen.queryByText(/Callers need a key/)).toBeNull();
  });
});

describe("FrameworksPanel", () => {
  it("shows the matrix and installs after the licence is accepted", async () => {
    let state = "not_installed";
    const net = routeFetch({ "GET /api/frameworks": () => jsonResponse(MATRIX({ frameworks: MATRIX().frameworks.map((f) => (f.id === "langgraph" ? { ...f, state } : f)) })),
      "POST /api/frameworks/langgraph/install": () => { state = "installing"; return jsonResponse({}); }, "DELETE /api/frameworks/strands": () => jsonResponse({}) });
    render(<FrameworksPanel />);
    expect(await screen.findByLabelText("LangGraph MCP servers: no")).toBeInTheDocument();
    expect(screen.getByLabelText("Strands Agents Tools: partly")).toBeInTheDocument();
    expect(screen.getByText("installed 1.64.0")).toBeInTheDocument();
    expect(screen.getByText("install failed")).toBeInTheDocument();
    expect(screen.getByText("installing…")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Install" })[0]);
    const group = screen.getByRole("group", { name: "Install LangGraph" });
    const go = within(group).getByRole("button", { name: "Install LangGraph" });
    expect(go).toBeDisabled();
    await userEvent.click(within(group).getByRole("checkbox"));
    await userEvent.click(go);
    await waitFor(() => expect(net.calls.some((c) => c.method === "POST" && c.url.pathname === "/api/frameworks/langgraph/install" && c.body.accepted === true)).toBe(true));
    await userEvent.click(screen.getAllByRole("button", { name: "Install" })[0] ?? document.body);
  });

  it("opens a failed framework with its error, removes one, and reports errors", async () => {
    const net = routeFetch({ "GET /api/frameworks": () => jsonResponse(MATRIX()), "DELETE /api/frameworks/strands": () => jsonResponse({}) , "POST /api/frameworks/pydantic_ai/install": () => jsonResponse({ detail: "disk full" }, { status: 422 }) });
    render(<FrameworksPanel />);
    await screen.findByText("installed 1.64.0");
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "DELETE")).toBe(true));
    await userEvent.click(screen.getAllByRole("button", { name: "Install" })[1]);
    expect(screen.getByText(/The last install failed: pip failed/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Install Pydantic AI" }));
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/frameworks/pydantic_ai/install")).toBe(true));
    expect(screen.getByRole("group", { name: "Install Pydantic AI" })).toBeInTheDocument();      // still open: the install was refused
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("polls while installing and shows the log and a failure", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    const running = { running: true, framework: "langgraph", log: ["== Create the isolated environment"], error: null };
    const net = routeFetch({ "GET /api/frameworks": () => jsonResponse(MATRIX({ install: ++n < 3 ? running : { running: false, framework: null, log: [], error: "Step 'Install' failed" } })) });
    render(<FrameworksPanel />);
    expect(await screen.findByTestId("install-log")).toHaveTextContent("Create the isolated environment");
    await vi.advanceTimersByTimeAsync(4000);
    expect(await screen.findByText("Step 'Install' failed")).toBeInTheDocument();
    expect(net.fn.mock.calls.length).toBeGreaterThanOrEqual(3);
    vi.useRealTimers();
  });

  it("explains a load failure", async () => {
    routeFetch({ "GET /api/frameworks": () => jsonResponse({ detail: "nope" }, { status: 500 }) });
    render(<FrameworksPanel />);
    expect(await screen.findByText(/Frameworks could not be loaded/)).toBeInTheDocument();
  });
});

const POLICY = { permission_mode: "full", loop_guard_repeats: 3, project_instructions: true, checkpoints_enabled: true, hooks: [], max_tool_calls_per_turn: 60, max_framework_steps: 25, web_enabled: false, web_allow_hosts: [], web_search_url: "" };

describe("HarnessRules", () => {
  it("edits the rules, hooks and web tools and saves them", async () => {
    let saved: any = null;
    const net = routeFetch({ "GET /api/harness/policy": () => jsonResponse({ policy: POLICY, modes: ["full", "accept_edits", "plan"], web_search_key_set: true }),
      "PUT /api/harness/policy": (init) => { saved = JSON.parse(init!.body as string); return jsonResponse({ policy: { ...POLICY, ...saved, hooks: saved.hooks, web_allow_hosts: saved.web_allow_hosts }, modes: ["full", "accept_edits", "plan"], web_search_key_set: true }); },
      "PUT /api/harness/web-key": () => jsonResponse({ policy: POLICY, modes: ["full", "accept_edits", "plan"], web_search_key_set: false }) });
    render(<HarnessRules />);
    await screen.findByLabelText("Permission mode");
    await userEvent.selectOptions(screen.getByLabelText("Permission mode"), "plan");
    fireEvent.change(screen.getByLabelText("Loop guard repeats"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Max tool calls per turn"), { target: { value: "80" } });
    fireEvent.change(screen.getByLabelText("Max framework steps"), { target: { value: "30" } });
    await userEvent.click(screen.getByLabelText(/Read AGENTS.md/));
    await userEvent.click(screen.getByLabelText(/Keep checkpoints/));
    await userEvent.click(screen.getByRole("button", { name: "Add a hook" }));
    await userEvent.type(screen.getByLabelText("Hook 1 tool"), "x");
    await userEvent.type(screen.getByLabelText("Hook 1 pattern"), "rm");
    await userEvent.type(screen.getByLabelText("Hook 1 message"), "never");
    await userEvent.selectOptions(screen.getByLabelText("Hook 1 event"), "post_tool");
    await userEvent.selectOptions(screen.getByLabelText("Hook 1 event"), "pre_tool");
    await userEvent.click(screen.getByRole("button", { name: "Add a hook" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove hook 2" }));
    await userEvent.click(screen.getByLabelText(/Let agents use web_fetch/));
    await userEvent.type(screen.getByLabelText("Allowed sites"), "docs.python.org, example.com");
    await userEvent.type(screen.getByLabelText("Search address"), "https://s.example");
    await userEvent.type(screen.getByLabelText("Brave Search key"), "BSA");
    await userEvent.click(screen.getByRole("button", { name: "Save key" }));
    await userEvent.click(screen.getByRole("button", { name: "Save harness rules" }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toMatchObject({ permission_mode: "plan", loop_guard_repeats: 5, max_tool_calls_per_turn: 80, max_framework_steps: 30, project_instructions: false, checkpoints_enabled: false, web_enabled: true,
      web_allow_hosts: ["docs.python.org", "example.com"], web_search_url: "https://s.example", hooks: [{ event: "pre_tool", tool: "*x", match: "rm", action: "block", message: "never" }] });
    expect(net.calls.find((c) => c.url.pathname === "/api/harness/web-key")!.body).toEqual({ brave_api_key: "BSA" });
    expect(screen.getByLabelText("Brave Search key")).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "Save harness rules" }));
  });

  it("removes a stored key, shows server errors and load errors", async () => {
    const view = { policy: { ...POLICY, web_enabled: true }, modes: ["full", "plan"], web_search_key_set: true };
    const net = routeFetch({ "GET /api/harness/policy": () => jsonResponse(view), "PUT /api/harness/policy": () => jsonResponse({ detail: "permission_mode must be one of full" }, { status: 422 }),
      "PUT /api/harness/web-key": (init) => (JSON.parse(init!.body as string).brave_api_key === "" ? jsonResponse({ ...view, web_search_key_set: false }) : jsonResponse({ detail: "bad" }, { status: 422 })) });
    render(<HarnessRules />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove key" }));
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/harness/web-key" && c.body.brave_api_key === "")).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Save harness rules" }));
    expect(await screen.findByText("permission_mode must be one of full")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Brave Search key"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save key" }));
    expect(await screen.findByText("bad")).toBeInTheDocument();
  });

  it("explains when the rules cannot be loaded", async () => {
    routeFetch({ "GET /api/harness/policy": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<HarnessRules />);
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  });
});

const ACCESS = (over: Record<string, unknown> = {}) => ({ api_enabled: false, a2a_enabled: false, api_default_provider: "", base_url: "http://127.0.0.1:8765/v1", a2a_base: "http://127.0.0.1:8765/a2a",
  agents: [{ id: "helper", name: "Helper", api_access: true, a2a: false }, { id: "other", name: "Other", api_access: false, a2a: true }],
  keys: [{ id: "k1", name: "laptop", prefix: "asx_k1_abcde", agents: ["*"], rate_per_min: 60, created: 1, last_used: null, expires: 99999999, revoked: false, uses: 3 },
    { id: "k2", name: "ci", prefix: "asx_k2_fghij", agents: ["helper"], rate_per_min: 5, created: 1, last_used: 5, expires: null, revoked: true, uses: 0 }],
  remotes: [{ id: "peer", label: "Peer", url: "https://x/", enabled: true, has_token: true }, { id: "off", label: "Off", url: "https://y/", enabled: false, has_token: false }], ...over });

describe("AccessSettings", () => {
  it("switches, agents, keys and remotes", async () => {
    const net = routeFetch({ "GET /api/access/config": () => jsonResponse(ACCESS()), "GET /api/providers": () => jsonResponse([{ id: "mock", label: "Mock", enabled: true }, { id: "off", label: "Off", enabled: false }]),
      "PUT /api/access/config": () => jsonResponse({}), "PUT /api/agents/helper": () => jsonResponse({}), "PUT /api/agents/other": () => jsonResponse({}),
      "POST /api/access/keys": () => jsonResponse({ id: "k3", key: "asx_k3_secretsecretsecretsecret" }), "POST /api/access/keys/k1/rotate": () => jsonResponse({ id: "k1", key: "asx_k1_rotatedrotatedrotated" }),
      "PUT /api/access/keys/k1": () => jsonResponse({}), "PUT /api/access/keys/k2": () => jsonResponse({}), "DELETE /api/access/keys/k1": () => jsonResponse({ ok: true }),
      "POST /api/access/remotes": () => jsonResponse({}), "POST /api/access/remotes/peer/test": () => jsonResponse({ ok: true, name: "Peer agent" }), "POST /api/access/remotes/off/test": () => jsonResponse({ ok: false, error: "refused" }),
      "PUT /api/access/remotes/peer": () => jsonResponse({}), "PUT /api/access/remotes/off": () => jsonResponse({}), "DELETE /api/access/remotes/peer": () => jsonResponse({ ok: true }) });
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
    render(<AccessSettings />);
    await screen.findByText("laptop");
    expect(screen.getByTestId("snippet")).toHaveTextContent('"model": "helper"');
    await userEvent.click(screen.getByLabelText(/OpenAI-compatible API/));
    await userEvent.click(screen.getByRole("checkbox", { name: /^A2A \(/ }));
    await userEvent.selectOptions(screen.getByLabelText("Default provider"), "mock");
    expect(net.calls.filter((c) => c.method === "PUT" && c.url.pathname === "/api/access/config").map((c) => c.body)).toEqual([{ api_enabled: true }, { a2a_enabled: true }, { api_default_provider: "mock" }]);
    await userEvent.click(screen.getByLabelText("Helper API access"));
    await userEvent.click(screen.getByLabelText("Other A2A"));
    expect(net.calls.find((c) => c.url.pathname === "/api/agents/helper")!.body).toEqual({ api_access: false });
    expect(net.calls.find((c) => c.url.pathname === "/api/agents/other")!.body).toEqual({ a2a: false });
    // keys
    const rows = screen.getAllByTestId("key-row");
    expect(rows[0]).toHaveTextContent("all agents · 60/min · used 3× · last never");
    expect(rows[1]).toHaveTextContent("revoked");
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Rotate" }));
    expect(await screen.findByTestId("fresh-key")).toHaveTextContent("asx_k1_rotatedrotatedrotated");
    await userEvent.click(within(screen.getByTestId("fresh-key")).getByRole("button", { name: "Copy" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("asx_k1_rotatedrotatedrotated");
    await userEvent.click(within(screen.getByTestId("fresh-key")).getByRole("button", { name: "Done" }));
    expect(screen.queryByTestId("fresh-key")).toBeNull();
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Revoke" }));
    await userEvent.click(within(rows[1]).getByRole("button", { name: "Re-enable" }));
    expect(net.calls.filter((c) => c.method === "PUT" && /keys\/k[12]$/.test(c.url.pathname)).map((c) => c.body)).toEqual([{ revoked: true }, { revoked: false }]);
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "DELETE" && c.url.pathname === "/api/access/keys/k1")).toBe(true));
    const create = screen.getByRole("button", { name: "Create key" });
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Key name"), "ci runner");
    fireEvent.change(screen.getByLabelText("Requests per minute"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Expiry days"), { target: { value: "30" } });
    await userEvent.selectOptions(screen.getByLabelText("Key scope"), "pick");
    expect(create).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Helper"));
    await userEvent.click(screen.getByLabelText("Other"));
    await userEvent.click(screen.getByLabelText("Other"));
    await userEvent.click(create);
    expect(await screen.findByText(/asx_k3_secret/)).toBeInTheDocument();
    expect(net.calls.find((c) => c.method === "POST" && c.url.pathname === "/api/access/keys")!.body).toEqual({ name: "ci runner", agents: ["helper"], rate_per_min: 10, expires_days: 30 });
    await userEvent.selectOptions(screen.getByLabelText("Key scope"), "all");
    // remotes
    const remotes = screen.getAllByTestId("remote-row");
    await userEvent.click(within(remotes[0]).getByRole("button", { name: "Test" }));
    expect(await screen.findByText("reachable: Peer agent")).toBeInTheDocument();
    await userEvent.click(within(remotes[1]).getByRole("button", { name: "Test" }));
    expect(await screen.findByText("failed: refused")).toBeInTheDocument();
    await userEvent.click(within(remotes[0]).getByRole("button", { name: "Disable" }));
    await userEvent.click(within(remotes[1]).getByRole("button", { name: "Enable" }));
    await userEvent.click(within(remotes[0]).getByRole("button", { name: "Remove" }));
    await userEvent.type(screen.getByLabelText("Remote id"), "Travel_Agent");
    await userEvent.type(screen.getByLabelText("Remote name"), "Travel");
    await userEvent.type(screen.getByLabelText("Remote address"), "https://t.example/");
    await userEvent.type(screen.getByLabelText("Remote token"), "tok");
    await userEvent.click(screen.getByRole("button", { name: "Add remote agent" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "POST" && c.url.pathname === "/api/access/remotes")).toBe(true));
    expect(net.calls.find((c) => c.method === "POST" && c.url.pathname === "/api/access/remotes")!.body).toEqual({ id: "travel_agent", label: "Travel", url: "https://t.example/", token: "tok" });
  });

  it("starts empty and reports errors", async () => {
    routeFetch({ "GET /api/access/config": () => jsonResponse(ACCESS({ keys: [], remotes: [], agents: [{ id: "x", name: "X", api_access: false, a2a: false }] })), "GET /api/providers": () => jsonResponse({ detail: "x" }, { status: 500 }),
      "PUT /api/access/config": () => jsonResponse({ detail: "choose one of your providers" }, { status: 422 }) });
    render(<AccessSettings />);
    expect(await screen.findByText("No keys yet.")).toBeInTheDocument();
    expect(screen.getByTestId("snippet")).toHaveTextContent('"model": "your_agent"');
    expect(screen.getByText(/None yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/OpenAI-compatible API/));
    expect(await screen.findByText("choose one of your providers")).toBeInTheDocument();
  });

  it("explains when the settings cannot be loaded", async () => {
    routeFetch({ "GET /api/access/config": () => jsonResponse({ detail: "down" }, { status: 500 }), "GET /api/providers": () => jsonResponse([]) });
    render(<AccessSettings />);
    expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  });
});
