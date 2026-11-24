import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/settings/page";
import { forgetModels } from "@/components/ModelSelect";
import { jsonResponse, routeFetch, sseResponse, type Call } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

/* ---------- fixtures ---------- */

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
const VOICE = {
  configured: false, key_hint: null, key_source: null, voice_id: "", tts_model: "eleven_multilingual_v2",
  char_cap: 0, chars_used_month: 0, auto_read: false, dictation: false,
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
const CREDITS = {
  mode: "manual", balance_usd: 20, as_of: null, source: "entered by you", strict: false, remaining_usd: 12.5,
  checked_at: null, error: null, auto_available: false, why_not: "This provider has no balance API.", spent_since_usd: 7.5,
};

type Handler = (init: any, url: URL) => Response | Promise<Response>;

/** Mounts-ready fake backend. Mutable fixtures are per test; PUTs merge into them like the real server. */
function setup(opts: {
  providers?: any[]; settings?: Record<string, any>; sandbox?: any; acp?: any[];
  otel?: Record<string, any>; voice?: Record<string, any>; routes?: Record<string, Handler>;
} = {}) {
  const providers = opts.providers ?? [prov(), MOCK];
  const settings: Record<string, any> = { ...SETTINGS, ...(opts.settings ?? {}) };
  const otel: Record<string, any> = { ...OTEL, ...(opts.otel ?? {}) };
  const voice: Record<string, any> = { ...VOICE, ...(opts.voice ?? {}) };
  const sandbox = opts.sandbox ?? SANDBOX;
  const acp = opts.acp ?? [];
  const summary = { total: 0, by_source: [] as any[] };

  const routes: Record<string, Handler> = {
    "GET /api/providers": () => jsonResponse(providers),
    "GET /api/settings": () => jsonResponse(settings),
    "PUT /api/settings": (init) => { Object.assign(settings, JSON.parse(init.body)); return jsonResponse(settings); },
    "GET /api/sandbox/status": () => jsonResponse(sandbox),
    "GET /api/sandboxes": () => jsonResponse({ policy: { kind: "docker-container", memory: "1g", cpus: "2", pids: 256, read_only_root: false, microvm_enabled: false }, network: false, default: "docker-container",
      kinds: [{ id: "docker-container", label: "Docker container", available: true, detail: "ready", experimental: false, notice: "" }] }),

    "GET /api/acp/agents": () => jsonResponse(acp),
    "POST /api/fs/pick-folder": () => jsonResponse({ path: null, cancelled: true }),
    "GET /api/fs/dirs": (_init, url) => {
      const p = url.searchParams.get("path") ?? "H:/htdocs/demo";
      const inside = p === "H:/htdocs/agent-studio";
      return jsonResponse({ path: p, parent: "H:/htdocs", dirs: inside ? [] : [{ name: "agent-studio", path: "H:/htdocs/agent-studio" }], home: "C:/Users/me" });
    },
    "GET /api/otel/settings": () => jsonResponse(otel),
    "PUT /api/otel/settings": (init) => { Object.assign(otel, JSON.parse(init.body)); return jsonResponse({ ...otel }); },
    "GET /api/otel/summary": () => jsonResponse(summary),
    "DELETE /api/otel/records": () => jsonResponse({ ok: true }),
    "GET /api/voice/settings": () => jsonResponse(voice),
    "PUT /api/voice/settings": (init) => {
      const patch = JSON.parse(init.body);
      if (patch.api_key) { voice.configured = true; voice.key_hint = "…" + String(patch.api_key).slice(-4); delete patch.api_key; }
      Object.assign(voice, patch);
      return jsonResponse({ ...voice });
    },
    "GET /api/voice/voices": () => jsonResponse([]),
    "GET /api/voice/models": () => jsonResponse([]),
    "GET /api/voice/subscription": () => jsonResponse(null),
  };
  for (const a of acp) {
    const base = `/api/acp/agents/${a.id}`;
    routes[`PUT ${base}`] = (init) => { Object.assign(a, JSON.parse(init.body)); return jsonResponse(a); };
    routes[`DELETE ${base}`] = () => jsonResponse({ ok: true });
    routes[`POST ${base}/probe`] = () => jsonResponse({
      ok: true, title: "Claude Code", version: "1.2.3", protocol: 1,
      modes: { available: [{ id: "default" }, { id: "plan" }] }, auth_methods: [{ id: "oauth" }],
    });
  }
  routes["POST /api/acp/agents"] = () => jsonResponse({ ok: true });
  for (const p of providers) {
    const base = `/api/providers/${p.id}`;
    routes[`GET ${base}/models`] = () => jsonResponse(modelsFor(p.id));
    routes[`GET ${base}/models?refresh=true`] = () => jsonResponse(modelsFor(p.id));
    routes[`GET ${base}/ollama/suggestions`] = () => jsonResponse([]);
    routes[`POST ${base}/pull`] = () => sseResponse([`data: ${JSON.stringify({ type: "done", model: "m" })}\n\n`]);
    routes[`PUT ${base}/credits`] = () => jsonResponse({ ok: true });
    routes[`POST ${base}/credits/refresh`] = () => jsonResponse({ ok: true });
    routes[`PUT ${base}`] = (init) => { Object.assign(p, JSON.parse(init.body)); return jsonResponse(p); };
  }
  Object.assign(routes, opts.routes ?? {});
  const net = routeFetch(routes);
  return { ...net, providers, settings, otel, voice, acp };
}

const sent = (calls: Call[], method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url.pathname === path).map((c) => c.body);
const lastSent = (calls: Call[], method: string, path: string) => sent(calls, method, path).at(-1);

/** The node for a Toggle row (label text + switch). */
const toggle = (label: RegExp) => within(screen.getByText(label).closest(".row") as HTMLElement).getByRole("checkbox");

/** Number/textarea fields: fireEvent is deterministic for type=number (user-event mangles "7.5"). */
const setValue = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

let toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail); };

