import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/settings/page";
import { forgetModels } from "@/components/ModelSelect";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

/* ---------- fixtures (trimmed copies of the main settings test) ---------- */

const MODELS = [
  { id: "gpt-x", label: "GPT X", input_per_m: 2.5, output_per_m: 10 },
  { id: "gpt-mini", label: "GPT mini", input_per_m: 0, output_per_m: 0 },
];
const modelsFor = (pid: string) => ({ provider: pid, source: "live", error: null, models: MODELS, cached: false });

function prov(over: Record<string, any> = {}): any {
  return {
    id: "openai", kind: "openai", label: "OpenAI", enabled: true,
    has_api_key: true, api_key_hint: "…abcd", api_base: null,
    vertex_project: null, vertex_location: null, vertex_credentials_path: null,
    default_model: "gpt-x", pro_model: null, flash_model: null,
    cap_usd: 5, cap_period: "monthly", max_output_tokens: 4096,
    catalog: { label: "OpenAI", needs_key: true, note: "Hosted GPT models", default_model: "gpt-x", auth_modes: ["api_key"] },
    auth_mode: "api_key", auth_modes: [{ id: "api_key", label: "API key" }],
    api_key_env: null, key_env_default: "OPENAI_API_KEY",
    has_service_account_json: false, service_account_email: null,
    extra_header_names: [], organization: null,
    auth_ready: true, auth_detail: "key stored",
    ...over,
  };
}
const MOCK = prov({
  id: "mock", kind: "mock", label: "Mock", has_api_key: false, api_key_hint: null, default_model: "mock-1", cap_usd: 0,
  catalog: { label: "Mock", needs_key: false, note: "Offline stub", default_model: "mock-1", auth_modes: ["none"] },
  auth_mode: "none", auth_modes: [{ id: "none", label: "None" }], key_env_default: "", auth_detail: "",
});
const SETTINGS = {
  sandbox_network: false, workspace_dir: "H:/htdocs/demo", thinking_level: "medium", auto_debug_mcp: false,
  model_fallbacks: [], agent_spawning_enabled: false, max_spawns_per_turn: 3,
  max_turn_minutes: 30, max_llm_calls_per_turn: 40,
  agents_can_call_external: false, max_external_calls_per_turn: 5, external_timeout_min: 10,
  sidebot_enabled: false, sidebot_provider: "", sidebot_model: "", sidebot_resolved: { provider: "openai", model: "gpt-x" },
};
const SANDBOX = { docker: true, image: true, detail: "Docker ready · image agent-studio-sandbox" };
const OTEL = {
  enabled: true, inject_env: false, receiver_url: "http://127.0.0.1:8765", forward_url: "", forward_header_names: [],
  retention_days: 30, forwarded: 0, forward_errors: 0, last_forward_error: null,
};
const CLAUDE = {
  id: "claude_code", label: "Claude Code", command: "claude", args: ["--acp"], enabled: true, builtin: true, max_turns: 20,
  notes: "Anthropic's Claude Code CLI", installed: true, resolved_path: "C:/bin/claude.exe",
  env_keys: ["ANTHROPIC_API_KEY"], env_set: { ANTHROPIC_API_KEY: true },
  month_cost_cap: 0, month_turn_cap: 0, month_minutes_cap: 0,
  month: { turns: 3, minutes: 4.5, reported_cost: 0.0123, cost_reported_turns: 2 },
};
const KIRO = {
  id: "kiro", label: "Kiro", command: "kiro-cli", args: ["acp"], enabled: false, builtin: false, max_turns: 10,
  notes: "Kiro CLI", installed: false, resolved_path: null, env_keys: [], env_set: {},
  month_cost_cap: 0, month_turn_cap: 0, month_minutes_cap: 0,
  month: { turns: 0, minutes: 0, reported_cost: null, cost_reported_turns: 0 },
};

type Handler = (init: any, url: URL) => Response | Promise<Response>;

