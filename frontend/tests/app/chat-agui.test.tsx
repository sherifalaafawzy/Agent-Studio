import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
}));

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const rich = { id: "rich", name: "Rich", tier: "pro", description: "", sub_agents: [], can_spawn: false, agui: { components: true, endpoint: true } };
const plain = { id: "plain", name: "Plain", tier: "pro", description: "", sub_agents: [], can_spawn: false, agui: { components: false, endpoint: true } };
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const reply = "Here you go\n\n```chart\n{\"type\": \"bar\", \"title\": \"Sales\", \"data\": [{\"label\": \"a\", \"value\": 1}]}\n```\n";

function setup() {
  routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic]),
    "GET /api/agents": () => jsonResponse([rich, plain]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, models: [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }], cached: false }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff),
    "POST /api/chat/stream": () => sseResponse([
      frame({ type: "session", session_id: "s1", seq: 0 }),
      frame({ type: "ui", spec: { type: "card.alert", title: "Heads up", text: "hello" }, seq: 1 }),
      frame({ type: "done", reply, model_calls: [], usage: {}, steps: [], runs: [], warnings: [] }),
    ]),
  });
}

beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear();
  forgetModels("anthropic");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});

describe("an agent with AG-UI components switched off", () => {
  it("shows a rich block of its answer as plain code and drops component events", async () => {
    setup();
    render(<Page />);
    await screen.findByRole("option", { name: "Plain" });
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "plain");
    await userEvent.type(screen.getByLabelText("Message"), "chart it{Enter}");
    expect(await screen.findByText("Here you go")).toBeInTheDocument();
    expect(document.querySelector("pre code")?.textContent).toContain("\"type\": \"bar\"");     // the fence is ordinary code, nothing crashed
    expect(document.querySelector("figure.ui-frame")).toBeNull();
    expect(screen.queryByText("Heads up")).not.toBeInTheDocument();
  });

  it("still shows components for an agent that has them on", async () => {
    setup();
    render(<Page />);
    await screen.findByRole("option", { name: "Rich" });
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "rich");
    await userEvent.type(screen.getByLabelText("Message"), "chart it{Enter}");
    expect(await screen.findByText("Here you go")).toBeInTheDocument();
    expect((await screen.findAllByText("Heads up")).length).toBeGreaterThan(0);
  });
});
