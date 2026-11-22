import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse, type Call } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
}));

type Routes = Parameters<typeof routeFetch>[0];

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const AUTO_ON = { enabled: true, available: true, reason: "", engine: "rules", preference: "balanced" };
const ROUTE = {
  provider: "anthropic", model: "claude-haiku-5-5", tier: "flash", engine: "rules", reason: "built-in rules read this as simple: the fast tier.", confidence: 0.6,
  signals: { complexity: "simple", code: false, reasoning: false, tools: false, vision: false, est_tokens: 12 },
  price: { input_per_m: 1, output_per_m: 5, known: true }, alternates: [{ provider: "anthropic", model: "claude-opus-5-5", tier: "pro" }], skipped: [],
};
const call = { agent: "plain", provider: "anthropic", model: "claude-haiku-5-5", cost_usd: 0.0001, input_tokens: 10, output_tokens: 5 };
const done = (extra: object = {}) => ({ type: "done", reply: "Hello there", model_calls: [call], usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001 }, steps: [], runs: [], warnings: [], route: ROUTE, ...extra });
const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const streamOf = (...events: object[]) => sseResponse(events.map(frame));

function base(extra: Routes = {}, auto: Routes[string] | null = () => jsonResponse(AUTO_ON)) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic]),
    "GET /api/agents": () => jsonResponse([]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, cached: false, models: [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }] }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff),
    ...(auto ? { "GET /api/auto": auto } : {}),
    ...extra,
  });
}
const streams = (calls: Call[]) => calls.filter((c) => c.method === "POST" && c.url.pathname === "/api/chat/stream");
const textbox = () => screen.getByLabelText("Message") as HTMLTextAreaElement;
const say = (t: string) => userEvent.type(textbox(), `${t}{Enter}`);
const modelSelect = () => document.querySelector("select.pill-select-inner") as HTMLSelectElement | null;
const providerSelect = () => document.querySelector(".opts-pop select") as HTMLSelectElement;

beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear();
  forgetModels("anthropic");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});

