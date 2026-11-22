import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/settings/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/settings/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const provider = { id: "openai", kind: "openai", label: "OpenAI", enabled: true, has_api_key: true, api_key_hint: null, api_base: null, vertex_project: null,
  vertex_location: null, vertex_credentials_path: null, default_model: "gpt-x", pro_model: null, flash_model: null, cap_usd: 5, cap_period: "monthly", max_output_tokens: 4096,
  catalog: { label: "OpenAI", needs_key: true, note: "", default_model: "gpt-x", auth_modes: ["api_key"] }, auth_mode: "api_key", auth_modes: [{ id: "api_key", label: "API key" }],
  api_key_env: null, key_env_default: "OPENAI_API_KEY", has_service_account_json: false, service_account_email: null, extra_header_names: [], organization: null, auth_ready: true, auth_detail: "" };

function routes() {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([provider]),
    "GET /api/settings": () => jsonResponse({ sidebot_enabled: false, sidebot_provider: "", sidebot_model: "", sidebot_resolved: { provider: null, model: null }, model_fallbacks: [] }),
    "GET /api/sandbox/status": () => jsonResponse({ docker: true, image: true, detail: "ok" }),
    "GET /api/acp/agents": () => jsonResponse([]),
    "GET /api/auto": () => jsonResponse({ enabled: true, engine: "rules", preference: "balanced", allow: [], deny: [], fallback: true, timeout_s: 4, tier_models: { flash: "", pro: "" },
      engines: ["rules"], preferences: ["balanced"], available: true, reason: "", worker: null, engine_ready: {} }),
    "GET /api/d1/models": () => jsonResponse({ default: "d1-2b", installing: null, models: {} }),
  });
}

afterEach(() => { window.history.replaceState(null, "", "/"); });

describe("settings page: the Auto tab", () => {
  it("opens from the tab bar", async () => {
    routes();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Auto$/ }));
    expect(await screen.findByText("Auto mode")).toBeInTheDocument();
    expect(window.location.hash).toBe("#auto");
    expect(document.querySelector(".page-sub")).toHaveTextContent(/Pick Auto in the chat/);
  });

  it("opens straight from the #auto link in the chat's options", async () => {
    window.history.replaceState(null, "", "/settings/#auto");
    routes();
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("Auto mode")).toBeInTheDocument());
  });
});
