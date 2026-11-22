import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice, stopSpeaking } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
}));

type Routes = Parameters<typeof routeFetch>[0];

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const openai = { id: "openai", kind: "openai", label: "OpenAI", enabled: true };
const engineer = { id: "engineer", name: "Engineer", tier: "pro", description: "Writes code", sub_agents: ["reviewer"], can_spawn: false };
const models = [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }];
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const voiceOn = { ...voiceOff, configured: true, key_source: "store", key_hint: "…abcd", voice_id: "v1", tts_model: "m1", stt_model: "s1" };
const ctxInfo = { model: "claude-x", window_tokens: 200000, used_tokens: 1200, percent: 0.6, level: "ok", source: "Live",
  messages: 4, summarized: false, summarized_messages: 0, messages_after_summary: 4 };
const oneCall = { agent: "Engineer", provider: "anthropic", model: "claude-x", cost_usd: 0.0012, input_tokens: 100, output_tokens: 20 };
const doneEv = (reply: string, extra: object = {}) => ({ type: "done", reply, model_calls: [], usage: {}, steps: [], runs: [], ...extra });

function base(extra: Routes = {}) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic, openai]),
    "GET /api/agents": () => jsonResponse([engineer]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, models, cached: false }),
    "GET /api/providers/openai/models": () => jsonResponse({ provider: "openai", source: "catalog", error: null, cached: false, models: [{ id: "gpt-y", label: "GPT Y", input_per_m: null, output_per_m: null }] }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff),
    "GET /api/chat/sessions/s1/context": () => jsonResponse(ctxInfo),
    "GET /api/chat/sessions/s7/context": () => jsonResponse(ctxInfo),
    ...extra,
  });
}

const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const streamOf = (...events: object[]) => sseResponse(events.map(frame));

function live(first: object[] = []) {
  const enc = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctrl = c; for (const e of first) c.enqueue(enc.encode(frame(e))); } });
  const safe = (fn: () => void) => { try { fn(); } catch { /* already closed or errored */ } };
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (e: object) => safe(() => ctrl.enqueue(enc.encode(frame(e)))),
    close: () => safe(() => ctrl.close()),
    fail: (err: Error) => safe(() => ctrl.error(err)),
  };
}
type Live = ReturnType<typeof live>;
const watchAbort = (init: RequestInit | undefined, s: Live) =>
  init?.signal?.addEventListener("abort", () => s.fail(Object.assign(new Error("aborted"), { name: "AbortError" })));

const textbox = () => screen.getByLabelText("Message") as HTMLTextAreaElement;
const say = (text: string) => userEvent.type(textbox(), `${text}{Enter}`);
const streamCalls = (calls: { method: string; url: URL }[]) => calls.filter((c) => c.method === "POST" && c.url.pathname === "/api/chat/stream");
const session = (messages: object[], extra: object = {}) => ({
  session: { id: "s1", title: "Chat", provider: "anthropic", ...extra }, messages, active_job: null,
});

beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear();
  forgetModels("anthropic"); forgetModels("openai");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});

describe("chat page: restored history variants", () => {
  it("shows singular/plural tool counts, thought process, sub-agent counts, a bare summary and a no-answer turn without usage", async () => {
    nav.search = "s=s1";
    const run = (id: string, spawned = false) => ({ run_id: id, parent: id === "root" ? null : "root", agent: id, spawned, task: "t", status: "done", steps: [], texts: [], calls: [], cost_usd: 0 });
    base({ "GET /api/chat/sessions/s1": () => jsonResponse(session([
      { role: "user", text: "q1" },
      { role: "bot", text: "A1", meta: { thinking: "hmm", steps: [
        { agent: "Engineer", type: "call", tool: "grep", args: "a" }, { agent: "Engineer", type: "result", tool: "grep", result: "found" },
        { agent: "Engineer", type: "call", tool: "ls", args: "." }], runs: [run("root"), run("h1"), run("h2")] } },
      { role: "summary", text: "Earlier stuff" },
      { role: "bot", text: "", meta: { no_answer: true } },
    ], { title: "" })) });
    render(<Page />);
    expect(await screen.findByText("A1")).toBeInTheDocument();
    expect(screen.getByText("New chat", { selector: ".cv-title" })).toBeInTheDocument();
    expect(screen.getByText(/Used 2 tools/)).toBeInTheDocument();
    expect(screen.getByText("Thought process")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /2 sub-agents$/ })).toBeInTheDocument();
    expect(screen.getByText("Context summarised")).toBeInTheDocument();
    expect(screen.getByText("The model finished without an answer")).toBeInTheDocument();
    expect(screen.getAllByText(/^0 calls · 0→0 tok/).length).toBeGreaterThanOrEqual(1);
  });

  it("a saved error with no user message before it cannot be retried or switched", async () => {
    nav.search = "s=s1";
    const { calls } = base({ "GET /api/chat/sessions/s1": () => jsonResponse(session([
      { role: "bot", text: "", meta: { error: { title: "Boom", retryable: true, suggestions: [{ provider: "openai", label: "OpenAI", model: "gpt-y" }] } } },
    ])) });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await userEvent.click(screen.getByRole("button", { name: "OpenAI · gpt-y" }));
    expect(screen.getByText("Boom")).toBeInTheDocument();
    expect(streamCalls(calls)).toHaveLength(0);
  });

  it("keeps the chat's own provider when the provider list arrives after the chat opened", async () => {
    nav.search = "s=s1";
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    base({
      "GET /api/providers": async () => { await gate; return jsonResponse([anthropic, openai]); },
      "GET /api/chat/sessions/s1": () => jsonResponse(session([{ role: "user", text: "hi" }, { role: "bot", text: "hello back" }], { provider: "openai" })),
    });
    render(<Page />);
    expect(await screen.findByText("hello back")).toBeInTheDocument();
    release();
    await waitFor(() => expect(modelSelectEl()).not.toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    expect((document.querySelector(".opts-pop select") as HTMLSelectElement).value).toBe("openai");
  });
});
const modelSelectEl = () => document.querySelector("select.pill-select-inner") as HTMLSelectElement | null;

