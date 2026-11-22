import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/app/settings/page";
import { dirtyIds, setDirty } from "@/lib/dirty";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

const HS = { sandbox_network: false, workspace_dir: "H:/work", thinking_level: "medium", auto_debug_mcp: false, model_fallbacks: [], agent_spawning_enabled: true,
  max_spawns_per_turn: 9, max_turn_minutes: 0, max_llm_calls_per_turn: 40, agents_can_call_external: true, max_external_calls_per_turn: 6, external_timeout_min: 120,
  sidebot_enabled: true, sidebot_provider: "", sidebot_model: "", sidebot_resolved: { provider: null, model: null } };
const entry = (o: Record<string, unknown>) => ({ key: "k", label: "K", tab: "harness", kind: "int", effect: "", help: "", where: "", internal: false, secret: false, min: 1, max: 20,
  choices: [], max_len: null, default: 4, value: 4, changed: false, field: "", ...o });

function setup(over: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([]), "GET /api/settings": () => jsonResponse(HS), "GET /api/sandbox/status": () => jsonResponse({ docker: true, image: true, detail: "ok" }),
    "GET /api/acp/agents": () => jsonResponse([]),
    "GET /api/settings/registry": () => jsonResponse({ tabs: [], settings: [entry({ key: "max_spawns_per_turn", label: "Max agent-created sub-agents per turn", effect: "stops runaway agent creation", value: 9, changed: true })] }),
    "GET /api/settings/history": () => jsonResponse([]),
    "POST /api/settings/reset": () => jsonResponse({ reset: ["max_spawns_per_turn"] }),
    "GET /api/memory/config": () => jsonResponse({}),
    ...over,
  });
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); for (const id of dirtyIds()) setDirty(id, false); window.location.hash = ""; });

describe("Settings page: audit features", () => {
  it("searches every setting and opens the right tab with the setting highlighted", async () => {
    setup();
    render(<SettingsPage />);
    await userEvent.type(await screen.findByLabelText("Search settings"), "runaway");
    const hit = await screen.findByRole("option", { name: /Max agent-created sub-agents per turn/ });
    await userEvent.click(hit);
    await waitFor(() => expect(document.querySelector('[data-setting="max_spawns_per_turn"]')).not.toBeNull());
    await waitFor(() => expect(document.querySelector('[data-setting="max_spawns_per_turn"]')!.classList.contains("flash")).toBe(true));
    expect(window.location.hash).toBe("#harness");
  });

  it("offers a reset link for a changed setting, and resets it through the API", async () => {
    const net = setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Reset to default \(4\)/ }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "POST" && c.url.pathname === "/api/settings/reset")).toBe(true));
  });

  it("asks before leaving a tab that has unsaved edits, and stays when you say no", async () => {
    setup();
    const ask = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Agents & sandbox/ }));
    setDirty("some-form", true);
    await userEvent.click(screen.getByRole("button", { name: /Voice/ }));
    expect(ask).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe("#harness");
    await userEvent.click(screen.getByRole("button", { name: /Backup & history/ }));        // second time the answer is yes
    expect(window.location.hash).toBe("#backup");
    expect(await screen.findByRole("button", { name: "Download settings file" })).toBeInTheDocument();
  });

  it("jumping from search also asks before leaving unsaved edits", async () => {
    setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<SettingsPage />);
    await screen.findByRole("button", { name: /Agents & sandbox/ });
    setDirty("some-form", true);
    await userEvent.type(screen.getByLabelText("Search settings"), "runaway");
    await userEvent.click(await screen.findByRole("option"));
    expect(window.location.hash).not.toBe("#harness");
  });

  it("has a Memory tab", async () => {
    setup();
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Memory/ }));
    expect(window.location.hash).toBe("#memory");
  });

  it("has an Artifacts tab with the storage and rules", async () => {
    setup({ "GET /api/artifacts/config": () => jsonResponse({ enabled: true, capture: "new", max_file_mb: 25, max_per_turn: 50, secret_scan: "redact", retention_days: 0, ignore: [], storages: [], policy: [],
      default_storages: ["local"], capture_modes: { new: "New files only" }, scan_modes: { redact: "Remove" }, types: [], default_ignore: [], kinds: [] }) });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Artifacts/ }));
    expect(window.location.hash).toBe("#artifacts");
    expect(await screen.findByText(/A copy always stays on this PC/)).toBeInTheDocument();
  });

  it("has a Runtimes tab and a Data sources tab", async () => {
    setup({
      "GET /api/runtimes": () => jsonResponse({ targets: [{ id: "local", label: "This PC", docs: "", remote: false, cost_note: "", platform: "", side_effects: [], fields: [], enabled: true, values: {}, secrets_set: [], configured: true, problem: "" }], image_template: "", model_env_set: [] }),
      "GET /api/data/sources": () => jsonResponse({ sources: [], kinds: [{ kind: "sqlite", label: "SQLite file", description: "d", docs: "", fields: [], note: "", managed_mcp: null }] }),
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /^Runtimes/ }));
    expect(window.location.hash).toBe("#runtimes");
    expect(await screen.findByText(/Where an agent runs/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Data sources/ }));
    expect(window.location.hash).toBe("#data");
    expect(await screen.findByTestId("data-sources")).toBeInTheDocument();
  });

  it("reloads the lists after a backup import or reset", async () => {
    const net = setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Backup & history/ }));
    const before = net.calls.filter((c) => c.url.pathname === "/api/settings" && c.method === "GET").length;
    await userEvent.click(await screen.findByRole("button", { name: "Reset all settings to defaults" }));
    await waitFor(() => expect(net.calls.filter((c) => c.url.pathname === "/api/settings" && c.method === "GET").length).toBeGreaterThan(before));
  });
});