beforeEach(() => {
  toasts = [];
  window.addEventListener("as:toast", onToast);
  // ModelSelect/Ollama keep per-provider model caches at module level; start every test cold.
  ["openai", "anthropic", "mock", "ollama", "bedrock", "vertex"].forEach(forgetModels);
  window.location.hash = "";
});
afterEach(() => {
  window.removeEventListener("as:toast", onToast);
});

const providerCard = () => document.querySelector(".card") as HTMLElement;
const tab = (name: RegExp) => screen.getByRole("button", { name });

/* ---------- loading, tabs, and errors ---------- */

describe("Settings page: loading and tabs", () => {
  it("lists providers with their spend caps and opens the first enabled one", async () => {
    setup({ providers: [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic", enabled: false, cap_usd: 0 }), MOCK] });
    render(<SettingsPage />);
    expect(await screen.findByText("Hosted GPT models")).toBeInTheDocument();
    const list = document.querySelector(".list") as HTMLElement;
    expect(within(list).getByText("$5.00")).toBeInTheDocument();
    expect(within(list).getByText("off")).toBeInTheDocument();
    expect(providerCard().querySelector("b")).toHaveTextContent("OpenAI");
    expect(tab(/Model providers/)).toHaveTextContent("2/3");
  });

  it("switches the card to another provider when it is clicked in the list", async () => {
    setup({ providers: [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic", catalog: { label: "Anthropic", needs_key: true, note: "Claude API", default_model: "x", auth_modes: ["api_key"] } })] });
    render(<SettingsPage />);
    await screen.findByText("Hosted GPT models");
    await userEvent.click(screen.getByText("Anthropic", { selector: ".li .t" }));
    expect(await screen.findByText("Claude API")).toBeInTheDocument();
    expect(providerCard().querySelector("b")).toHaveTextContent("Anthropic");
  });

  it("reports an unreachable backend instead of an empty page", async () => {
    setup({ routes: { "GET /api/providers": () => jsonResponse({ detail: "boom" }, { status: 500 }) } });
    render(<SettingsPage />);
    expect(await screen.findByText("Backend offline: boom")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("opens the tab named in the URL hash", async () => {
    setup();
    window.location.hash = "#telemetry";
    render(<SettingsPage />);
    expect(await screen.findByText("Clear all")).toBeInTheDocument();
    expect(screen.queryByText("Hosted GPT models")).toBeNull();
  });

  it("ignores an unknown hash and stays on the providers tab", async () => {
    setup();
    window.location.hash = "#nonsense";
    render(<SettingsPage />);
    expect(await screen.findByText("Hosted GPT models")).toBeInTheDocument();
  });

  it("switches tabs and writes the choice into the URL hash", async () => {
    setup();
    render(<SettingsPage />);
    await screen.findByText("Hosted GPT models");
    await userEvent.click(tab(/^Voice/));
    expect(window.location.hash).toBe("#voice");
    expect(await screen.findByText("not set up")).toBeInTheDocument();
  });

  it("shows the external-agent count of installed agents", async () => {
    setup({ acp: [CLAUDE, KIRO] });
    render(<SettingsPage />);
    await screen.findByText("Hosted GPT models");
    expect(tab(/External agents/)).toHaveTextContent("1/2");
  });
});

/* ---------- model providers ---------- */

describe("Settings page: model provider cards", () => {
  it("keeps Save disabled until a field changes", async () => {
    setup();
    render(<SettingsPage />);
    const save = await screen.findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    setValue(screen.getByLabelText(/Spend cap/), "6");
    expect(save).toBeEnabled();
  });

  it("saves the spend cap as a number and confirms with a toast", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    setValue(await screen.findByLabelText(/Spend cap/), "7.5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(toasts).toContainEqual({ text: "Saved OpenAI", tone: "ok" }));
    expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ cap_usd: 7.5 });
    expect(await screen.findByText("$7.50")).toBeInTheDocument();
  });

  it("sends the cap window and max output tokens that were chosen", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.selectOptions(await screen.findByLabelText(/^Cap window/), "daily");
    setValue(screen.getByLabelText(/^Max output tokens/), "2048");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(calls, "PUT", "/api/providers/openai")).toHaveLength(1));
    expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ cap_period: "daily", max_output_tokens: 2048 });
  });

  it("sends the default, pro-tier and flash-tier models picked from the live list", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    const defaults = await screen.findByLabelText(/^Default model/);
    const pro = screen.getByLabelText(/^Pro-tier model/);
    const flash = screen.getByLabelText(/^Flash-tier model/);
    await waitFor(() => expect(within(defaults).getByRole("option", { name: /GPT mini/ })).toBeInTheDocument());
    await waitFor(() => expect(within(pro).getByRole("option", { name: /GPT mini/ })).toBeInTheDocument());
    await waitFor(() => expect(within(flash).getByRole("option", { name: /GPT mini/ })).toBeInTheDocument());
    await userEvent.selectOptions(defaults, "gpt-mini");
    await userEvent.selectOptions(pro, "gpt-x");
    await userEvent.selectOptions(flash, "gpt-mini");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(calls, "PUT", "/api/providers/openai")).toHaveLength(1));
    expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ default_model: "gpt-mini", pro_model: "gpt-x", flash_model: "gpt-mini" });
  });

  it("disables a provider with the enabled checkbox", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    const box = await screen.findByRole("checkbox", { name: /enabled/ });
    expect(box).toBeChecked();
    await userEvent.click(box);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ enabled: false }));
  });

  it("shows a rejected save inline, keeps the edits, and does not toast", async () => {
    setup({ routes: { "PUT /api/providers/openai": () => jsonResponse({ detail: "cap below spend" }, { status: 422 }) } });
    render(<SettingsPage />);
    setValue(await screen.findByLabelText(/Spend cap/), "1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("⚠ cap below spend")).toBeInTheDocument();
    expect(toasts.find((t) => t.text.startsWith("Saved"))).toBeUndefined();
    expect(screen.getByLabelText(/Spend cap/)).toHaveValue(1);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("sends a typed API key and shows the stored hint beside the field", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    const field = await screen.findByLabelText(/^API key/);
    expect(field).toHaveAttribute("placeholder", "(unchanged)");
    expect(screen.getByText("stored …abcd")).toBeInTheDocument();
    await userEvent.type(field, "sk-test-123");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ api_key: "sk-test-123" }));
  });

  it("switches to an environment variable and saves its name", async () => {
    const { calls } = setup({
      providers: [prov({ auth_modes: [{ id: "api_key", label: "API key" }, { id: "env", label: "Environment variable" }] })],
    });
    render(<SettingsPage />);
    await userEvent.selectOptions(await screen.findByLabelText(/^Authentication/), "env");
    expect(screen.queryByLabelText(/^API key/)).toBeNull();
    setValue(screen.getByLabelText(/^Environment variable/), "OPENAI_PROXY_KEY");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ auth_mode: "env", api_key_env: "OPENAI_PROXY_KEY" }));
  });

  it("saves an endpoint override and an OpenAI organisation id", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.type(await screen.findByLabelText(/^API base/), "https://proxy.local/v1");
    await userEvent.type(screen.getByLabelText(/^Organization/), "org-42");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ api_base: "https://proxy.local/v1", organization: "org-42" }));
  });

  it("parses extra headers line by line and drops lines without a colon", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    setValue(await screen.findByLabelText(/^Extra headers/), "X-A: 1\njust text\nX-B:  two words ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(calls, "PUT", "/api/providers/openai")).toHaveLength(1));
    expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ extra_headers: { "X-A": "1", "X-B": "two words" } });
  });

  it("clears stored extra headers with one click", async () => {
    const { calls } = setup({ providers: [prov({ extra_header_names: ["X-Proxy-Token"] })] });
    render(<SettingsPage />);
    expect(await screen.findByText(/stored: X-Proxy-Token/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear headers" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai")).toEqual({ extra_headers: {} }));
  });

  it("shows the mock provider without endpoint or key fields", async () => {
    setup();
    render(<SettingsPage />);
    await screen.findByText("Hosted GPT models");
    await userEvent.click(screen.getByText("Mock", { selector: ".li .t" }));
    expect(await screen.findByText("Offline stub")).toBeInTheDocument();
    expect(screen.queryByLabelText(/^API base/)).toBeNull();
    expect(screen.queryByText("auth ready")).toBeNull();
  });
});