describe("chat page: stream events edge cases", () => {
  it("ignores events for runs it never saw, adopts only the first session id, and does not reload after a clean end", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf(
      { type: "session", session_id: "s7" }, { type: "session", session_id: "s8" },
      { type: "tool_call", run_id: "ghost", agent: "g", tool: "x", args: "" }, { type: "tool_result", run_id: "ghost", agent: "g", tool: "x", result: "r" },
      { type: "agent_text", run_id: "ghost", text: "boo" }, { type: "external_text", run_id: "ghost", delta: "boo" },
      { type: "model_call", run_id: "ghost", cost_usd: 1 }, { type: "agent_end", run_id: "ghost", status: "done", result: "", cost_usd: 1, ended: 1 },
      { type: "end", status: "done" },
      doneEv("Hi there", { model_calls: [oneCall], usage: { input_tokens: 5, output_tokens: 2, cost_usd: 0.01 }, billing: { spent_usd: 1.5, cap_usd: 10 }, runs: undefined }),
    ) });
    render(<Page />);
    await say("hello");
    expect(await screen.findByText("Hi there")).toBeInTheDocument();
    expect(screen.getByText(/1 call · 5→2 tok .* of .* cap used/)).toBeInTheDocument();
    expect(nav.replace).toHaveBeenCalledTimes(1);
    expect(nav.replace).toHaveBeenCalledWith("/?s=s7", { scroll: false });
    expect(screen.queryByText(/boo/)).toBeNull();
    await new Promise((r) => setTimeout(r, 400));
    expect(calls.filter((c) => c.url.pathname === "/api/chat/sessions/s7")).toHaveLength(0);
  });

  it("events that arrive after the answer is finished do not alter it, and a steer then opens a fresh bubble", async () => {
    base({ "POST /api/chat/stream": () => streamOf(
      doneEv("Final answer"),
      { type: "thinking", delta: "late thought" }, { type: "text", delta: " LATE" },
      { type: "steered" },
    ) });
    render(<Page />);
    await say("go");
    expect(await screen.findByText("Final answer")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".thinking")).not.toBeNull());
    expect(screen.queryByText(/LATE/)).toBeNull();
    expect(screen.queryByText(/Thought/)).toBeNull();
  });

  it("a model switch without model names shows just the providers", async () => {
    base({ "POST /api/chat/stream": () => streamOf(
      { type: "fallback", from: { provider: "openai" }, to: { provider: "anthropic" }, reason: "rate limited" }, doneEv("ok")) });
    render(<Page />);
    await say("go");
    expect(await screen.findByText("Switched model: openai → anthropic · rate limited")).toBeInTheDocument();
  });

  it("an error event with only a message shows it and offers no retry", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "error", message: "Provider exploded" }) });
    render(<Page />);
    await say("go");
    expect(await screen.findByText("Provider exploded")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("a non-clean end reloads the saved chat after a moment", async () => {
    const { calls } = base({
      "GET /api/chat/sessions/s7": () => jsonResponse(session([{ role: "user", text: "go" }, { role: "bot", text: "saved partial" }])),
      "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s7" }, { type: "end", status: "cancelled" }, doneEv("x")),
    });
    render(<Page />);
    await say("go");
    expect(await screen.findByText("saved partial")).toBeInTheDocument();
    expect(calls.some((c) => c.url.pathname === "/api/chat/sessions/s7")).toBe(true);
  });

  it("shows the time spent thinking once the answer starts, keeping it as more text arrives", async () => {
    {
      let now = 1_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => now);
      const s = live();
      base({ "POST /api/chat/stream": () => s.response });
      render(<Page />);
      await say("think");
      s.push({ type: "thinking", delta: "pon" });
      s.push({ type: "thinking", delta: "dering" });
      await new Promise((r) => setTimeout(r, 100));
      expect(await screen.findByText("Thinking…")).toBeInTheDocument();
      now += 5000;
      s.push({ type: "text", delta: "A" });
      s.push({ type: "text", delta: "B" });
      expect(await screen.findByText("AB")).toBeInTheDocument();
      expect(screen.getByText(/Thought for 5/)).toBeInTheDocument();
      s.push(doneEv("AB"));
      s.close();
      await waitFor(() => expect(screen.getByText("Copy")).toBeInTheDocument());
      expect(screen.getByText(/Thought for 5/)).toBeInTheDocument();
    }
  });

  it("Stop before the job id with nothing streamed finishes the bubble as Stopped.", async () => {
    const s = live();
    base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; } });
    render(<Page />);
    await say("go");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Stopped.")).toBeInTheDocument();
  });

  it("Stop after the answer already arrived leaves it untouched", async () => {
    const s = live([doneEv("Done early")]);
    base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; } });
    render(<Page />);
    await say("go");
    expect(await screen.findByText("Done early")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    expect(screen.getByText("Done early")).toBeInTheDocument();
    expect(screen.queryByText("Stopped.")).toBeNull();
  });

  it("names a sub-agent that is working before it has called any tool", async () => {
    const s = live([
      { type: "agent_start", run_id: "root", agent: "Engineer", task: "t", spawned: false, parent: null },
      { type: "agent_start", run_id: "sub", agent: "helper", task: "t2", spawned: true, parent: "root" },
    ]);
    base({ "POST /api/chat/stream": () => s.response });
    render(<Page />);
    await say("go");
    expect(await screen.findByText(/helper is working/)).toBeInTheDocument();
    s.push(doneEv("fin")); s.close();
    expect(await screen.findByText("fin")).toBeInTheDocument();
  });
});

