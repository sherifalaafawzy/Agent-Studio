import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/settings/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/OllamaSetup", () => ({ default: () => <div>ollama-setup</div> }));
vi.mock("@/components/OllamaLibrary", () => ({ default: () => <div>ollama-library</div> }));
const installer = vi.hoisted(() => ({ onChange: undefined as undefined | (() => void) }));
vi.mock("@/components/AgentInstaller", () => ({ default: ({ onChange }: { onChange?: () => void }) => { installer.onChange = onChange; return <div>agent-installer</div>; } }));

const OPENAI = {
  id: "openai", kind: "openai", label: "OpenAI", enabled: true, has_api_key: true, api_key_hint: "…abcd", api_base: null, vertex_project: null, vertex_location: null, vertex_credentials_path: null,
  default_model: "gpt-x", pro_model: null, flash_model: null, cap_usd: 5, cap_period: "monthly", max_output_tokens: 4096,
  catalog: { label: "OpenAI", needs_key: true, note: "Hosted GPT models", default_model: "gpt-x", auth_modes: ["api_key"] },
  auth_mode: "api_key", auth_modes: [{ id: "api_key", label: "API key" }], api_key_env: null, key_env_default: "OPENAI_API_KEY", has_service_account_json: false, service_account_email: null,
  extra_header_names: [], organization: null, auth_ready: true, auth_detail: "key stored",
};
const PROMPT = { core: "You are the side agent.\nSafety rules (always apply)", default_guidance: "Help the user use the app.", effective: "CORE\n\nGuidance:\nHelp the user use the app.", max_guidance: 4000 };
const TOOLS = [{ name: "navigate", description: "Open a page of Agent Studio for you.", enabled: true }, { name: "start_tour", description: "Start a guided tour.", enabled: true }, { name: "explain", description: "Point at one thing.", enabled: false }];

function setup(settings: Record<string, unknown> = {}, routes: Record<string, any> = {}) {
  const s: Record<string, any> = {
    sandbox_network: false, workspace_dir: "H:/demo", thinking_level: "medium", auto_debug_mcp: false, model_fallbacks: [], agent_spawning_enabled: false, max_spawns_per_turn: 3,
    max_turn_minutes: 30, max_llm_calls_per_turn: 40, agents_can_call_external: false, max_external_calls_per_turn: 5, external_timeout_min: 10,
    sidebot_enabled: true, sidebot_provider: "", sidebot_model: "", sidebot_resolved: { provider: "openai", model: "gpt-x" },
    sidebot_guidance: "", sidebot_tools: TOOLS, sidebot_prompt: PROMPT, ...settings,
  };
  const r = routeFetch({
    "GET /api/providers": () => jsonResponse([OPENAI]), "GET /api/settings": () => jsonResponse(s),
    "PUT /api/settings": (init?: RequestInit) => {
      const b = JSON.parse(init?.body as string);
      if ("sidebot_guidance" in b) { s.sidebot_guidance = b.sidebot_guidance; s.sidebot_prompt = { ...PROMPT, effective: `CORE\n\nGuidance:\n${b.sidebot_guidance || PROMPT.default_guidance}` }; }
      if ("sidebot_tools_off" in b) s.sidebot_tools = TOOLS.map((t) => ({ ...t, enabled: !b.sidebot_tools_off.includes(t.name) }));
      return jsonResponse(s);
    },
    "GET /api/sandbox/status": () => jsonResponse({ docker: true, image: true, detail: "ok" }), "GET /api/acp/agents": () => jsonResponse([]),
    "GET /api/providers/openai/models": () => jsonResponse({ provider: "openai", source: "live", error: null, models: [], cached: false }),
    "GET /api/otel/settings": () => jsonResponse({ enabled: true }), "GET /api/otel/summary": () => jsonResponse({ total: 0, by_source: [] }),
    ...routes,
  });
  return { ...r, s };
}
const openTab = async (name: RegExp) => { render(<SettingsPage />); await userEvent.click(await screen.findByRole("tab", { name })); };
const sent = (calls: any[]) => calls.filter((c) => c.method === "PUT" && c.url.pathname === "/api/settings").map((c) => c.body);
beforeEach(() => { window.location.hash = ""; });