function setup(opts: {
  providers?: any[]; settings?: Record<string, any>; acp?: any[];
  otel?: Record<string, any>; summary?: Record<string, any>; routes?: Record<string, Handler>;
} = {}) {
  const providers = opts.providers ?? [prov(), MOCK];
  const settings: Record<string, any> = { ...SETTINGS, ...(opts.settings ?? {}) };
  const otel: Record<string, any> = { ...OTEL, ...(opts.otel ?? {}) };
  const acp = opts.acp ?? [];
  const summary = opts.summary ?? { total: 0, by_source: [] as any[] };

  const routes: Record<string, Handler> = {
    "GET /api/providers": () => jsonResponse(providers),
    "GET /api/settings": () => jsonResponse(settings),
    "PUT /api/settings": (init) => { Object.assign(settings, JSON.parse(init.body)); return jsonResponse(settings); },
    "GET /api/sandbox/status": () => jsonResponse(SANDBOX),
    "GET /api/sandboxes": () => jsonResponse({ policy: { kind: "docker-container", memory: "1g", cpus: "2", pids: 256, read_only_root: false, microvm_enabled: false }, network: false, default: "docker-container",
      kinds: [{ id: "docker-container", label: "Docker container", available: true, detail: "ready", experimental: false, notice: "" }] }),

    "GET /api/acp/agents": () => jsonResponse(acp),
    "GET /api/otel/settings": () => jsonResponse(otel),
    "PUT /api/otel/settings": (init) => { Object.assign(otel, JSON.parse(init.body)); return jsonResponse({ ...otel }); },
    "GET /api/otel/summary": () => jsonResponse(summary),
    "DELETE /api/otel/records": () => jsonResponse({ ok: true }),
  };
  for (const a of acp) {
    const base = `/api/acp/agents/${a.id}`;
    routes[`PUT ${base}`] = (init) => { Object.assign(a, JSON.parse(init.body)); return jsonResponse(a); };
    routes[`DELETE ${base}`] = () => jsonResponse({ ok: true });
    routes[`POST ${base}/probe`] = () => jsonResponse({ ok: true, title: "Claude Code", version: "1.2.3", protocol: 1 });
  }
  for (const p of providers) {
    const base = `/api/providers/${p.id}`;
    routes[`GET ${base}/models`] = () => jsonResponse(modelsFor(p.id));
    routes[`PUT ${base}`] = (init) => { Object.assign(p, JSON.parse(init.body)); return jsonResponse(p); };
  }
  Object.assign(routes, opts.routes ?? {});
  const net = routeFetch(routes);
  return { ...net, providers, settings, otel, acp };
}

const sent = (calls: Call[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url.pathname === path).map((c) => c.body);
const lastSent = (calls: Call[], method: string, path: string) => sent(calls, method, path).at(-1);
const toggle = (label: RegExp) => within(screen.getByText(label).closest(".row") as HTMLElement).getByRole("checkbox");
const setValue = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });
const tab = (name: RegExp) => screen.getByRole("button", { name });
const fail = (detail: string) => () => jsonResponse({ detail }, { status: 500 });

let toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail); };

beforeEach(() => {
  toasts = [];
  window.addEventListener("as:toast", onToast);
  ["openai", "anthropic", "mock", "ollama", "bedrock", "vertex"].forEach(forgetModels);
  window.location.hash = "";
});
afterEach(() => { window.removeEventListener("as:toast", onToast); });

/* ---------- provider list and provider card ---------- */

