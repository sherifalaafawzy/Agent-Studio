import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push, replace: nav.replace }) }));

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const call = { agent: "plain", provider: "anthropic", model: "claude-x", cost_usd: 0.0001, input_tokens: 10, output_tokens: 5 };
const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const streamOf = (...events: object[]) => sseResponse(events.map(frame));
const done = (extra: object = {}) => ({ type: "done", reply: "Hello there", model_calls: [call], usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001 }, steps: [], runs: [], warnings: [], ...extra });

function base(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic]), "GET /api/agents": () => jsonResponse([]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, cached: false, models: [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }] }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff), "GET /api/auto": () => jsonResponse({ enabled: false, available: false, reason: "off", engine: "rules", preference: "balanced" }),
    ...extra,
  });
}
const say = (t: string) => userEvent.type(screen.getByLabelText("Message"), `${t}{Enter}`);

beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear();
  forgetModels("anthropic");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});

describe("chat page: memory", () => {
  it("shows what the agent recalled and remembered under its answer, live and in the final reply", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "Hello " },
      { type: "memory", kind: "recalled", memory_type: "semantic", scope: "user", text: "The billing system is Xero" },
      done({ memory: [{ kind: "recalled", type: "semantic", scope: "user", text: "The billing system is Xero" }, { kind: "saved", type: "semantic", text: "Deploys are on Fridays" }] })) });
    render(<Page />);
    await say("Which billing system do we use?");
    const btn = await screen.findByRole("button", { name: /Memory: recalled 1 · remembered 1/ });
    await userEvent.click(btn);
    expect(screen.getByText(/The billing system is Xero/)).toBeInTheDocument();
    expect(screen.getByText(/Deploys are on Fridays/)).toBeInTheDocument();
  });

  it("keeps the live memory notes when the final reply carries none", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "Hi " },
      { type: "memory", kind: "recalled", memory_type: "semantic", text: "Live one" }, { type: "memory", kind: "forgot", memory_type: "semantic", text: "Old one" }, done()) });
    render(<Page />);
    await say("hello");
    expect(await screen.findByRole("button", { name: /Memory: recalled 1 · forgot 1/ })).toBeInTheDocument();
  });

  it("shows the memory notes again when an earlier chat is reopened", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier chat", provider: "anthropic" }, messages: [
      { role: "user", text: "Earlier question" }, { role: "bot", text: "Earlier answer", meta: { calls: [call], usage: { input_tokens: 5, output_tokens: 1, cost_usd: 0 }, memory: [{ kind: "pending", type: "semantic", text: "Timezone is Paris" }] } }] }),
      "GET /api/chat/sessions/s1/context": () => jsonResponse({ model: "claude-x", window_tokens: 1000, used_tokens: 10, percent: 1, level: "ok", source: "estimate", messages: 2, summarized: false, summarized_messages: 0 }) });
    render(<Page />);
    expect(await screen.findByRole("button", { name: /Memory: waiting for your review 1/ })).toBeInTheDocument();
  });
});