describe("Settings: Assistant controls", () => {
  it("shows what it may do, the locked safety rules, the guidance and the exact prompt", async () => {
    setup();
    await openTab(/^Assistant/);
    expect(await screen.findByText("What it may do")).toBeInTheDocument();
    for (const n of ["navigate", "start_tour", "explain"]) expect(screen.getByText(n)).toBeInTheDocument();
    expect(within(screen.getByText("explain").closest(".row") as HTMLElement).getByRole("checkbox")).not.toBeChecked();
    expect(within(screen.getByText("navigate").closest(".row") as HTMLElement).getByRole("checkbox")).toBeChecked();
    const rules = screen.getByLabelText("Safety rules (locked)");
    expect(rules).toHaveValue(PROMPT.core);
    expect(rules).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Your guidance")).toHaveValue("Help the user use the app.");
    await userEvent.click(screen.getByText("See exactly what the model receives"));
    expect(screen.getByText(/CORE\s+Guidance:\s+Help the user use the app\./)).toBeInTheDocument();
    expect(screen.getByText(`${"Help the user use the app.".length} / 4000`)).toBeInTheDocument();
  });

  it("saves your own guidance and can reset it to the default", async () => {
    const { calls } = setup();
    await openTab(/^Assistant/);
    const box = await screen.findByLabelText("Your guidance");
    const save = screen.getByRole("button", { name: "Save guidance" });
    const reset = screen.getByRole("button", { name: "Reset to default" });
    expect(save).toBeDisabled();
    expect(reset).toBeDisabled();
    await userEvent.clear(box);
    await userEvent.type(box, "Answer in French.");
    await userEvent.click(save);
    expect(await screen.findByText("Guidance saved")).toBeInTheDocument();
    expect(sent(calls).at(-1)).toEqual({ sidebot_guidance: "Answer in French." });
    await waitFor(() => expect(screen.getByLabelText("Your guidance")).toHaveValue("Answer in French."));
    await userEvent.click(screen.getByRole("button", { name: "Reset to default" }));
    expect(await screen.findByText("Back to the default guidance")).toBeInTheDocument();
    expect(sent(calls).at(-1)).toEqual({ sidebot_guidance: "" });
    await waitFor(() => expect(screen.getByLabelText("Your guidance")).toHaveValue("Help the user use the app."));
  });

  it("typing the default text back saves as 'no custom guidance'", async () => {
    const { calls } = setup({ sidebot_guidance: "Custom." });
    await openTab(/^Assistant/);
    const box = await screen.findByLabelText("Your guidance");
    await userEvent.clear(box);
    await userEvent.type(box, "Help the user use the app.");
    await userEvent.click(screen.getByRole("button", { name: "Save guidance" }));
    await waitFor(() => expect(sent(calls).at(-1)).toEqual({ sidebot_guidance: "" }));
  });

  it("switches a tool off and on again, and sends the full list of switched-off tools", async () => {
    const { calls } = setup();
    await openTab(/^Assistant/);
    const row = async (n: string) => within((await screen.findByText(n)).closest(".row") as HTMLElement).getByRole("checkbox");
    await userEvent.click(await row("navigate"));
    expect(await screen.findByText("navigate switched off")).toBeInTheDocument();
    expect(sent(calls).at(-1)).toEqual({ sidebot_tools_off: ["navigate", "explain"] });
    await userEvent.click(await row("explain"));
    expect(await screen.findByText("explain allowed")).toBeInTheDocument();
    expect(sent(calls).at(-1)).toEqual({ sidebot_tools_off: ["navigate"] });
  });

  it("reports a refused save of guidance or a tool switch", async () => {
    setup({}, { "PUT /api/settings": () => jsonResponse({ detail: "locked" }, { status: 500 }) });
    await openTab(/^Assistant/);
    await userEvent.click(within((await screen.findByText("navigate")).closest(".row") as HTMLElement).getByRole("checkbox"));
    expect(await screen.findByText("⚠ locked")).toBeInTheDocument();
    const box = screen.getByLabelText("Your guidance");
    await userEvent.type(box, " more");
    await userEvent.click(screen.getByRole("button", { name: "Save guidance" }));
    await waitFor(() => expect(screen.getAllByText("⚠ locked").length).toBeGreaterThan(0));
  });

  it("works with an older server that sends no prompt or tool list", async () => {
    setup({ sidebot_tools: undefined, sidebot_prompt: undefined, sidebot_guidance: undefined });
    await openTab(/^Assistant/);
    expect(await screen.findByLabelText("Your guidance")).toHaveValue("");
    expect(screen.queryByText("navigate")).toBeNull();
  });
});

describe("Settings: local models and coding agents", () => {
  it("has a Local models tab with the installer and the library", async () => {
    setup();
    await openTab(/^Local models/);
    expect(await screen.findByText("ollama-setup")).toBeInTheDocument();
    expect(screen.getByText("ollama-library")).toBeInTheDocument();
    expect(screen.getByText(/Run AI models on this PC for free/)).toBeInTheDocument();
  });

  it("offers one-click coding agent installs on the External agents tab, and refreshes the list when one is installed", async () => {
    const { calls } = setup();
    await openTab(/^External agents/);
    const box = await screen.findByText(/Install coding agents/);
    expect(box.closest("details")).toHaveClass("inst-box");
    await userEvent.click(box);
    expect(screen.getByText("agent-installer")).toBeInTheDocument();
    const before = calls.filter((c) => c.url.pathname === "/api/acp/agents").length;
    installer.onChange?.();
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/acp/agents").length).toBe(before + 1));
  });
});
