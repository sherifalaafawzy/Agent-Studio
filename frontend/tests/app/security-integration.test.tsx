// Security wired into the rest of the app: the agent editor, the chat answer, the Settings tab and the sidebar.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import Page from "@/app/page";
import SettingsPage from "@/app/settings/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push, replace: nav.replace }) }));

const engineer = { id: "engineer", name: "Engineer", description: "", instruction: "", provider: null, tier: "pro", model: null, tools: [], mcp: [], skills: [], sub_agents: [],
  builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask", guardrails: [], identity: {} };
const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const call = { agent: "plain", provider: "anthropic", model: "claude-x", cost_usd: 0.0001, input_tokens: 10, output_tokens: 5 };
const done = (extra: object = {}) => ({ type: "done", reply: "Hello there", model_calls: [call], usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001 }, steps: [], runs: [], warnings: [], ...extra });
const GUARD = { stage: "input", agent: "plain", profile: "basic", profile_name: "Basic rules", action: "blocked", reason: "contains an email address" };

function chatNet(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic]), "GET /api/agents": () => jsonResponse([]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, cached: false, models: [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }] }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff), "GET /api/auto": () => jsonResponse({ enabled: false, available: false, reason: "off", engine: "rules", preference: "balanced" }), ...extra });
}
beforeEach(async () => {
  nav.search = "";
  forgetModels("anthropic");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});

describe("agent editor: guardrails and identity", () => {
  it("sends the agent's guardrail choice and identity with the agent", async () => {
    const net = routeFetch({ "GET /api/agents": () => jsonResponse([engineer]), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]),
      "GET /api/acp/agents": () => jsonResponse([]), "GET /api/rag/kbs": () => jsonResponse([]), "GET /api/guardrails": () => jsonResponse({ profiles: [{ id: "basic", name: "Basic rules", mode: "monitor" }] }),
      "GET /api/identity/config": () => jsonResponse({ trust_domain: "agent-studio.local", scopes: { "files:read": "read" } }), "PUT /api/agents/engineer": () => jsonResponse({ ok: true }) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    expect(await screen.findByText("spiffe://agent-studio.local/agent/engineer")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /None/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /work it out from its tools/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "files:read" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(net.calls.find((c) => c.method === "PUT")!.body).toMatchObject({ guardrails: ["none"], identity: { scopes: ["files:read"] } });
  });

  it("starts a new agent with the defaults", async () => {
    const net = routeFetch({ "GET /api/agents": () => jsonResponse([engineer]), "GET /api/tools": () => jsonResponse([]), "GET /api/mcp": () => jsonResponse([]), "GET /api/skills": () => jsonResponse([]),
      "GET /api/acp/agents": () => jsonResponse([]), "GET /api/rag/kbs": () => jsonResponse([]), "GET /api/guardrails": () => jsonResponse({ profiles: [] }), "GET /api/identity/config": () => jsonResponse({ trust_domain: "x.org", scopes: {} }),
      "POST /api/agents": () => jsonResponse({ ok: true }) });
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: /advanced editor|Advanced/i }));
    expect(await screen.findByText("spiffe://x.org/agent/(new agent)")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^Id/), "newbie");
    await userEvent.type(screen.getByLabelText("Name"), "Newbie");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "POST")).toBe(true));
    expect(net.calls.find((c) => c.method === "POST" && c.url.pathname === "/api/agents")!.body).toMatchObject({ id: "newbie", guardrails: [], identity: {} });
  });
});

describe("chat: guardrail notes", () => {
  const say = (t: string) => userEvent.type(screen.getByLabelText("Message"), `${t}{Enter}`);
  it("shows a block live under the answer and keeps it in the final reply", async () => {
    chatNet({ "POST /api/chat/stream": () => sseResponse([frame({ type: "session", session_id: "s1" }), frame({ type: "guardrail", ...GUARD }), frame(done({ guardrails: [GUARD] }))]) });
    render(<Page />);
    await say("my email is a@b.co");
    const btn = await screen.findByRole("button", { name: /Guardrails: 1 blocked/ });
    await userEvent.click(btn);
    expect(screen.getByText(/contains an email address/)).toBeInTheDocument();
  });
  it("keeps the live note when the final reply carries none", async () => {
    chatNet({ "POST /api/chat/stream": () => sseResponse([frame({ type: "session", session_id: "s1" }), frame({ type: "text", delta: "Hi " }), frame({ type: "guardrail", ...GUARD, action: "flagged" }), frame(done())]) });
    render(<Page />);
    await say("hello");
    expect(await screen.findByRole("button", { name: /Guardrails: 1 flagged/ })).toBeInTheDocument();
  });
  it("shows the note again when an old chat is reopened", async () => {
    nav.search = "s=old";
    chatNet({ "GET /api/chat/sessions/old": () => jsonResponse({ session: { id: "old", title: "Old", provider: "anthropic", agent: null, model: "claude-x", workspace: "" },
      messages: [{ role: "user", text: "hi" }, { role: "bot", text: "I stopped here", meta: { status: "ok", guardrails: [GUARD] } }], active_job: null }) });
    render(<Page />);
    expect(await screen.findByRole("button", { name: /Guardrails: 1 blocked/ })).toBeInTheDocument();
  });
});

describe("settings: the Security tab", () => {
  it("shows guardrails and identity under their own tab", async () => {
    routeFetch({ "GET /api/providers": () => jsonResponse([]), "GET /api/settings": () => jsonResponse({ workspace_dir: "", sandbox_network: false, sidebot_enabled: true, sidebot_resolved: {} }), "GET /api/sandbox/status": () => jsonResponse({}),
      "GET /api/acp/agents": () => jsonResponse([]), "GET /api/settings/registry": () => jsonResponse({ tabs: [], settings: [] }),
      "GET /api/guardrails": () => jsonResponse({ enabled: false, profiles: [], kinds: [{ id: "rules", label: "Built-in rules", summary: "s", where: "w", leaves_pc: false, cost: "free", fields: [] }] }),
      "GET /api/guardrails/events": () => jsonResponse({ events: [] }), "GET /api/guardrails/stats": () => jsonResponse({ days: 7, flagged: 0, blocked: 0, errors: 0, top_categories: [] }),
      "GET /api/identity/config": () => jsonResponse({ trust_domain: "t.org", issuer: "http://x", ttl_s: 300, enforce: false, active: "local", provider: { kind: "local", params: {}, secrets_set: [] }, providers: [{ id: "local", label: "Built-in issuer", summary: "s", exchange: "rfc8693", fields: [] }], scopes: {} }),
      "GET /api/identity/agents": () => jsonResponse({ agents: [] }), "GET /api/identity/events": () => jsonResponse({ events: [] }) });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Security" }));
    expect(await screen.findByText("Guardrails")).toBeInTheDocument();
    expect(await screen.findByText("Agent identity")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Security" })).toHaveAttribute("href", "/security/");
  });
});