describe("chat page: agent requests", () => {
  it("renders each kind of request with or without label, input and options", async () => {
    const s = live([
      { type: "job", job_id: "j1" },
      { type: "agent_start", run_id: "root", agent: "Engineer", task: "t", spawned: false, parent: null },
      { type: "tool_call", run_id: "root", agent: "Engineer", tool: "grep_repo", args: "needle" }, { type: "tool_result", run_id: "root", agent: "Engineer", tool: "grep_repo", result: "3 hits" },
      { type: "external_text", run_id: "root", delta: "streamed" }, { type: "external_text", run_id: "root", delta: " more" },
      { type: "external_ask", request_id: "r1", ask: "permission", agent: "claude", input: "ls -la" },
      { type: "external_ask", request_id: "r2", ask: "question", agent: "claude", label: "Planner", input: { a: 1 } },
      { type: "external_ask", request_id: "r3", ask: "question", agent: "claude", questions: [{ id: "q1", text: "bare" }] },
    ]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response, "POST /api/acp/respond/r1": () => jsonResponse({}) });
    render(<Page />);
    await say("go");
    expect(await screen.findByText("1 run")).toBeInTheDocument();
    expect(await screen.findByText("asks permission")).toBeInTheDocument();
    expect(screen.getByText("ls -la")).toBeInTheDocument();
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
    expect(screen.getByText("Planner")).toBeInTheDocument();
    expect(screen.getAllByText("claude").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: "Skip" })).toHaveLength(2);
    expect(await screen.findAllByText("streamed more")).not.toHaveLength(0);
    expect((await screen.findAllByText(/grep_repo/)).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("asks permission")).toBeNull());
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/r1")?.body).toEqual({ cancel: true });
    s.push(doneEv("fin")); s.close();
    await screen.findByText("fin");
  });
});