describe("Settings page: Ollama provider", () => {
  const ollama = () => prov({
    id: "ollama", kind: "ollama", label: "Ollama", has_api_key: false, api_key_hint: null, default_model: "llama3.2", context_window: null,
    catalog: { label: "Ollama", needs_key: false, note: "Local models", default_model: "llama3.2", auth_modes: ["none"] },
    auth_mode: "none", auth_modes: [{ id: "none", label: "None" }], auth_ready: true, auth_detail: "", key_env_default: "",
  });

  it("saves a context window picked from the list (16,384 is the default)", async () => {
    const { calls } = setup({ providers: [ollama()] });
    render(<SettingsPage />);
    const select = await screen.findByLabelText(/^Context window/);
    expect(select).toHaveValue("16384");
    expect(screen.getByRole("option", { name: "16,384 (default)" })).toBeInTheDocument();
    await userEvent.selectOptions(select, "32768");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/ollama")).toEqual({ context_window: 32768 }));
  });

  it("downloads a typed model and reports it installed", async () => {
    const { calls } = setup({
      providers: [ollama()],
      routes: { "POST /api/providers/ollama/pull": () => sseResponse([
        `data: ${JSON.stringify({ type: "progress", status: "pulling", completed: 50, total: 100 })}\n\n`,
        `data: ${JSON.stringify({ type: "done", model: "llama3.2:3b" })}\n\n`,
      ]) },
    });
    render(<SettingsPage />);
    await userEvent.type(await screen.findByPlaceholderText(/qwen2.5-coder/), "llama3.2:3b");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("Installed llama3.2:3b. Pick it in the model lists above.")).toBeInTheDocument();
    expect(lastSent(calls, "POST", "/api/providers/ollama/pull")).toEqual({ model: "llama3.2:3b" });
    expect(calls.some((c) => c.url.pathname === "/api/providers/ollama/models" && c.url.search === "?refresh=true")).toBe(true);
  });

  it("shows the error Ollama streams back during a download", async () => {
    setup({
      providers: [ollama()],
      routes: { "POST /api/providers/ollama/pull": () => sseResponse([`data: ${JSON.stringify({ type: "error", message: "disk full" })}\n\n`]) },
    });
    render(<SettingsPage />);
    await userEvent.type(await screen.findByPlaceholderText(/qwen2.5-coder/), "big:70b");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("⚠ disk full")).toBeInTheDocument();
  });

  it("starts a download when a suggestion chip is clicked", async () => {
    const { calls } = setup({
      providers: [ollama()],
      routes: { "GET /api/providers/ollama/ollama/suggestions": () => jsonResponse([{ id: "qwen2.5-coder:7b", note: "Good at code" }]) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "qwen2.5-coder:7b" }));
    await waitFor(() => expect(lastSent(calls, "POST", "/api/providers/ollama/pull")).toEqual({ model: "qwen2.5-coder:7b" }));
  });
});