describe("Settings branches: provider list", () => {
  it("marks a provider with incomplete auth as bad, a disabled one as neutral, and shows the incomplete pill", async () => {
    setup({
      providers: [
        prov({ auth_ready: false, auth_detail: "no key" }),
        prov({ id: "anthropic", label: "Anthropic", kind: "anthropic", enabled: false, cap_usd: 0 }),
        MOCK,
      ],
    });
    render(<SettingsPage />);
    expect(await screen.findByText("auth incomplete")).toHaveAttribute("title", "no key");
    const items = Array.from(document.querySelectorAll(".list .li"));
    const dot = (label: string) => items.find((li) => li.textContent?.includes(label))!.querySelector(".dot")!;
    expect(dot("OpenAI")).toHaveClass("bad");
    expect(dot("Anthropic")).not.toHaveClass("ok");
    expect(dot("Anthropic")).not.toHaveClass("bad");
    expect(dot("Mock")).toHaveClass("ok");
  });

  it("falls back to the first provider when only the mock exists", async () => {
    setup({ providers: [MOCK] });
    render(<SettingsPage />);
    expect(await screen.findByText("Offline stub")).toBeInTheDocument();
    // the mock has no auth badge, API base, or headers
    expect(screen.queryByText("auth ready")).toBeNull();
    expect(screen.queryByLabelText(/^API base/)).toBeNull();
    expect(screen.queryByLabelText(/^Extra headers/)).toBeNull();
  });

  it("renders without a provider card when there are no providers at all", async () => {
    setup({ providers: [], acp: [] });
    render(<SettingsPage />);
    expect(await screen.findByText(/Each provider has its own hard spend cap/)).toBeInTheDocument();
    await waitFor(() => expect(tab(/^Model providers/)).toHaveTextContent("0/0"));
    expect(document.querySelector(".card")).toBeNull();
    // external tab: nothing selected, only the add entry
    await userEvent.click(tab(/External agents/));
    expect(screen.getByText("+ Add custom agent")).toBeInTheDocument();
    expect(document.querySelector(".card")).toBeNull();
  });

  it("asks for a key when none is stored", async () => {
    setup({ providers: [prov({ has_api_key: false, api_key_hint: null }), MOCK] });
    render(<SettingsPage />);
    const input = await screen.findByPlaceholderText("paste key");
    expect(input).toHaveValue("");
    expect(screen.queryByText(/^stored/)).toBeNull();
  });

  it("clears stored headers and reports a failure inline", async () => {
    setup({
      providers: [prov({ extra_header_names: ["X-Proxy"] }), MOCK],
      routes: { "PUT /api/providers/openai": fail("proxy refused") },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Clear headers" }));
    expect(await screen.findByText("⚠ proxy refused")).toBeInTheDocument();
  });
});

describe("Settings branches: cloud credentials that are not stored yet", () => {
  const bedrock = (over: Record<string, any> = {}) => prov({
    id: "bedrock", kind: "bedrock", label: "AWS Bedrock", has_api_key: false, api_key_hint: null,
    aws_region: "us-east-1", aws_profile: null, aws_access_key_hint: null, has_aws_secret: false, has_aws_session_token: false,
    catalog: { label: "Bedrock", needs_key: false, note: "Claude on AWS", default_model: "x", auth_modes: ["aws_keys"] },
    auth_mode: "aws_keys",
    auth_modes: [{ id: "api_key", label: "Bedrock API key" }, { id: "env", label: "Env var" }, { id: "aws_keys", label: "Access keys" }, { id: "aws_profile", label: "Profile" }],
    ...over,
  });

  it("shows empty-state placeholders for AWS keys", async () => {
    setup({ providers: [bedrock()] });
    render(<SettingsPage />);
    expect(await screen.findByPlaceholderText("AKIA…")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("paste secret")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("optional")).toBeInTheDocument();
    expect(screen.queryByText(/^stored/)).toBeNull();
  });

  it("marks a stored session token and shows the AWS key hint", async () => {
    setup({ providers: [bedrock({ has_aws_session_token: true, has_aws_secret: true, aws_access_key_hint: "AKIA…1" })] });
    render(<SettingsPage />);
    expect(await screen.findByText("stored AKIA…1")).toBeInTheDocument();
    expect(screen.getAllByText("stored")).toHaveLength(2);
    expect(screen.getAllByPlaceholderText("(unchanged)")).toHaveLength(3);
  });

  it("warns that a Bedrock API key cannot list models, in API-key and env modes", async () => {
    setup({ providers: [bedrock()] });
    render(<SettingsPage />);
    const auth = await screen.findByLabelText(/^Authentication/);
    expect(screen.queryByText(/A Bedrock API key works for running models/)).toBeNull();
    await userEvent.selectOptions(auth, "api_key");
    expect(screen.getByText(/A Bedrock API key works for running models/)).toBeInTheDocument();
    await userEvent.selectOptions(auth, "env");
    expect(screen.getByText(/A Bedrock API key works for running models/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Environment variable/)).toHaveAttribute("placeholder", "OPENAI_API_KEY");
    await userEvent.selectOptions(auth, "aws_profile");
    expect(screen.queryByText(/A Bedrock API key works for running models/)).toBeNull();
  });

  const vertex = (over: Record<string, any> = {}) => prov({
    id: "vertex", kind: "vertex_ai", label: "Vertex AI", has_api_key: false, api_key_hint: null,
    catalog: { label: "Vertex", needs_key: false, note: "Gemini on Google Cloud", default_model: "g", auth_modes: ["adc"] },
    auth_mode: "service_account_json",
    auth_modes: [{ id: "api_key", label: "Express key" }, { id: "adc", label: "Application default" }, { id: "service_account_json", label: "Service-account JSON" }],
    ...over,
  });

  it("asks for service-account JSON when none is stored", async () => {
    setup({ providers: [vertex()] });
    render(<SettingsPage />);
    const area = await screen.findByLabelText(/^Service-account JSON/);
    expect(area).toHaveAttribute("placeholder", expect.stringContaining('"type": "service_account"'));
    expect(screen.queryByText(/^stored/)).toBeNull();
    // project placeholder differs outside ADC mode
    expect(screen.getByLabelText(/^GCP project ID/)).toHaveAttribute("placeholder", "blank = project_id from the JSON");
  });

  it("shows a stored service account without an email", async () => {
    setup({ providers: [vertex({ has_service_account_json: true, service_account_email: null })] });
    render(<SettingsPage />);
    const pill = await screen.findByText(/^stored/);
    expect(pill).toHaveTextContent(/^stored\s*$/);
    expect(screen.getByLabelText(/^Service-account JSON/)).toHaveAttribute("placeholder", "(unchanged) — paste to replace");
  });

  it("shows a stored service account with its email, and the express-endpoint hint in API-key mode", async () => {
    setup({ providers: [vertex({ has_service_account_json: true, service_account_email: "sa@proj.iam" })] });
    render(<SettingsPage />);
    expect(await screen.findByText("stored sa@proj.iam")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/^Authentication/), "api_key");
    expect(screen.getByLabelText(/default: Vertex express endpoint/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^GCP project ID/)).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText(/^Authentication/), "adc");
    expect(screen.getByLabelText(/^GCP project ID/)).toHaveAttribute("placeholder", "my-project");
    expect(screen.queryByLabelText(/express endpoint/)).toBeNull();
  });
});

