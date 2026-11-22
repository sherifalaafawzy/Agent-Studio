import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/settings/page";
import { forgetModels } from "@/components/ModelSelect";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

const base = (over: Record<string, any>) => ({
  enabled: true, has_api_key: true, api_key_hint: "…abcd", api_base: null, vertex_project: null, vertex_location: null, vertex_credentials_path: null,
  default_model: "m", pro_model: null, flash_model: null, cap_usd: 5, cap_period: "monthly", max_output_tokens: 4096,
  catalog: { label: "x", needs_key: true, note: "", default_model: "m", auth_modes: ["api_key"] }, auth_mode: "api_key", auth_modes: [{ id: "api_key", label: "API key" }],
  api_key_env: null, key_env_default: "", has_service_account_json: false, service_account_email: null, extra_header_names: [], organization: null, auth_ready: true, auth_detail: "ok", ...over,
});
const OPENAI = base({ id: "openai", kind: "openai", label: "OpenAI" });
const CUSTOM = base({ id: "custom_gpu", kind: "openai_compatible", label: "Office GPU", custom: true, network_scope: "network", cap_usd: 1, manual_models: [], model_prices: {}, has_api_key: false, auth_mode: "none",
  auth_modes: [{ id: "none", label: "No key" }, { id: "api_key", label: "API key" }], auth_detail: "no key", catalog: { label: "OpenAI-compatible endpoint", needs_key: false, note: "", default_model: "", auth_modes: ["none"], custom: true } });

function setup(providers: any[] = [OPENAI, CUSTOM]) {
  let list = providers;
  const models = (pid: string) => jsonResponse({ provider: pid, source: "manual", error: "ConnectError: refused", models: [{ id: "m", label: "m", input_per_m: null, output_per_m: null }], cached: false });
  return routeFetch({
    "GET /api/providers": () => jsonResponse(list),
    "GET /api/settings": () => jsonResponse({ sandbox_network: false, workspace_dir: "", thinking_level: "medium", auto_debug_mcp: false, model_fallbacks: [], agent_spawning_enabled: false,
      max_spawns_per_turn: 3, max_turn_minutes: 30, max_llm_calls_per_turn: 40, agents_can_call_external: false, max_external_calls_per_turn: 5, external_timeout_min: 10,
      sidebot_enabled: false, sidebot_provider: "", sidebot_model: "", sidebot_resolved: {} }),
    "GET /api/sandbox/status": () => jsonResponse({ docker: true, image: true, detail: "ready" }),
    "GET /api/acp/agents": () => jsonResponse([]),
    "GET /api/settings/registry": () => jsonResponse({ tabs: [], settings: [] }),
    "GET /api/providers/openai/models": () => models("openai"),
    "GET /api/providers/custom_gpu/models": () => models("custom_gpu"),
    "GET /api/providers/new_one/models": () => models("new_one"),
    "POST /api/providers/custom": () => { const p = { ...CUSTOM, id: "new_one", label: "Brand new" }; list = [...list, p]; return jsonResponse(p); },
    "DELETE /api/providers/custom_gpu": () => { list = list.filter((p) => p.id !== "custom_gpu"); return jsonResponse({ ok: true }); },
  });
}

beforeEach(() => { ["openai", "custom_gpu", "new_one"].forEach(forgetModels); window.location.hash = ""; });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Settings > Model providers: custom endpoints", () => {
  it("marks custom endpoints in the list and shows their own card, not the built-in one", async () => {
    setup();
    render(<SettingsPage />);
    const row = await screen.findByText("Office GPU");
    expect(row.parentElement).toHaveTextContent("custom · $1.00");
    await userEvent.click(row);
    expect(await screen.findByTestId("custom-provider-card")).toBeInTheDocument();
    expect(screen.getByText(/Showing the 1 model\(s\) you typed in — couldn't list live: ConnectError: refused/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("OpenAI"));
    await waitFor(() => expect(screen.queryByTestId("custom-provider-card")).toBeNull());
  });

  it("adds an endpoint from the list button, then selects it", async () => {
    const net = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "+ Add OpenAI-compatible endpoint" }));
    expect(await screen.findByTestId("custom-endpoint-form")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/^Sign-in/), "none");
    await userEvent.type(screen.getByLabelText(/^Name/), "Brand new");
    await userEvent.type(screen.getByLabelText(/^Address/), "http://10.0.0.9:8000");
    await userEvent.type(screen.getByLabelText(/^Default model/), "m");
    await userEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(screen.queryByTestId("custom-endpoint-form")).toBeNull());
    expect(net.calls.some((c) => c.method === "POST" && c.url.pathname === "/api/providers/custom")).toBe(true);
    expect(await screen.findByTestId("custom-provider-card")).toHaveTextContent("Brand new");
  });

  it("closes the form when another provider is picked or it is cancelled", async () => {
    setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "+ Add OpenAI-compatible endpoint" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("custom-endpoint-form")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "+ Add OpenAI-compatible endpoint" }));
    await userEvent.click(within(document.querySelector(".list") as HTMLElement).getByText("OpenAI"));
    expect(screen.queryByTestId("custom-endpoint-form")).toBeNull();
  });

  it("returns to another provider after an endpoint is deleted", async () => {
    setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SettingsPage />);
    await userEvent.click(await screen.findByText("Office GPU"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Office GPU")).toBeNull());
    expect((await screen.findAllByText("OpenAI")).length).toBeGreaterThan(0);
  });
});