describe("Settings page: cloud provider auth modes", () => {
  const bedrock = (over: Record<string, any> = {}) => prov({
    id: "bedrock", kind: "bedrock", label: "AWS Bedrock", has_api_key: false, api_key_hint: null,
    aws_region: "us-east-1", aws_profile: null, aws_access_key_hint: "AKIA…7XYZ", has_aws_secret: true, has_aws_session_token: false,
    catalog: { label: "Bedrock", needs_key: false, note: "Claude on AWS", default_model: "x", auth_modes: ["aws_keys"] },
    auth_mode: "aws_keys",
    auth_modes: [{ id: "api_key", label: "Bedrock API key" }, { id: "aws_keys", label: "Access keys" }, { id: "aws_profile", label: "Profile" }, { id: "aws_default", label: "Default chain" }],
    ...over,
  });

  it("stores a new AWS key pair and region without touching the secret that is already stored", async () => {
    const { calls } = setup({ providers: [bedrock()] });
    render(<SettingsPage />);
    expect(await screen.findByText("stored AKIA…7XYZ")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^Access key ID/), "AKIATEST0000");
    setValue(screen.getByLabelText(/^AWS region/), "eu-west-1");
    await userEvent.type(screen.getByLabelText(/^Secret access key/), "secret-test");
    await userEvent.type(screen.getByLabelText(/^Session token/), "temp-token");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/bedrock")).toEqual({
      aws_access_key_id: "AKIATEST0000", aws_region: "eu-west-1", aws_secret_access_key: "secret-test", aws_session_token: "temp-token",
    }));
  });

  it("asks for a profile name in profile mode", async () => {
    const { calls } = setup({ providers: [bedrock({ auth_mode: "aws_profile", aws_profile: "work" })] });
    render(<SettingsPage />);
    const field = await screen.findByLabelText(/^Profile name/);
    expect(field).toHaveValue("work");
    setValue(field, "sandbox");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/bedrock")).toEqual({ aws_profile: "sandbox" }));
  });

  it("explains where credentials come from in default-chain mode", async () => {
    const { calls } = setup({ providers: [bedrock()] });
    render(<SettingsPage />);
    await userEvent.selectOptions(await screen.findByLabelText(/^Authentication/), "aws_default");
    expect(screen.getByText(/Uses whatever AWS credentials/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Access key ID/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/bedrock")).toEqual({ auth_mode: "aws_default" }));
  });

  const vertex = (over: Record<string, any> = {}) => prov({
    id: "vertex", kind: "vertex_ai", label: "Vertex AI", has_api_key: false, api_key_hint: null,
    vertex_project: null, vertex_location: null,
    catalog: { label: "Vertex", needs_key: false, note: "Gemini on Google Cloud", default_model: "g", auth_modes: ["adc"] },
    auth_mode: "adc",
    auth_modes: [{ id: "api_key", label: "Express key" }, { id: "adc", label: "Application default" }, { id: "service_account_file", label: "Service-account file" }, { id: "service_account_json", label: "Service-account JSON" }],
    ...over,
  });

  it("saves the GCP project and location in application-default mode", async () => {
    const { calls } = setup({ providers: [vertex()] });
    render(<SettingsPage />);
    setValue(await screen.findByLabelText(/^GCP project ID/), "my-proj");
    setValue(screen.getByLabelText(/^Location/), "europe-west4");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/vertex")).toEqual({ vertex_project: "my-proj", vertex_location: "europe-west4" }));
  });

  it("takes a service-account file path", async () => {
    const { calls } = setup({ providers: [vertex({ auth_mode: "service_account_file" })] });
    render(<SettingsPage />);
    await userEvent.type(await screen.findByLabelText(/^Service-account JSON path/), "C:/keys/sa.json");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/vertex")).toEqual({ vertex_credentials_path: "C:/keys/sa.json" }));
  });

  it("pastes service-account JSON and shows which account is stored", async () => {
    const { calls } = setup({ providers: [vertex({ auth_mode: "service_account_json", has_service_account_json: true, service_account_email: "bot@proj.iam.gserviceaccount.com" })] });
    render(<SettingsPage />);
    expect(await screen.findByText("stored bot@proj.iam.gserviceaccount.com")).toBeInTheDocument();
    setValue(screen.getByLabelText(/^Service-account JSON/), '{"type": "service_account"}');
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/vertex")).toEqual({ vertex_credentials_json: '{"type": "service_account"}' }));
  });
});