/* ---------- external agents ---------- */

describe("Settings branches: external agent rows", () => {
  const open = async (acp: any[]) => {
    const env = setup({ acp });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await screen.findAllByText("Command");
    return env;
  };

  it("treats missing limits as zero and saves them as numbers", async () => {
    const noCaps: any = { ...CLAUDE, env_set: { ANTHROPIC_API_KEY: false } };
    delete noCaps.month_cost_cap; delete noCaps.month_turn_cap; delete noCaps.month_minutes_cap;
    const { calls } = await open([noCaps]);
    expect(screen.getByLabelText(/^Monthly turn limit/)).toHaveValue(0);
    expect(screen.getByLabelText(/^Monthly time limit/)).toHaveValue(0);
    expect(screen.getByLabelText(/^Monthly cost limit/)).toHaveValue(0);
    // untouched -> Save disabled; no stored env -> "optional"
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByPlaceholderText("optional")).toBeInTheDocument();
    expect(screen.queryByText("stored")).toBeNull();
    setValue(screen.getByLabelText(/^Monthly cost limit/), "2.5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/acp/agents/claude_code")).toMatchObject({ month_cost_cap: 2.5, month_turn_cap: 0, month_minutes_cap: 0 }));
  });

  it("enables Save when only the time or turn limit changes", async () => {
    await open([{ ...CLAUDE }]);
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    setValue(screen.getByLabelText(/^Monthly time limit/), "60");
    expect(save).toBeEnabled();
    setValue(screen.getByLabelText(/^Monthly time limit/), "0");
    expect(save).toBeDisabled();
    setValue(screen.getByLabelText(/^Monthly turn limit/), "7");
    expect(save).toBeEnabled();
    setValue(screen.getByLabelText(/^Monthly turn limit/), "0");
    expect(save).toBeDisabled();
  });

  it("shows a zero cost when the agent reported turns without a cost figure", async () => {
    await open([{ ...CLAUDE, month: { turns: 2, minutes: 1, reported_cost: null, cost_reported_turns: 1 } }]);
    expect(document.body).toHaveTextContent("$0.0000 reported by the agent");
  });

  it("reports when the agent never reports cost", async () => {
    await open([{ ...CLAUDE, month: { turns: 2, minutes: 1, reported_cost: null, cost_reported_turns: 0 } }]);
    expect(document.body).toHaveTextContent("no cost reported (use turn/time limits for this agent)");
  });

  it("omits the monthly usage line when the backend sends none", async () => {
    await open([{ ...CLAUDE, month: undefined, resolved_path: null }]);
    expect(screen.queryByText(/^This month:/)).toBeNull();
    expect(screen.queryByText(/^→/)).toBeNull();
  });

  it("describes a minimal successful probe (name only, no modes or auth)", async () => {
    await open([{ ...CLAUDE }]);
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("connected")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("Claude Code 1.2.3 · ACP v1 · modes: none");
    expect(document.body).not.toHaveTextContent("auth:");
  });

  it("falls back to the probe's name and lists several auth methods", async () => {
    setup({
      acp: [{ ...CLAUDE }],
      routes: { "POST /api/acp/agents/claude_code/probe": () => jsonResponse({ ok: true, name: "claude-agent", version: "9", protocol: 2, modes: { available: [{ id: "plan" }] }, auth_methods: [{ id: "oauth" }, { id: "key" }] }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("connected")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("claude-agent 9 · ACP v2 · modes: plan · auth: oauth, key");
  });

  it("treats an empty auth list as none and an empty modes object as no modes", async () => {
    setup({
      acp: [{ ...CLAUDE }],
      routes: { "POST /api/acp/agents/claude_code/probe": () => jsonResponse({ ok: true, title: "T", version: "1", protocol: 1, modes: {}, auth_methods: [] }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("connected")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("T 1 · ACP v1 · modes: none");
    expect(document.body).not.toHaveTextContent("auth:");
  });

  it("shows the probe error for a failed connection", async () => {
    setup({
      acp: [{ ...CLAUDE }],
      routes: { "POST /api/acp/agents/claude_code/probe": () => jsonResponse({ ok: false, error: "spawn ENOENT" }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(screen.getByText("spawn ENOENT")).toBeInTheDocument();
  });

  it("reports a failed delete of a custom agent inline and keeps the agent", async () => {
    setup({ acp: [{ ...KIRO }], routes: { "DELETE /api/acp/agents/kiro": fail("in use") } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText("⚠ in use")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("reports a failed save of an agent's enabled switch", async () => {
    setup({ acp: [{ ...CLAUDE }], routes: { "PUT /api/acp/agents/claude_code": fail("read-only config") } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /enabled/ }));
    expect(await screen.findByText("⚠ read-only config")).toBeInTheDocument();
  });
});

/* ---------- telemetry ---------- */

describe("Settings branches: telemetry", () => {
  const openTelemetry = async () => {
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Telemetry/ }));
  };

  it("shows the load error instead of the card when the connector cannot be read", async () => {
    setup({ routes: { "GET /api/otel/settings": fail("otel disabled") } });
    await openTelemetry();
    expect(await screen.findByText("⚠ otel disabled")).toBeInTheDocument();
    expect(screen.queryByText("Save forwarding")).toBeNull();
  });

  it("lists names of stored forward headers", async () => {
    setup({ otel: { forward_header_names: ["Authorization", "X-Team"] } });
    await openTelemetry();
    expect(await screen.findByLabelText(/^Forward headers/)).toBeInTheDocument();
    expect(screen.getByLabelText(/stored: Authorization, X-Team/)).toBeInTheDocument();
  });

  it("counts zero records when the summary has no total", async () => {
    setup({ summary: { by_source: [] } });
    await openTelemetry();
    expect(await screen.findByText("0 records stored (local SQLite)")).toBeInTheDocument();
    expect(screen.getByText("Nothing stored yet.")).toBeInTheDocument();
  });

  it("reports a failed 'clear all' and does not reload", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = setup({ routes: { "DELETE /api/otel/records": fail("database is locked") } });
    await openTelemetry();
    await screen.findByText("Save forwarding");
    const before = calls.filter((c) => c.url.pathname === "/api/otel/settings" && c.method === "GET").length;
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(await screen.findByText("⚠ database is locked")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.pathname === "/api/otel/settings" && c.method === "GET")).toHaveLength(before);
  });

  it("does nothing when the clear-all confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls } = setup();
    await openTelemetry();
    await userEvent.click(await screen.findByRole("button", { name: "Clear all" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });
});

/* ---------- assistant ---------- */

describe("Settings branches: assistant tab", () => {
  const openAssistant = async (settings: Record<string, any>, providers?: any[]) => {
    const env = setup({ settings, providers });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Assistant/ }));
    await screen.findByText("The side agent.", { exact: false });
    return env;
  };

  it("treats null provider/model and a missing resolution as unset", async () => {
    await openAssistant({ sidebot_provider: null, sidebot_model: null, sidebot_resolved: undefined });
    expect(screen.getByLabelText(/^Provider/)).toHaveValue("");
    expect(screen.getByRole("option", { name: "Automatic" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(document.body).toHaveTextContent("no provider available — add one under Model providers");
  });

  it("treats missing provider/model keys as unset too", async () => {
    await openAssistant({ sidebot_provider: undefined, sidebot_model: undefined });
    expect(screen.getByLabelText(/^Provider/)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("says the default model is used when the resolved model is null", async () => {
    await openAssistant({ sidebot_resolved: { provider: "openai", model: null } });
    expect(document.body).toHaveTextContent("Right now it runs on: openai · default model.");
  });

  it("shows the stored provider as selected and offers only enabled non-mock providers", async () => {
    await openAssistant(
      { sidebot_provider: "anthropic", sidebot_model: "" },
      [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic" }), prov({ id: "ollama", label: "Ollama", kind: "ollama", enabled: false }), MOCK],
    );
    expect(screen.getByLabelText(/^Provider/)).toHaveValue("anthropic");
    const options = within(screen.getByLabelText(/^Provider/)).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Automatic (openai)", "OpenAI", "Anthropic"]);
  });

  it("switches the assistant off and says so", async () => {
    const { calls } = await openAssistant({ sidebot_enabled: true });
    await userEvent.click(toggle(/^Assistant on$/));
    expect(await screen.findByText("Assistant is off")).toBeInTheDocument();
    expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sidebot_enabled: false });
  });

  it("enables Save when only the model changes and clears the dirty state when reverted", async () => {
    await openAssistant({ sidebot_provider: "openai", sidebot_model: "gpt-x" });
    await screen.findByRole("option", { name: /GPT mini/ });
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "gpt-mini");
    expect(save).toBeEnabled();
    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "gpt-x");
    expect(save).toBeDisabled();
  });

  it("enables Save when only the provider changes back to automatic", async () => {
    await openAssistant({ sidebot_provider: "openai", sidebot_model: "" });
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText(/^Provider/), "");
    expect(save).toBeEnabled();
  });

  it("shows an error when the model list cannot be used because the provider is blank and none resolves", async () => {
    await openAssistant({ sidebot_resolved: { provider: null, model: null } });
    expect(screen.getByLabelText(/^Model/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "The provider's default model" })).toBeInTheDocument();
  });
});

/* ---------- harness tab ---------- */

describe("Settings branches: harness tab", () => {
  const openHarness = async () => {
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await screen.findByText("Sandbox");
  };

  it("toasts the reason when a toggle fails to save and leaves the switch unchanged", async () => {
    setup({ routes: { "PUT /api/settings": fail("settings locked") } });
    await openHarness();
    await userEvent.click(toggle(/^Allow network inside the sandbox$/));
    await waitFor(() => expect(toasts).toContainEqual({ text: "settings locked", tone: "err" }));
    expect(toggle(/^Allow network inside the sandbox$/)).not.toBeChecked();
  });

  it("toasts when the thinking level cannot be saved", async () => {
    setup({ routes: { "PUT /api/settings": fail("nope") } });
    await openHarness();
    await userEvent.selectOptions(screen.getByDisplayValue(/Medium/), "high");
    await waitFor(() => expect(toasts).toContainEqual({ text: "nope", tone: "err" }));
  });

  it("defaults the thinking level to medium when the setting is absent", async () => {
    setup({ settings: { thinking_level: undefined } });
    await openHarness();
    expect(screen.getByDisplayValue(/Medium/)).toBeInTheDocument();
  });

  it("refuses a blank 'max minutes per turn': nothing is sent and the field says what is wrong", async () => {
    const { calls } = setup();
    await openHarness();
    const field = screen.getByLabelText(/^Max minutes per turn/) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.blur(field);
    expect(await screen.findByRole("alert")).toHaveTextContent(/between 0 and 100000/);
    expect(sent(calls, "PUT", "/api/settings")).toHaveLength(0);
    // a real value is saved
    fireEvent.change(field, { target: { value: "45" } });
    fireEvent.blur(field);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ max_turn_minutes: 45 }));
  });

  it("starts with no fallbacks when the setting is absent, and adds the first one", async () => {
    const { calls } = setup({ settings: { model_fallbacks: undefined } });
    await openHarness();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "+ Add a fallback model" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ model_fallbacks: [{ provider: "openai", model: null }] }));
  });

  it("offers no 'add fallback' button when no provider is enabled", async () => {
    setup({ providers: [prov({ enabled: false }), { ...MOCK, enabled: false }] });
    await openHarness();
    expect(screen.queryByRole("button", { name: "+ Add a fallback model" })).toBeNull();
  });

  it("edits only the targeted row when several fallbacks exist", async () => {
    const two = [{ provider: "openai", model: "gpt-x" }, { provider: "anthropic", model: null }];
    const { calls } = setup({
      providers: [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic" }), MOCK],
      settings: { model_fallbacks: two },
    });
    await openHarness();
    const rows = (await screen.findAllByRole("button", { name: "Remove" })).map((b) => b.closest(".row") as HTMLElement);
    expect(rows).toHaveLength(2);
    // change the provider of row 2 only
    await userEvent.selectOptions(within(rows[1]).getAllByRole("combobox")[0], "openai");
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({
      model_fallbacks: [{ provider: "openai", model: "gpt-x" }, { provider: "openai", model: null }],
    }));
    // change the model of row 1 only
    await screen.findAllByRole("option", { name: /GPT mini/ });
    const freshRows = screen.getAllByRole("button", { name: "Remove" }).map((b) => b.closest(".row") as HTMLElement);
    await userEvent.selectOptions(within(freshRows[0]).getAllByRole("combobox")[1], "gpt-mini");
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({
      model_fallbacks: [{ provider: "openai", model: "gpt-mini" }, { provider: "openai", model: null }],
    }));
    // choosing the empty option stores null again
    await userEvent.selectOptions(within(freshRows[0]).getAllByRole("combobox")[1], "");
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({
      model_fallbacks: [{ provider: "openai", model: null }, { provider: "openai", model: null }],
    }));
    // remove row 2 only
    await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ model_fallbacks: [{ provider: "openai", model: null }] }));
  });

  it("keeps a fallback's provider selectable even when that provider is now disabled", async () => {
    setup({
      providers: [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic", enabled: false }), MOCK],
      settings: { model_fallbacks: [{ provider: "anthropic", model: null }] },
    });
    await openHarness();
    const row = (await screen.findByRole("button", { name: "Remove" })).closest(".row") as HTMLElement;
    const options = within(within(row).getAllByRole("combobox")[0]).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["OpenAI", "Anthropic", "Mock"]);
    expect(within(row).getAllByRole("combobox")[0]).toHaveValue("anthropic");
  });

  it("saves numeric limits and toasts on failure", async () => {
    setup({ routes: { "PUT /api/settings": fail("bad value") } });
    await openHarness();
    const field = screen.getByLabelText(/^Max model calls per turn/);
    fireEvent.change(field, { target: { value: "55" } });
    fireEvent.blur(field);
    await waitFor(() => expect(toasts).toContainEqual({ text: "bad value", tone: "err" }));
  });
});