describe("chat page: notes and composer controls", () => {
  it("truncates a long note chip, toggles Queue/Steer back and forth, and retracts locally after the run was detached", async () => {
    const s = live([{ type: "job", job_id: "j1" }]);
    const { calls } = base({
      "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; },
      "POST /api/chat/jobs/j1/notes": () => jsonResponse({ id: "n1" }),
    });
    const { rerender } = render(<Page />);
    await say("go");
    const long = "x".repeat(120);
    await userEvent.click(await screen.findByRole("button", { name: "Queue" }));
    expect(screen.getByRole("button", { name: "Steer now" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Steer now" }));
    expect(screen.getByRole("button", { name: "Queue" })).toBeInTheDocument();
    await userEvent.click(textbox());
    await userEvent.paste(long);
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText(new RegExp(`^${"x".repeat(90)}…$`))).toBeInTheDocument();

    nav.search = "new=1";
    rerender(<Page />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Retract note" }));
    expect(screen.queryByRole("button", { name: "Retract note" })).toBeNull();
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  });

  it("lists agents without sub-agents plainly and marks agents that can build agents", async () => {
    base({ "GET /api/agents": () => jsonResponse([
      { id: "solo", name: "Solo", tier: "flash", description: "", sub_agents: [] },
      { id: "maker", name: "Maker", tier: "pro", description: "", sub_agents: [], can_spawn: true }]) });
    render(<Page />);
    const agent = screen.getByLabelText("Agent");
    expect(await within(agent).findByRole("option", { name: "Solo" })).toBeInTheDocument();
    expect(within(agent).getByRole("option", { name: "Maker · builds agents" })).toBeInTheDocument();
  });

  it("dictation appends to what is already typed", async () => {
    class FakeRecorder {
      mimeType = ""; ondataavailable: ((e: { data: Blob }) => void) | null = null; onstop: (() => void) | null = null;
      start = vi.fn();
      stop = vi.fn(() => { this.ondataavailable?.({ data: new Blob(["a"]) }); this.onstop?.(); });
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) }, configurable: true });
    base({ "GET /api/voice/settings": () => jsonResponse({ ...voiceOn, dictation: true }), "POST /api/voice/stt": () => jsonResponse({ text: "and more" }) });
    await loadVoice(true);
    render(<Page />);
    await userEvent.type(textbox(), "first part   ");
    await userEvent.click(await screen.findByTitle("Dictate (ElevenLabs)"));
    await userEvent.click(await screen.findByTitle("Stop and transcribe"));
    await waitFor(() => expect(textbox()).toHaveValue("first part and more"));
  });
});

describe("chat page: read aloud", () => {
  class FakeAudio {
    onended: (() => void) | null = null; pause = vi.fn(); play = vi.fn(() => Promise.resolve());
    constructor(public src: string) {}
  }
  const audio = () => new Response(new Blob(["mp3"]), { status: 200 });
  beforeEach(() => {
    vi.stubGlobal("Audio", FakeAudio);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() }));
  });

  it("reads a fresh answer aloud when auto-read is on, and offers Listen only on answers with text", async () => {
    const { calls } = base({
      "GET /api/voice/settings": () => jsonResponse({ ...voiceOn, auto_read: true }),
      "POST /api/voice/tts": () => audio(),
      "POST /api/chat/stream": () => streamOf(doneEv("Spoken answer")),
    });
    await loadVoice(true);
    render(<Page />);
    await say("speak");
    expect(await screen.findByText("Spoken answer")).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/voice/tts")).toHaveLength(1));
    expect(calls.find((c) => c.url.pathname === "/api/voice/tts")?.body).toEqual({ text: "Spoken answer" });
    stopSpeaking();
  });

  it("does not auto-read restored answers, hides Listen without text or voice id", async () => {
    nav.search = "s=s1";
    const { calls } = base({
      "GET /api/voice/settings": () => jsonResponse({ ...voiceOn, auto_read: true }),
      "GET /api/chat/sessions/s1": () => jsonResponse(session([{ role: "user", text: "q" }, { role: "bot", text: "old answer" }, { role: "bot", text: "" }])),
    });
    await loadVoice(true);
    render(<Page />);
    expect(await screen.findByText("old answer")).toBeInTheDocument();
    await screen.findAllByText("Copy");
    expect(await screen.findAllByRole("button", { name: /Listen/ })).toHaveLength(1);
    expect(calls.filter((c) => c.url.pathname === "/api/voice/tts")).toHaveLength(0);
  });

  it("shows no Listen button when voice is configured without a voice id", async () => {
    nav.search = "s=s1";
    base({
      "GET /api/voice/settings": () => jsonResponse({ ...voiceOn, voice_id: "" }),
      "GET /api/chat/sessions/s1": () => jsonResponse(session([{ role: "user", text: "q" }, { role: "bot", text: "an answer" }])),
    });
    await loadVoice(true);
    render(<Page />);
    await screen.findByText("an answer");
    await screen.findAllByText("Copy");
    expect(screen.queryByRole("button", { name: /Listen/ })).toBeNull();
  });
});

describe("chat page: greeting hours", () => {
  it.each([[3, "Burning the midnight oil?"], [9, "Good morning"], [14, "Good afternoon"], [20, "Good evening"]])("at %i:00 says %s", (hour, text) => {
    base();
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date(2026, 10, 21, hour, 0));
      render(<Page />);
      expect(screen.getByRole("heading", { name: new RegExp(text.replace("?", "\\?")) })).toBeInTheDocument();
    } finally { vi.useRealTimers(); }
  });
});