describe("Settings page: account credits panel", () => {
  it("shows the balance left and sends a manually entered balance", async () => {
    const { calls } = setup({ providers: [prov({ credits: CREDITS })] });
    render(<SettingsPage />);
    expect(await screen.findByText(/12\.5000 left/)).toBeInTheDocument();
    expect(screen.getByText(/entered by you · \$7\.5000 used here since/)).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("your current balance ($)"), "15");
    await userEvent.click(screen.getByRole("button", { name: "Set balance" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai/credits")).toEqual({ balance_usd: 15 }));
  });

  it("stops tracking the balance from the panel", async () => {
    const { calls } = setup({ providers: [prov({ credits: CREDITS })] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Stop tracking" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai/credits")).toEqual({ mode: "off" }));
  });

  it("switches strict mode on from the panel", async () => {
    const { calls } = setup({ providers: [prov({ credits: CREDITS })] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: /Strict/ }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/providers/openai/credits")).toEqual({ strict: true }));
  });

  it("refreshes the balance from the provider when the provider can report it", async () => {
    const { calls } = setup({
      providers: [prov({ credits: { ...CREDITS, mode: "auto", source: "OpenRouter", auto_available: true, why_not: null, spent_since_usd: null, remaining_usd: 3 } })],
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Refresh from provider" }));
    await waitFor(() => expect(sent(calls, "POST", "/api/providers/openai/credits/refresh")).toHaveLength(1));
  });
});

/* ---------- agents & sandbox tab ---------- */

describe("Settings page: Agents & sandbox tab", () => {
  it("shows a sandbox that is not running as a failure", async () => {
    setup({ sandbox: { docker: false, image: false, detail: "Docker is not running" } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    expect(await screen.findByText("Docker is not running")).toHaveClass("pill", "bad");
  });

  it("turns network access in the sandbox on and reflects the saved state", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const box = toggle(/Allow network inside the sandbox/);
    expect(box).not.toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sandbox_network: true });
  });

  it("sends the workspace folder typed into the field", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    const field = screen.getByPlaceholderText("e.g. H:/htdocs/my-project");
    expect(field).toHaveValue("H:/htdocs/demo");
    setValue(field, "H:/htdocs/other");
    await userEvent.click(save);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ workspace_dir: "H:/htdocs/other" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
  });

  it("takes the workspace folder from the native folder dialog", async () => {
    const { calls } = setup({
      routes: { "POST /api/fs/pick-folder": () => jsonResponse({ path: "D:/work/app", cancelled: false }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(screen.getByRole("button", { name: "Browse…" }));
    await waitFor(() => expect(screen.getByPlaceholderText("e.g. H:/htdocs/my-project")).toHaveValue("D:/work/app"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ workspace_dir: "D:/work/app" }));
  });

  it("falls back to the in-app folder browser when no native dialog exists", async () => {
    const { calls } = setup({
      routes: { "POST /api/fs/pick-folder": () => jsonResponse({ detail: "no dialog" }, { status: 501 }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(screen.getByRole("button", { name: "Browse…" }));
    await userEvent.click(await screen.findByText("agent-studio"));
    expect(await screen.findByText("H:/htdocs/agent-studio")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ workspace_dir: "H:/htdocs/agent-studio" }));
  });

  it("shows the reason when the folder dialog fails", async () => {
    setup({ routes: { "POST /api/fs/pick-folder": () => jsonResponse({ detail: "dialog crashed" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(screen.getByRole("button", { name: "Browse…" }));
    expect(await screen.findByText("dialog crashed")).toBeInTheDocument();
  });

  it("sends the thinking level picked from the list", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const select = within(screen.getByText("Model thinking").closest(".row") as HTMLElement).getByRole("combobox");
    expect(select).toHaveValue("medium");
    await userEvent.selectOptions(select, "high");
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ thinking_level: "high" }));
  });

  it("turns on automatic debugging of failing tools", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(toggle(/Debug failing tools automatically/));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ auto_debug_mcp: true }));
  });

  it("turns on agent-built sub-agents and saves the per-turn cap", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(toggle(/Let agents create their own sub-agents/));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ agent_spawning_enabled: true }));
    const cap = screen.getByLabelText(/Max agent-created sub-agents per turn/);
    fireEvent.change(cap, { target: { value: "5" } });
    fireEvent.blur(cap);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ max_spawns_per_turn: 5 }));
  });

  it("saves the long-run limits when their fields lose focus", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const minutes = screen.getByLabelText(/^Max minutes per turn/);
    fireEvent.change(minutes, { target: { value: "45" } });
    fireEvent.blur(minutes);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ max_turn_minutes: 45 }));
    const calls_ = screen.getByLabelText(/^Max model calls per turn/);
    fireEvent.change(calls_, { target: { value: "60" } });
    fireEvent.blur(calls_);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ max_llm_calls_per_turn: 60 }));
  });

  // (see also the failed-save test below: putSettings has no catch)
  // Known bug: a blank "Max minutes per turn" is sent as 0, which the UI itself documents as "no limit".
  // Clearing the field silently removes the time limit. it.fails keeps the suite green while the bug exists.
  it("does not silently remove the time limit when the field is cleared: nothing is sent and the problem is shown", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const minutes = screen.getByLabelText(/^Max minutes per turn/) as HTMLInputElement;
    fireEvent.change(minutes, { target: { value: "" } });
    fireEvent.blur(minutes);
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a number between 0 and 100000");
    expect(sent(calls, "PUT", "/api/settings")).toHaveLength(0);
  });

  it("still sends an explicit 0 (no limit) for the time limit", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const minutes = screen.getByLabelText(/^Max minutes per turn/);
    fireEvent.change(minutes, { target: { value: "0" } });
    fireEvent.blur(minutes);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ max_turn_minutes: 0 }));
  });

  it("turns external-agent calls on and saves their limits", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(toggle(/Let agents call external agents/));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ agents_can_call_external: true }));
    const perTurn = screen.getByLabelText(/^Max external-agent calls per turn/);
    fireEvent.change(perTurn, { target: { value: "9" } });
    fireEvent.blur(perTurn);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ max_external_calls_per_turn: 9 }));
    const minutes = screen.getByLabelText(/^Max minutes per external-agent call/);
    fireEvent.change(minutes, { target: { value: "20" } });
    fireEvent.blur(minutes);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ external_timeout_min: 20 }));
  });

  it("adds a fallback model, changes it, and removes it", async () => {
    const { calls, settings } = setup({ providers: [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic", enabled: false })] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(screen.getByRole("button", { name: "+ Add a fallback model" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ model_fallbacks: [{ provider: "openai", model: null }] }));
    expect(settings.model_fallbacks).toHaveLength(1);
    const row = await screen.findByRole("button", { name: "Remove" });
    const rowEl = row.closest(".row") as HTMLElement;
    await waitFor(() => expect(within(rowEl).getAllByRole("combobox")).toHaveLength(2));
    const [, modelSelect] = within(rowEl).getAllByRole("combobox");
    await screen.findByRole("option", { name: /GPT mini/ });
    await userEvent.selectOptions(modelSelect, "gpt-mini");
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ model_fallbacks: [{ provider: "openai", model: "gpt-mini" }] }));
    await userEvent.click(within(rowEl.parentElement as HTMLElement).getAllByRole("button", { name: "Remove" })[0]);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ model_fallbacks: [] }));
  });

  it("resets a fallback's model when its provider changes", async () => {
    const { calls } = setup({
      providers: [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic" })],
      settings: { model_fallbacks: [{ provider: "openai", model: "gpt-x" }] },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const rowEl = (await screen.findByRole("button", { name: "Remove" })).closest(".row") as HTMLElement;
    await userEvent.selectOptions(within(rowEl).getAllByRole("combobox")[0], "anthropic");
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ model_fallbacks: [{ provider: "anthropic", model: null }] }));
  });

  it("hides the add button once five fallbacks exist", async () => {
    const five = Array.from({ length: 5 }, () => ({ provider: "openai", model: null }));
    setup({ settings: { model_fallbacks: five } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    expect(await screen.findAllByRole("button", { name: "Remove" })).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "+ Add a fallback model" })).toBeNull();
  });

  // Known bug: harness toggles/selects call putSettings(), which has no catch and no toast. A failed save
  // is only an unhandled promise rejection; nothing reaches the user. Expected: an inline or toast error.
  it("reports a failed harness save to the user with an error toast", async () => {
    setup({ routes: { "PUT /api/settings": () => jsonResponse({ detail: "disk locked" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(toggle(/Allow network inside the sandbox/));
    await waitFor(() => expect(toasts).toContainEqual({ text: "disk locked", tone: "err" }));
  });

  it("keeps the harness toggle in its saved state when the save fails", async () => {
    setup({ routes: { "PUT /api/settings": () => jsonResponse({ detail: "disk locked" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    const box = toggle(/Allow network inside the sandbox/);
    await userEvent.click(box);
    await waitFor(() => expect(toasts.some((t) => t.text === "disk locked")).toBe(true));
    expect(box).not.toBeChecked();
  });

  it("links to MCP servers and Skills from the footer", async () => {
    setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    expect(screen.getByRole("link", { name: "MCP servers" })).toHaveAttribute("href", expect.stringMatching(/^\/mcp\/?$/));
    expect(screen.getByRole("link", { name: "Skills" })).toHaveAttribute("href", expect.stringMatching(/^\/skills\/?$/));
  });
});

/* ---------- external agents ---------- */

describe("Settings page: external agents tab", () => {
  const open = async () => {
    const env = setup({ acp: [CLAUDE, KIRO] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await screen.findByText("Claude Code", { selector: ".li .t" });
    return env;
  };

  it("shows an agent's install state, resolved path, and this month's usage", async () => {
    await open();
    expect(screen.getByText("installed")).toHaveClass("pill", "ok");
    expect(screen.getByText("→ C:/bin/claude.exe")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("This month: 3 turns · 4.5 min · $0.0123 reported by the agent");
  });

  it("does not allow testing or deleting a builtin agent that is missing", async () => {
    await open();
    await userEvent.click(screen.getByText("Kiro", { selector: ".li .t" }));
    expect(await screen.findByText("not found on PATH")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
    await userEvent.click(screen.getByText("Claude Code", { selector: ".li .t" }));
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("reports a successful connection test with version, protocol, modes and auth", async () => {
    const { calls } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("connected")).toHaveClass("pill", "ok");
    expect(document.body).toHaveTextContent("Claude Code 1.2.3 · ACP v1 · modes: default, plan · auth: oauth");
    expect(sent(calls, "POST", "/api/acp/agents/claude_code/probe")).toHaveLength(1);
  });

  it("reports a failed connection test with the error", async () => {
    // A failing probe comes back as a non-2xx response; the page maps it to { ok: false, error }.
    setup({
      acp: [CLAUDE],
      routes: { "POST /api/acp/agents/claude_code/probe": () => jsonResponse({ detail: "spawn ENOENT" }, { status: 500 }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("failed")).toHaveClass("pill", "bad");
    expect(screen.getByText("spawn ENOENT")).toBeInTheDocument();
  });

  it("saves the command, arguments and turn limit that were edited", async () => {
    const { calls } = setup({ acp: [CLAUDE] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    const command = await screen.findByLabelText(/^Command/);
    await userEvent.clear(command);
    await userEvent.type(command, "claude-dev");
    setValue(screen.getByLabelText(/^Arguments/), "--acp\n--verbose");
    setValue(screen.getByLabelText(/^Max turns per session/), "30");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/acp/agents/claude_code")).toEqual({
      command: "claude-dev", args: ["--acp", "--verbose"], max_turns: 30,
      month_cost_cap: 0, month_turn_cap: 0, month_minutes_cap: 0, env: {},
    }));
    await waitFor(() => expect(toasts).toContainEqual({ text: "Saved Claude Code", tone: "ok" }));
  });

  it("saves monthly caps and sends only the environment values that were typed", async () => {
    const { calls } = setup({ acp: [CLAUDE] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    setValue(await screen.findByLabelText(/^Monthly turn limit/), "50");
    setValue(screen.getByLabelText(/^Monthly time limit/), "120");
    setValue(screen.getByLabelText(/^Monthly cost limit/), "2.5");
    await userEvent.type(screen.getByLabelText(/^ANTHROPIC_API_KEY/), "sk-ant-test");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/acp/agents/claude_code")).toEqual(expect.objectContaining({
      month_turn_cap: 50, month_minutes_cap: 120, month_cost_cap: 2.5, env: { ANTHROPIC_API_KEY: "sk-ant-test" },
    })));
  });

  it("enables or disables an agent straight away", async () => {
    const { calls } = setup({ acp: [CLAUDE] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /enabled/ }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/acp/agents/claude_code")).toEqual({ enabled: false }));
  });

  it("shows the reason on the row when a save is rejected", async () => {
    setup({
      acp: [CLAUDE],
      routes: { "PUT /api/acp/agents/claude_code": () => jsonResponse({ detail: "max_turns must be at least 1" }, { status: 422 }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    setValue(await screen.findByLabelText(/^Max turns per session/), "0");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("⚠ max_turns must be at least 1")).toBeInTheDocument();
  });

  it("deletes a custom agent", async () => {
    const { calls } = setup({ acp: [KIRO] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(sent(calls, "DELETE", "/api/acp/agents/kiro")).toHaveLength(1));
  });

  it("adds a custom agent once its id and command are filled in", async () => {
    const { calls } = setup({ acp: [CLAUDE] });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByText("+ Add custom agent"));
    const add = screen.getByRole("button", { name: "Add" });
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Id$/), "my-agent");
    await userEvent.type(screen.getByLabelText(/^Label$/), "My agent");
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Command/), "my-cli");
    expect(add).toBeEnabled();
    setValue(screen.getByLabelText(/^Arguments/), "--acp\n\n--debug ");
    await userEvent.click(add);
    await waitFor(() => expect(sent(calls, "POST", "/api/acp/agents")).toHaveLength(1));
    expect(lastSent(calls, "POST", "/api/acp/agents")).toEqual({ id: "my-agent", label: "My agent", command: "my-cli", args: ["--acp", "--debug"] });
    await waitFor(() => expect(screen.getByLabelText(/^Id$/)).toHaveValue(""));
  });

  it("shows the server's reason when the new agent's id is already taken", async () => {
    setup({
      acp: [CLAUDE],
      routes: { "POST /api/acp/agents": () => jsonResponse({ detail: "id already exists" }, { status: 409 }) },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /External agents/ }));
    await userEvent.click(await screen.findByText("+ Add custom agent"));
    await userEvent.type(screen.getByLabelText(/^Id$/), "claude_code");
    await userEvent.type(screen.getByLabelText(/^Command/), "claude");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText("⚠ id already exists")).toBeInTheDocument();
  });
});

/* ---------- assistant tab ---------- */

describe("Settings page: Assistant tab", () => {
  const open = async (settings: Record<string, any> = {}, providers?: any[]) => {
    const env = setup({
      settings,
      providers: providers ?? [prov(), prov({ id: "anthropic", label: "Anthropic", kind: "anthropic", catalog: { label: "A", needs_key: true, note: "Claude API", default_model: "x", auth_modes: ["api_key"] } }), MOCK],
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Assistant/ }));
    await screen.findByText("The side agent.", { exact: false });
    return env;
  };

  it("turns the assistant on and confirms it", async () => {
    const { calls } = await open();
    expect(screen.getByText("off", { selector: ".pill" })).toBeInTheDocument();
    await userEvent.click(toggle(/^Assistant on$/));
    expect(await screen.findByText("Assistant is on")).toBeInTheDocument();
    expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sidebot_enabled: true });
    expect(screen.getByText("on", { selector: ".pill" })).toBeInTheDocument();
  });

  it("lets you stop the assistant seeing what is on screen, and shows it as on by default", async () => {
    const { calls } = await open();
    expect(toggle(/^Assistant can see what is on screen$/)).toBeChecked();          // an older server that says nothing means "on"
    await userEvent.click(toggle(/^Assistant can see what is on screen$/));
    expect(await screen.findByText("The assistant can no longer see what is on screen")).toBeInTheDocument();
    expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sidebot_page_context: false });
  });

  it("switches screen reading back on, and reports a failure", async () => {
    const { calls } = await open({ sidebot_page_context: false });
    expect(toggle(/^Assistant can see what is on screen$/)).not.toBeChecked();
    await userEvent.click(toggle(/^Assistant can see what is on screen$/));
    expect(await screen.findByText("The assistant can see what is on screen")).toBeInTheDocument();
    expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sidebot_page_context: true });
  });

  it("says where the assistant runs right now", async () => {
    await open();
    expect(document.body).toHaveTextContent("Right now it runs on: openai · gpt-x.");
    expect(screen.getByRole("option", { name: /Automatic \(openai\)/ })).toBeInTheDocument();
  });

  it("says when no provider is available to the assistant", async () => {
    await open({ sidebot_resolved: { provider: null, model: null } });
    expect(document.body).toHaveTextContent("no provider available — add one under Model providers");
  });

  it("reports a failed screen-reading switch", async () => {
    setup({ routes: { "PUT /api/settings": () => jsonResponse({ detail: "nope" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Assistant/ }));
    await userEvent.click(toggle(/^Assistant can see what is on screen$/));
    expect(await screen.findByText("⚠ nope")).toBeInTheDocument();
  });

  it("reports a failed on/off switch for the assistant", async () => {
    setup({ routes: { "PUT /api/settings": () => jsonResponse({ detail: "locked" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Assistant/ }));
    await userEvent.click(toggle(/^Assistant on$/));
    expect(await screen.findByText("⚠ locked")).toBeInTheDocument();
    expect(screen.getByText("off", { selector: ".pill" })).toBeInTheDocument();
  });

  it("saves a chosen provider and clears the model", async () => {
    const { calls } = await open();
    await userEvent.selectOptions(screen.getByLabelText(/^Provider/), "anthropic");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sidebot_provider: "anthropic", sidebot_model: "" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("saves a model picked for the automatically chosen provider", async () => {
    const { calls } = await open();
    await screen.findByRole("option", { name: /GPT X/ });
    await userEvent.selectOptions(screen.getByLabelText(/^Model/), "gpt-x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/settings")).toEqual({ sidebot_provider: "", sidebot_model: "gpt-x" }));
  });

  it("shows the reason when saving the assistant's provider fails", async () => {
    setup({ routes: { "PUT /api/settings": () => jsonResponse({ detail: "unknown provider" }, { status: 422 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Assistant/ }));
    await userEvent.selectOptions(await screen.findByLabelText(/^Provider/), "openai");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("⚠ unknown provider")).toBeInTheDocument();
  });
});

/* ---------- voice tab ---------- */

describe("Settings page: Voice tab", () => {
  it("connects an ElevenLabs key and lists its voices and plan usage", async () => {
    const { calls } = setup({
      routes: {
        "GET /api/voice/voices": () => jsonResponse([{ voice_id: "v1", name: "Rachel", category: "premade", preview_url: null }]),
        "GET /api/voice/models": () => jsonResponse([{ model_id: "eleven_multilingual_v2", name: "Multilingual v2" }]),
        "GET /api/voice/subscription": () => jsonResponse({ tier: "creator", character_count: 1200, character_limit: 100000 }),
      },
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Voice/ }));
    await userEvent.type(await screen.findByPlaceholderText("paste your xi-api-key"), "xi-test-key");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/^connected/)).toBeInTheDocument();
    expect(lastSent(calls, "PUT", "/api/voice/settings")).toEqual({ api_key: "xi-test-key" });
    expect(await screen.findByRole("option", { name: /Rachel · premade/ })).toBeInTheDocument();
    expect(document.body).toHaveTextContent("creator plan · 1,200 / 100,000 credits used");
  });

  it("saves auto-read and dictation as soon as they are switched", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Voice/ }));
    const autoRead = await screen.findByText("Read new answers aloud automatically");
    await userEvent.click(within(autoRead.closest(".row") as HTMLElement).getByRole("checkbox"));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/voice/settings")).toEqual({ auto_read: true }));
    const dictation = screen.getByText("Show the microphone (dictation) in the chat composer");
    await userEvent.click(within(dictation.closest(".row") as HTMLElement).getByRole("checkbox"));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/voice/settings")).toEqual({ dictation: true }));
  });

  it("saves the monthly character cap on blur", async () => {
    const { calls } = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Voice/ }));
    const cap = await screen.findByLabelText(/^Monthly character cap/);
    fireEvent.change(cap, { target: { value: "5000" } });
    fireEvent.blur(cap);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/voice/settings")).toEqual({ char_cap: 5000 }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("shows the voice service's error when the settings cannot be loaded", async () => {
    setup({ routes: { "GET /api/voice/settings": () => jsonResponse({ detail: "voice offline" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Voice/ }));
    expect(await screen.findByText("voice offline")).toBeInTheDocument();
  });
});

/* ---------- telemetry tab ---------- */

describe("Settings page: Telemetry tab", () => {
  const open = async (over: Parameters<typeof setup>[0] = {}) => {
    const env = setup(over);
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Telemetry/ }));
    await screen.findByText("Clear all");
    return env;
  };

  it("shows the receiver endpoints, the viewer link and an empty store", async () => {
    await open();
    expect(document.body).toHaveTextContent("POST http://127.0.0.1:8765/v1/traces | /v1/metrics | /v1/logs");
    expect(screen.getByRole("link", { name: /Open telemetry viewer/ })).toHaveAttribute("href", expect.stringMatching(/^\/telemetry\/?$/));
    expect(screen.getByText("Nothing stored yet.")).toBeInTheDocument();
    expect(screen.getByText("0 records stored (local SQLite)")).toBeInTheDocument();
  });

  it("lists stored records per agent and signal", async () => {
    await open({ routes: { "GET /api/otel/summary": () => jsonResponse({ total: 12, by_source: [{ source: "claude_code", signal: "metric", n: 12 }] }) } });
    const row = screen.getByText("claude_code").closest("tr") as HTMLElement;
    expect(within(row).getByText("metric")).toBeInTheDocument();
    expect(within(row).getByText("12")).toBeInTheDocument();
    expect(screen.getByText("12 records stored (local SQLite)")).toBeInTheDocument();
  });

  it("turns storage off and the connector injection on", async () => {
    const { calls } = await open();
    await userEvent.click(screen.getByRole("checkbox", { name: /store OpenTelemetry data/ }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({ enabled: false }));
    await userEvent.click(screen.getByRole("checkbox", { name: /point external agents at this connector/ }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({ inject_env: true }));
  });

  it("saves the retention period when it loses focus", async () => {
    const { calls } = await open();
    const retention = screen.getByLabelText(/^Retention/);
    fireEvent.change(retention, { target: { value: "14" } });
    fireEvent.blur(retention);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({ retention_days: 14 }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("saves the receiver URL on blur only when it was edited", async () => {
    const { calls } = await open();
    const receiver = screen.getByLabelText(/^This server/);
    fireEvent.blur(receiver);
    expect(sent(calls, "PUT", "/api/otel/settings")).toHaveLength(0);
    setValue(receiver, "http://192.168.1.5:8765");
    fireEvent.blur(receiver);
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({ receiver_url: "http://192.168.1.5:8765" }));
  });

  it("forwards everything to a collector with the headers typed, then clears the header box", async () => {
    const { calls } = await open();
    setValue(screen.getByLabelText(/^Forward everything/), "http://localhost:4318");
    setValue(screen.getByLabelText(/^Forward headers/), "Authorization: Bearer t0k");
    await userEvent.click(screen.getByRole("button", { name: "Save forwarding" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({
      forward_url: "http://localhost:4318", forward_headers: { Authorization: "Bearer t0k" },
    }));
    await waitFor(() => expect(screen.getByLabelText(/^Forward headers/)).toHaveValue(""));
    expect(await screen.findByRole("button", { name: "Stop forwarding" })).toBeInTheDocument();
  });

  it("saves the forwarding URL alone when no headers were typed", async () => {
    const { calls } = await open();
    setValue(screen.getByLabelText(/^Forward everything/), "http://localhost:4318");
    await userEvent.click(screen.getByRole("button", { name: "Save forwarding" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({ forward_url: "http://localhost:4318" }));
  });

  it("stops forwarding and shows the forwarding counters while it was on", async () => {
    const { calls } = await open({ otel: { forward_url: "http://c:4318", forwarded: 4, forward_errors: 1, last_forward_error: "timeout" } });
    expect(screen.getByText(/forwarded 4 · errors 1 · last: timeout/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop forwarding" }));
    await waitFor(() => expect(lastSent(calls, "PUT", "/api/otel/settings")).toEqual({ forward_url: "", forward_headers: {} }));
  });

  it("clears all telemetry only after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { calls } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(sent(calls, "DELETE", "/api/otel/records")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(sent(calls, "DELETE", "/api/otel/records")).toHaveLength(1));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  // Known bug: OtelCard returns null when its load fails, so the error it stores in `msg` is never rendered.
  it("shows why the telemetry settings could not load", async () => {
    setup({ routes: { "GET /api/otel/settings": () => jsonResponse({ detail: "telemetry db locked" }, { status: 500 }) } });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Telemetry/ }));
    expect(await screen.findByText(/telemetry db locked/)).toBeInTheDocument();
  });
});