describe("chat page: Auto mode", () => {
  it("starts on Auto when it can work, with no model picker", async () => {
    base();
    render(<Page />);
    expect(await screen.findByRole("button", { name: "Auto mode" })).toHaveTextContent("Auto");
    expect(modelSelect()).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Auto mode" }));
    expect(providerSelect()).toHaveValue("auto");
    expect(within(providerSelect()).getByRole("option", { name: "Auto — picks the model for each message" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Change how it chooses" })).toHaveAttribute("href", expect.stringMatching(/\/settings\/?#auto/));
  });

  it("sends 'auto' with no model, shows the route live and under the answer", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf({ type: "route", ...ROUTE, seq: 0 }, { type: "session", session_id: "s1", seq: 1 }, { type: "text", delta: "Hello there", seq: 2 }, done()) });
    render(<Page />);
    await screen.findByRole("button", { name: "Auto mode" });
    await say("Hi");
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(streams(calls)[0].body).toEqual({ provider: "auto", message: "Hi" });
    expect(screen.getByRole("button", { name: "Auto mode" })).toHaveTextContent("Auto · claude-haiku-5-5");     // the pill names the model it chose
    const note = document.querySelector(".auto-note")!;
    expect(within(note as HTMLElement).getByText("claude-haiku-5-5")).toBeInTheDocument();
    expect(screen.getByText("built-in rules read this as simple: the fast tier.")).toBeInTheDocument();
    expect(screen.getByText(/Next best if it fails: anthropic\/claude-opus-5-5/)).toBeInTheDocument();
  });

  it("does not send a model chosen earlier for another provider", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf(done({ route: undefined })) });
    render(<Page />);
    await screen.findByRole("button", { name: "Auto mode" });
    await userEvent.click(screen.getByRole("button", { name: "Auto mode" }));
    await userEvent.selectOptions(providerSelect(), "anthropic");
    await screen.findByRole("option", { name: "Claude X (claude-x)" });
    await userEvent.selectOptions(modelSelect()!, "claude-x");
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));                    // choosing closed the popover; open it again
    await userEvent.selectOptions(providerSelect(), "auto");
    await say("Hi");
    await waitFor(() => expect(streams(calls)).toHaveLength(1));
    expect(streams(calls)[0].body).toEqual({ provider: "auto", message: "Hi" });
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(document.querySelector(".auto-note")).toBeNull();                                                      // no route came back: no note
  });

  it("goes back to a pinned provider and model, and to Auto again", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf(done({ route: undefined })) });
    render(<Page />);
    await screen.findByRole("button", { name: "Auto mode" });
    await userEvent.click(screen.getByRole("button", { name: "Auto mode" }));
    await userEvent.selectOptions(providerSelect(), "anthropic");
    expect(screen.queryByRole("button", { name: "Auto mode" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Change how it chooses" })).toBeNull();
    await screen.findByRole("option", { name: "Claude X (claude-x)" });
    await say("Hi");
    await waitFor(() => expect(streams(calls)).toHaveLength(1));
    expect(streams(calls)[0].body).toEqual({ provider: "anthropic", message: "Hi" });
  });

  it("opens the provider popover from the Auto pill", async () => {
    base();
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Auto mode" }));
    expect(document.querySelector(".opts-pop")).not.toBeNull();
  });

  it("is not offered when it cannot work, and the first provider is used instead", async () => {
    base({}, () => jsonResponse({ ...AUTO_ON, available: false, reason: "No model provider is ready for Auto." }));
    render(<Page />);
    await waitFor(() => expect(modelSelect()).not.toBeNull());
    expect(await within(modelSelect()!).findByRole("option", { name: "Claude X (claude-x)" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Auto mode" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    expect(within(providerSelect()).queryByRole("option", { name: /Auto/ })).toBeNull();
  });

  it("works as before when the Auto endpoint is missing", async () => {
    base({}, null);
    render(<Page />);
    await waitFor(() => expect(modelSelect()).not.toBeNull());
    expect(screen.queryByRole("button", { name: "Auto mode" })).toBeNull();
  });

  it("shows a refusal from Auto like any other error", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "error", title: "Auto could not choose a model", message: "Auto could not find a model that can take this message - openai: over budget.",
      hint: "Auto could not find a model that can take this message - openai: over budget.", raw: "", code: "auto_unavailable", retryable: false, suggestions: [] }) });
    render(<Page />);
    await screen.findByRole("button", { name: "Auto mode" });
    await say("Hi");
    expect(await screen.findByText("Auto could not choose a model")).toBeInTheDocument();
    expect(screen.getByText(/openai: over budget/)).toBeInTheDocument();
  });

  it("reopens a chat that used Auto on Auto, with each answer's route", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier", provider: "anthropic", model: "claude-haiku-5-5" }, messages: [
      { role: "user", text: "hello" }, { role: "bot", text: "Earlier answer", meta: { calls: [call], usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001 }, route: ROUTE } }], active_job: null }) });
    render(<Page />);
    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Auto mode" })).toHaveTextContent("Auto");
    expect(screen.getByText("built-in rules read this as simple: the fast tier.")).toBeInTheDocument();
  });

  it("reopens a chat that used Auto on its real provider when Auto is no longer available", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier", provider: "anthropic" }, messages: [
      { role: "user", text: "hello" }, { role: "bot", text: "Earlier answer", meta: { calls: [], route: ROUTE } }], active_job: null }) },
      () => jsonResponse({ ...AUTO_ON, available: false }));
    render(<Page />);
    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Auto mode" })).toBeNull();
    expect(modelSelect()).not.toBeNull();
    expect(screen.getByText("built-in rules read this as simple: the fast tier.")).toBeInTheDocument();             // the note still explains the old choice
  });

  it("reopens a chat that never used Auto on its own provider even when Auto is available", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier", provider: "anthropic" }, messages: [
      { role: "user", text: "hello" }, { role: "bot", text: "Earlier answer", meta: { calls: [] } }], active_job: null }) });
    render(<Page />);
    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Auto mode" })).toBeNull();
  });

  it("reopens a chat that used Auto on its real provider when the Auto endpoint cannot be reached", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier", provider: "anthropic" }, messages: [
      { role: "user", text: "hello" }, { role: "bot", text: "Earlier answer", meta: { calls: [], route: ROUTE } }], active_job: null }) }, null);
    render(<Page />);
    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Auto mode" })).toBeNull();
  });

  it("ignores a route that arrives when no answer is being written", async () => {
    base({ "POST /api/chat/stream": () => streamOf(done({ route: undefined }), { type: "route", ...ROUTE }) });
    render(<Page />);
    await screen.findByRole("button", { name: "Auto mode" });
    await say("Hi");
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(document.querySelector(".auto-note")).toBeNull();
  });

  it("forgets the model Auto chose when a new chat starts", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "route", ...ROUTE, seq: 0 }, done()) });
    const { rerender } = render(<Page />);
    await screen.findByRole("button", { name: "Auto mode" });
    await say("Hi");
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    nav.search = "new=1";
    rerender(<Page />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Auto mode" })).toHaveTextContent(/^Auto$/));
  });
});
