import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse, textResponse, type Call } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
}));

type Routes = Parameters<typeof routeFetch>[0];

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const openai = { id: "openai", kind: "openai", label: "OpenAI", enabled: true };
const gemini = { id: "gemini", kind: "gemini", label: "Gemini", enabled: false };
const engineer = { id: "engineer", name: "Engineer", tier: "pro", description: "Writes code", sub_agents: ["reviewer"], can_spawn: false };
const models = [
  { id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 },
  { id: "claude-y", label: "claude-y", input_per_m: null, output_per_m: null },
];
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const ctxInfo = { model: "claude-x", window_tokens: 200000, used_tokens: 1200, percent: 0.6, level: "ok", source: "Live",
  messages: 4, summarized: false, summarized_messages: 0, messages_after_summary: 4 };
const oneCall = { agent: "Engineer", provider: "anthropic", model: "claude-x", cost_usd: 0.0012, input_tokens: 100, output_tokens: 20 };

/** Default routes for every endpoint the page touches on load. Tests add their own on top. */
function base(extra: Routes = {}) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic, openai, gemini]),
    "GET /api/agents": () => jsonResponse([engineer]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, models, cached: false }),
    "GET /api/providers/openai/models": () => jsonResponse({ provider: "openai", source: "catalog", error: "no key", cached: false, models: [{ id: "gpt-y", label: "GPT Y", input_per_m: null, output_per_m: null }] }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff),
    "GET /api/chat/sessions/s1/context": () => jsonResponse(ctxInfo),
    ...extra,
  });
}

const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const streamOf = (...events: object[]) => sseResponse(events.map(frame));

/** A stream that stays open until the test closes it, so the page stays busy. */
function live(first: object[] = []) {
  const enc = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctrl = c; for (const e of first) c.enqueue(enc.encode(frame(e))); } });
  // a stream the page has already ended (or a test already closed) must not throw from cleanup
  const safe = (fn: () => void) => { try { fn(); } catch { /* already closed or errored */ } };
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (e: object) => safe(() => ctrl.enqueue(enc.encode(frame(e)))),
    close: () => safe(() => ctrl.close()),
    fail: (err: Error) => safe(() => ctrl.error(err)),
  };
}
type Live = ReturnType<typeof live>;
/** Makes a live stream honour the fetch abort signal, as a real fetch would. */
const watchAbort = (init: RequestInit | undefined, s: Live) =>
  init?.signal?.addEventListener("abort", () => s.fail(Object.assign(new Error("aborted"), { name: "AbortError" })));

const posts = (calls: Call[], path: string) => calls.filter((c) => c.method === "POST" && c.url.pathname === path);
const streams = (calls: Call[]) => posts(calls, "/api/chat/stream");
const textbox = () => screen.getByLabelText("Message") as HTMLTextAreaElement;
/** Types a message and presses Enter, which sends it. */
const say = (text: string) => userEvent.type(textbox(), `${text}{Enter}`);
const modelSelect = () => document.querySelector("select.pill-select-inner") as HTMLSelectElement;

// Every test starts from the same URL and module caches; a leftover ?s= from one test would reopen a chat in the next.
beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear();
  forgetModels("anthropic"); forgetModels("openai");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);   // Voice keeps a module-level cache: reset it so each test starts with voice off
});

describe("chat page: first load", () => {
  it("loads providers and agents into the composer selectors", async () => {
    const { calls } = base();
    render(<Page />);
    const agent = screen.getByLabelText("Agent");
    expect(await within(agent).findByRole("option", { name: "Engineer · 1 sub" })).toBeInTheDocument();
    expect(within(agent).getByRole("option", { name: "Plain chat" })).toBeInTheDocument();
    expect(await within(modelSelect()).findByRole("option", { name: "Claude X (claude-x)" })).toBeInTheDocument();
    expect(within(modelSelect()).getByRole("option", { name: "Anthropic · default" })).toBeInTheDocument();
    expect(calls.map((c) => c.url.pathname)).toEqual(expect.arrayContaining(["/api/providers", "/api/agents", "/api/providers/anthropic/models"]));
  });

  it("lists a disabled provider as off in the options popover", async () => {
    base();
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    const provider = document.querySelector(".opts-pop select") as HTMLSelectElement;
    expect(within(provider).getByRole("option", { name: "Anthropic (Claude)" })).toBeEnabled();
    expect(within(provider).getByRole("option", { name: "Gemini — off" })).toBeDisabled();
  });

  it("asks for a provider key when none is enabled", async () => {
    const { calls } = base({ "GET /api/providers": () => jsonResponse([gemini]) });
    render(<Page />);
    expect(await screen.findByText(/No model provider is enabled yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "add a key in Settings" })).toHaveAttribute("href", expect.stringMatching(/\/settings\/?#providers/));
    expect(modelSelect()).toBeNull();
    await userEvent.type(textbox(), "hello");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(streams(calls)).toHaveLength(0);
  });

  // BUG (documented, not yet fixed): the Send button is disabled without an enabled provider, but Enter
  // still calls send(), so the request goes out with an empty provider. it.fails keeps the suite green
  // and flips to a failure once the bug is fixed.
  it("Enter does not send when no provider is enabled", async () => {
    const { calls } = base({ "GET /api/providers": () => jsonResponse([gemini]) });
    render(<Page />);
    await screen.findByText(/No model provider is enabled yet/);
    await say("hello");
    await new Promise((r) => setTimeout(r, 30));
    expect(streams(calls)).toHaveLength(0);
  });

  it("shows the backend-offline pill when the providers request fails", async () => {
    base({ "GET /api/providers": () => textResponse("down", 500) });
    render(<Page />);
    expect(await screen.findByText("the server is not answering")).toBeInTheDocument();
  });

  it("a suggestion fills the composer and focuses it", async () => {
    base();
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Find and fix a failing test" }));
    expect(textbox()).toHaveValue("Find and fix a failing test");
    expect(textbox()).toHaveFocus();
  });

  it("greets by the time of day", () => {
    base();
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date(2026, 10, 21, 9, 0));
      render(<Page />);
      expect(screen.getByRole("heading", { name: /Good morning/ })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("switching provider in options reloads its models and sends that provider", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf({ type: "done", reply: "ok", model_calls: [], usage: {}, steps: [], runs: [] }) });
    render(<Page />);
    await screen.findByRole("option", { name: "Claude X (claude-x)" });
    await userEvent.selectOptions(modelSelect(), "claude-x");
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    await userEvent.selectOptions(document.querySelector(".opts-pop select") as HTMLSelectElement, "openai");
    expect(await within(modelSelect()).findByRole("option", { name: "GPT Y (gpt-y)" })).toBeInTheDocument();
    expect(calls.some((c) => c.url.pathname === "/api/providers/openai/models")).toBe(true);
    await say("hi");
    await waitFor(() => expect(streams(calls)).toHaveLength(1));
    // the model choice belongs to the old provider, so it is cleared
    expect(streams(calls)[0].body).toEqual({ provider: "openai", message: "hi" });
  });
});

describe("chat page: sending and streaming", () => {
  it("streams a reply and sends the message to the stream endpoint", async () => {
    const { calls } = base({
      "POST /api/chat/stream": () => streamOf(
        { type: "session", session_id: "s1", seq: 0 },
        { type: "job", job_id: "j1", seq: 1 },
        { type: "text", delta: "Hello ", seq: 2 },
        { type: "text", delta: "there", seq: 3 },
        { type: "done", reply: "Hello there", model_calls: [oneCall], usage: { input_tokens: 100, output_tokens: 20, cost_usd: 0.0012 }, steps: [], runs: [], warnings: [] },
      ),
    });
    render(<Page />);
    await say("Hi there");
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(screen.getByText("Hi there", { selector: ".bubble" })).toBeInTheDocument();
    expect(screen.getByText(/claude-x · 1 call · 100→20 tok/)).toBeInTheDocument();
    expect(textbox()).toHaveValue("");
    expect(streams(calls)[0].body).toEqual({ provider: "anthropic", message: "Hi there" });
  });

  it("sends the chosen agent, model and workspace", async () => {
    const { calls } = base({
      "POST /api/fs/pick-folder": () => jsonResponse({ path: "H:/work", cancelled: false }),
      "POST /api/chat/stream": () => streamOf({ type: "done", reply: "planned", model_calls: [], usage: {}, steps: [], runs: [] }),
    });
    render(<Page />);
    await screen.findByRole("option", { name: "Engineer · 1 sub" });
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "engineer");
    await screen.findByRole("option", { name: "Claude X (claude-x)" });
    await userEvent.selectOptions(modelSelect(), "claude-x");
    await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Browse…" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Workspace" })).toHaveTextContent("work"));      // the popover closed; the pill names the folder
    await say("Plan it");
    await waitFor(() => expect(streams(calls)).toHaveLength(1));
    expect(streams(calls)[0].body).toEqual({ provider: "anthropic", agent: "engineer", model: "claude-x", workspace: "H:/work", message: "Plan it" });
  });

  it("opens the chat under ?s= with the session id and announces it to the sidebar", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1", seq: 0 }, { type: "done", reply: "yo", model_calls: [], usage: {}, steps: [], runs: [] }) });
    const announced = vi.fn();
    window.addEventListener("as:sessions", announced);
    try {
      render(<Page />);
      await say("first");
      await screen.findByText("yo");
      expect(nav.replace).toHaveBeenCalledWith("/?s=s1", { scroll: false });
      expect(announced).toHaveBeenCalled();
      // the Inspect link appears once the chat has a session and no run is active
      expect(screen.getByRole("link", { name: /Inspect/ })).toHaveAttribute("href", expect.stringMatching(/\/runs\/?\?s=s1$/));
    } finally {
      window.removeEventListener("as:sessions", announced);
    }
  });

  it("continues the same session on the next message", async () => {
    let n = 0;
    const { calls } = base({
      "POST /api/chat/stream": () => (n++ === 0
        ? streamOf({ type: "session", session_id: "s1", seq: 0 }, { type: "done", reply: "one", model_calls: [], usage: {}, steps: [], runs: [] })
        : streamOf({ type: "done", reply: "two", model_calls: [], usage: {}, steps: [], runs: [] })),
    });
    render(<Page />);
    await say("first");
    await screen.findByText("one");
    await say("second");
    await screen.findByText("two");
    expect(streams(calls).map((c) => c.body)).toEqual([
      { provider: "anthropic", message: "first" },
      { provider: "anthropic", message: "second", session_id: "s1" },
    ]);
  });

  it("Enter sends, Shift+Enter adds a line, and a blank message is not sent", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf({ type: "done", reply: "ok", model_calls: [], usage: {}, steps: [], runs: [] }) });
    render(<Page />);
    await userEvent.type(textbox(), "   {Enter}");
    expect(streams(calls)).toHaveLength(0);
    await userEvent.type(textbox(), "line one{Shift>}{Enter}{/Shift}line two");
    expect(textbox()).toHaveValue("   line one\nline two");
    expect(streams(calls)).toHaveLength(0);
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(streams(calls)).toHaveLength(1));
    expect(streams(calls)[0].body.message).toBe("line one\nline two");
  });

  it("shows the tools used and the model's thinking on the answer", async () => {
    base({
      "POST /api/chat/stream": () => streamOf(
        { type: "thinking", delta: "Plan: read the file first", seq: 0 },
        { type: "done", reply: "Done.", thinking: "Plan: read the file first", model_calls: [], usage: {}, runs: [], steps: [
          { agent: "Engineer", type: "call", tool: "read_file", args: '{"path":"a.ts"}' },
          { agent: "Engineer", type: "result", tool: "read_file", result: "12 lines" },
        ] },
      ),
    });
    render(<Page />);
    await say("read it");
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    expect(screen.getByText(/Used 1 tool/)).toBeInTheDocument();
    expect(screen.getByText(/Engineer → read_file/)).toBeInTheDocument();
    expect(screen.getByText("↳ 12 lines")).toBeInTheDocument();
    // the label is "Thought process" when no time was measured (same millisecond), else "Thought for …"
    expect(screen.getByText(/Thought (process|for)/)).toBeInTheDocument();
    expect(screen.getByText("Plan: read the file first")).toBeInTheDocument();
  });

  it("shows warnings from the run under the answer", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "done", reply: "answer", warnings: ["Context almost full"], model_calls: [], usage: {}, steps: [], runs: [] }) });
    render(<Page />);
    await say("q");
    expect(await screen.findByText("⚠ Context almost full")).toBeInTheDocument();
  });

  it("shows live activity and a stop button while the run is going", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "agent_start", run_id: "root", agent: "Engineer", task: "fix", spawned: false, parent: null, seq: 1 },
      { type: "tool_call", run_id: "root", agent: "Engineer", tool: "read_file", args: "a.ts", seq: 2 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; },
      "POST /api/chat/jobs/j1/cancel": () => jsonResponse({ ok: true }),
      "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Fix", provider: "anthropic" }, messages: [
        { role: "user", text: "fix it" }, { role: "bot", text: "partial answer", meta: { status: "cancelled", calls: [] } }], active_job: null }) });
    render(<Page />);
    await say("fix it");
    expect(await screen.findByText(/Engineer → read_file…/)).toBeInTheDocument();
    expect(textbox()).toHaveAttribute("placeholder", "Add a note for the agent — it reads it during the run");
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(posts(calls, "/api/chat/jobs/j1/cancel")).toHaveLength(1);
    s.push({ type: "session", session_id: "s1", seq: 3 });
    s.push({ type: "end", status: "cancelled", seq: 4 });
    s.close();
    // the run ended without a done event: the chat is reloaded from what was saved
    expect(await screen.findByText("partial answer")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  });

  // BUG (documented): Stop before the job id has arrived aborts the request, but the streaming
  // bubble is never finalised, so the "Thinking" line keeps spinning.
  it("Stop before the job id arrives leaves the answer spinning", async () => {
    const s = live([{ type: "text", delta: "Starting", seq: 0 }]);
    base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; } });
    render(<Page />);
    await say("go");
    await screen.findByText("Starting");
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    expect(document.querySelector(".thinking")).toBeNull();
  });

  it("a run that is still starting refuses a second message with a toast and keeps the text", async () => {
    const toasts: { text: string; tone: string }[] = [];
    const on = (e: Event) => toasts.push((e as CustomEvent).detail);
    window.addEventListener("as:toast", on);
    const s = live([]);
    base({ "POST /api/chat/stream": () => s.response });
    try {
      render(<Page />);
      await say("first");
      await userEvent.type(textbox(), "second{Enter}");
      await waitFor(() => expect(toasts).toContainEqual({ text: "The run is still starting - try again in a moment", tone: "warn" }));
      expect(textbox()).toHaveValue("second");
    } finally {
      window.removeEventListener("as:toast", on);
      s.close();
    }
  });

  it("a message sent while running is queued as a note, then shown when the agent reads it", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "agent_start", run_id: "root", agent: "Engineer", task: "fix", spawned: false, parent: null, seq: 1 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response,
      "POST /api/chat/jobs/j1/notes": () => jsonResponse({ id: "n1" }) });
    render(<Page />);
    await say("fix it");
    await screen.findByRole("button", { name: "Stop" });
    await say("use the new API");
    expect(await screen.findByText(/⏳ queued/)).toBeInTheDocument();
    expect(posts(calls, "/api/chat/jobs/j1/notes")[0].body).toEqual({ text: "use the new API", mode: "queue" });
    s.push({ type: "note_retracted", note_id: "n1", seq: 3 });
    await waitFor(() => expect(screen.queryByText(/⏳ queued/)).toBeNull());
    await say("use the new API");
    expect(await screen.findByText(/⏳ queued/)).toBeInTheDocument();
    s.push({ type: "note_delivered", note_id: "n1", text: "use the new API", mode: "queue", seq: 2 });
    expect(await screen.findByText("your note · read at the next step")).toBeInTheDocument();
    expect(screen.queryByText(/⏳ queued/)).toBeNull();
    s.close();
  });

  it("Steer now sends the note in steer mode, and the interruption is shown", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "text", delta: "half a sentence", seq: 1 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response,
      "POST /api/chat/jobs/j1/notes": () => jsonResponse({ id: "n2" }) });
    render(<Page />);
    await say("go");
    await screen.findByText("half a sentence");
    await userEvent.click(screen.getByRole("button", { name: "Queue" }));
    expect(screen.getByRole("button", { name: "Steer now" })).toBeInTheDocument();
    await say("stop, wrong file");
    expect(await screen.findByText(/⚡ steer/)).toBeInTheDocument();
    expect(posts(calls, "/api/chat/jobs/j1/notes")[0].body).toEqual({ text: "stop, wrong file", mode: "steer" });
    s.push({ type: "note_delivered", note_id: "n2", text: "stop, wrong file", mode: "steer", seq: 2 });
    expect(await screen.findByText("your steer note · read now")).toBeInTheDocument();
    s.push({ type: "steered", seq: 3 });
    expect(await screen.findByText("interrupted by your steer note")).toBeInTheDocument();
    s.push({ type: "done", reply: "final answer", model_calls: [], usage: {}, steps: [], runs: [], seq: 4 });
    s.close();
    expect(await screen.findByText("final answer")).toBeInTheDocument();
  });

  it("retracting a queued note removes it, and a note the agent already read says so", async () => {
    const toasts: { text: string; tone: string }[] = [];
    const on = (e: Event) => toasts.push((e as CustomEvent).detail);
    window.addEventListener("as:toast", on);
    const s = live([{ type: "job", job_id: "j1", seq: 0 }]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response,
      "POST /api/chat/jobs/j1/notes": () => jsonResponse({ id: "n1" }),
      "DELETE /api/chat/jobs/j1/notes/n1": () => jsonResponse({ detail: "gone" }, { status: 409 }) });
    try {
      render(<Page />);
      await say("go");
      await screen.findByRole("button", { name: "Stop" });
      await say("a note");
      await screen.findByText(/⏳ queued/);
      await userEvent.click(screen.getByRole("button", { name: "Retract note" }));
      expect(screen.queryByText(/⏳ queued/)).toBeNull();
      expect(posts(calls, "/api/chat/jobs/j1/notes")).toHaveLength(1);
      expect(calls.some((c) => c.method === "DELETE" && c.url.pathname === "/api/chat/jobs/j1/notes/n1")).toBe(true);
      await waitFor(() => expect(toasts).toContainEqual({ text: "That note was already read by the agent", tone: "warn" }));
    } finally {
      window.removeEventListener("as:toast", on);
      s.close();
    }
  });

  it("a note the server refuses shows the server's reason as a toast", async () => {
    const toasts: { text: string; tone: string }[] = [];
    const on = (e: Event) => toasts.push((e as CustomEvent).detail);
    window.addEventListener("as:toast", on);
    const s = live([{ type: "job", job_id: "j1", seq: 0 }]);
    base({ "POST /api/chat/stream": () => s.response,
      "POST /api/chat/jobs/j1/notes": () => jsonResponse({ detail: "This run has finished" }, { status: 409 }) });
    try {
      render(<Page />);
      await say("go");
      await screen.findByRole("button", { name: "Stop" });
      await say("late note");
      await waitFor(() => expect(toasts).toContainEqual({ text: "This run has finished", tone: "warn" }));
    } finally {
      window.removeEventListener("as:toast", on);
      s.close();
    }
  });

  it("reconnects to the running job after the stream drops, resuming from the last event", async () => {
    let first = true;
    const enc = new TextEncoder();
    const { calls } = base({
      "POST /api/chat/stream": () => new Response(new ReadableStream<Uint8Array>({
        pull(c) {
          if (first) { first = false; c.enqueue(enc.encode(frame({ type: "job", job_id: "j1", seq: 0 }))); }
          else c.error(new TypeError("socket reset"));
        },
      }), { headers: { "Content-Type": "text/event-stream" } }),
      "GET /api/chat/jobs/j1/events": () => streamOf(
        { type: "text", delta: "resumed ", seq: 1 },
        { type: "done", reply: "resumed ok", model_calls: [], usage: {}, steps: [], runs: [], seq: 2 }),
    });
    render(<Page />);
    await say("long task");
    expect(await screen.findByText("resumed ok", undefined, { timeout: 4000 })).toBeInTheDocument();
    const resume = calls.find((c) => c.method === "GET" && c.url.pathname === "/api/chat/jobs/j1/events");
    expect(resume?.url.searchParams.get("after")).toBe("0");
  });

  it("a stream that fails before any job shows the network error in the answer", async () => {
    base({ "POST /api/chat/stream": () => { throw new TypeError("network down"); } });
    render(<Page />);
    await say("hello");
    expect(await screen.findByText("network down")).toBeInTheDocument();
    expect(screen.getByText("Stopped")).toBeInTheDocument();
  });

  it("a 402 from the stream shows the cap message", async () => {
    base({ "POST /api/chat/stream": () => jsonResponse({ detail: { message: "Monthly cap reached" } }, { status: 402 }) });
    render(<Page />);
    await say("hello");
    expect(await screen.findByText("Monthly cap reached")).toBeInTheDocument();
  });

  it("an HTTP error from the stream shows the server's detail", async () => {
    base({ "POST /api/chat/stream": () => jsonResponse({ detail: "upstream exploded" }, { status: 500 }) });
    render(<Page />);
    await say("hello");
    expect(await screen.findByText("upstream exploded")).toBeInTheDocument();
  });

  it("a budget refusal explains the projected cost and that nothing was sent", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "refused", detail: { message: "Over the monthly cap", projected_usd: 0.5, remaining_usd: 0.1 } }) });
    render(<Page />);
    await say("expensive");
    expect(await screen.findByText("Over the monthly cap")).toBeInTheDocument();
    expect(screen.getByText(/Projected \$0\.5000 · remaining \$0\.1000 · nothing was sent to the provider/)).toBeInTheDocument();
  });

  it("an error event shows its title, hint and details, and Try again resends the same message", async () => {
    let n = 0;
    const { calls } = base({ "POST /api/chat/stream": () => (n++ === 0
      ? streamOf({ type: "job", job_id: "j0", seq: 0 }, { type: "error", title: "Provider overloaded", hint: "Try again in a minute", raw: "HTTP 529 overloaded", retryable: true })
      : streamOf({ type: "done", reply: "recovered", model_calls: [], usage: {}, steps: [], runs: [] })) });
    render(<Page />);
    await say("do the thing");
    expect(await screen.findByText("Provider overloaded")).toBeInTheDocument();
    expect(screen.getByText("Try again in a minute")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("HTTP 529 overloaded")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    expect(streams(calls).map((c) => c.body.message)).toEqual(["do the thing", "do the thing"]);
    expect(screen.queryByText("Provider overloaded")).toBeNull();
  });

  it("a finished run with no reply says so and offers a retry", async () => {
    let n = 0;
    const { calls } = base({ "POST /api/chat/stream": () => (n++ === 0
      ? streamOf({ type: "done", reply: "  ", model_calls: [oneCall], usage: {}, steps: [], runs: [] })
      : streamOf({ type: "done", reply: "now answered", model_calls: [], usage: {}, steps: [], runs: [] })) });
    render(<Page />);
    await say("think");
    expect(await screen.findByText("The model finished without an answer")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("now answered")).toBeInTheDocument();
    expect(streams(calls)).toHaveLength(2);
  });

  it("a halted turn keeps the partial answer and says why it stopped", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "done", reply: "partial", halted: { message: "Budget hit" }, model_calls: [], usage: {}, steps: [], runs: [] }) });
    render(<Page />);
    await say("long");
    expect(await screen.findByText("partial")).toBeInTheDocument();
    expect(screen.getByText("Turn stopped early: Budget hit")).toBeInTheDocument();
    expect(screen.getByText("Partial progress above is real; nothing further was sent to any provider.")).toBeInTheDocument();
  });

  it("a model switch by fallback is shown in the transcript", async () => {
    base({ "POST /api/chat/stream": () => streamOf(
      { type: "job", job_id: "j1", seq: 0 },
      { type: "fallback", from: { provider: "anthropic", model: "claude-x" }, to: { provider: "openai", model: "gpt-y" }, reason: "rate limited", seq: 1 },
      { type: "done", reply: "answered", model_calls: [], usage: {}, steps: [], runs: [], seq: 2 },
    ) });
    render(<Page />);
    await say("hi");
    expect(await screen.findByText(/Switched model: anthropic\/claude-x → openai\/gpt-y · rate limited/)).toBeInTheDocument();
    expect(screen.getByText("answered")).toBeInTheDocument();
  });
});

describe("chat page: restoring and switching chats", () => {
  it("restores a saved chat from ?s= with its history, summary, handoff and error", async () => {
    nav.search = "s=s1";
    const { calls } = base({
      "GET /api/chat/sessions/s1": () => jsonResponse({
        session: { id: "s1", title: "Earlier chat", provider: "anthropic", agent: "engineer", model: "claude-x", workspace: "H:/proj" },
        messages: [
          { role: "user", text: "Earlier question" },
          { role: "bot", text: "Earlier answer", meta: { calls: [oneCall], usage: { input_tokens: 50, output_tokens: 9, cost_usd: 0.0005 }, steps: [{ agent: "Engineer", type: "call", tool: "list_dir", args: "." }] } },
          { role: "summary", text: "Summary of the first part", meta: { covered: 4 } },
          { role: "handoff", text: "Handed off to Reviewer" },
          { role: "user", text: "Still there?" },
          { role: "bot", text: "", meta: { error: { title: "Rate limited", hint: "Wait a bit", retryable: true, raw: "429" } } },
        ],
        active_job: null,
      }),
      "POST /api/chat/stream": () => streamOf({ type: "done", reply: "recovered", model_calls: [], usage: {}, steps: [], runs: [] }),
    });
    render(<Page />);
    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.getByText("Earlier chat", { selector: ".cv-title" })).toBeInTheDocument();
    expect(screen.getByText(/claude-x · 1 call · 50→9 tok/)).toBeInTheDocument();
    expect(screen.getByText(/Used 1 tool/)).toBeInTheDocument();
    expect(screen.getByText("Context summarised · 4 earlier messages condensed")).toBeInTheDocument();
    expect(screen.getByText("Handed off to Reviewer")).toBeInTheDocument();
    expect(screen.getByLabelText("Agent")).toHaveValue("engineer");

    await userEvent.click(screen.getByRole("button", { name: "Show the summary the model resumes from" }));
    expect(screen.getByText("Summary of the first part")).toBeInTheDocument();

    expect(screen.getByText("Rate limited")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    expect(streams(calls)[0].body).toEqual({ provider: "anthropic", agent: "engineer", model: "claude-x", workspace: "H:/proj", message: "Still there?", session_id: "s1" });
  });

  it("a chat that no longer exists falls back to a fresh chat", async () => {
    nav.search = "s=gone";
    base({ "GET /api/chat/sessions/gone": () => jsonResponse({ detail: "not found" }, { status: 404 }) });
    render(<Page />);
    await waitFor(() => expect(screen.getByLabelText("Message")).toHaveAttribute("placeholder", "What should we work on?"));
    expect(screen.queryByText(/Old answer/)).toBeNull();
  });

  // BUG (documented): openSession() sets the "no longer exists" note, then calls newChat(), which
  // clears the note again (page.tsx:130 setNote(""), reached from page.tsx:157), so the user never sees it.
  it("a missing chat shows 'That chat no longer exists.' before falling back", async () => {
    nav.search = "s=gone";
    base({ "GET /api/chat/sessions/gone": () => jsonResponse({ detail: "not found" }, { status: 404 }) });
    render(<Page />);
    expect(await screen.findByText("That chat no longer exists.", undefined, { timeout: 1000 })).toBeInTheDocument();
  });

  it("?new= clears the open chat", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Old", provider: "anthropic" },
      messages: [{ role: "user", text: "Old question" }, { role: "bot", text: "Old answer" }], active_job: null }) });
    const { rerender } = render(<Page />);
    expect(await screen.findByText("Old answer")).toBeInTheDocument();
    nav.search = "new=123";
    rerender(<Page />);
    await waitFor(() => expect(screen.queryByText("Old answer")).toBeNull());
    expect(screen.getByRole("heading", { name: /Good|Burning/ })).toBeInTheDocument();
  });

  it("opens a chat that is still running and follows it from the start", async () => {
    nav.search = "s=s9";
    const { calls } = base({
      "GET /api/chat/sessions/s9": () => jsonResponse({ session: { id: "s9", title: "Running", provider: "anthropic" },
        messages: [{ role: "user", text: "Long task" }], active_job: { job_id: "j9", started: 1700000000 } }),
      "GET /api/chat/jobs/j9/events": () => streamOf(
        { type: "agent_start", run_id: "root", agent: "Engineer", task: "long", spawned: false, parent: null, seq: 0 },
        { type: "text", delta: "Working on it", seq: 1 },
        { type: "done", reply: "All finished", model_calls: [], usage: {}, steps: [], runs: [], seq: 2 }),
    });
    render(<Page />);
    expect(await screen.findByText("All finished")).toBeInTheDocument();
    expect(screen.getByText("Long task", { selector: ".bubble" })).toBeInTheDocument();
    const replay = calls.find((c) => c.url.pathname === "/api/chat/jobs/j9/events");
    expect(replay?.url.searchParams.get("after")).toBe("-1");
  });

  it("a restored answer with copy writes it to the clipboard and confirms", async () => {
    nav.search = "s=s1";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "T", provider: "anthropic" },
        messages: [{ role: "user", text: "q" }, { role: "bot", text: "Copy me" }], active_job: null }) });
      render(<Page />);
      await screen.findByText("Copy me");
      await userEvent.click(screen.getByRole("button", { name: /Copy/ }));
      expect(writeText).toHaveBeenCalledWith("Copy me");
      expect(await screen.findByRole("button", { name: /Copied/ })).toBeInTheDocument();
    } finally {
      delete (navigator as any).clipboard;
    }
  });
});

describe("chat page: agent questions, permissions and runs", () => {
  it("answers a permission request with the chosen option, or cancels it", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "external_ask", request_id: "r1", ask: "permission", agent: "claude_code", label: "Claude Code", kind: "edit", title: "Edit src/app.ts", input: { path: "src/app.ts" },
        options: [{ id: "o-allow", name: "Allow once", kind: "allow_once" }, { id: "o-deny", name: "Deny", kind: "reject_once" }], seq: 1 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response,
      "POST /api/acp/respond/r1": () => jsonResponse({ ok: true }),
      "POST /api/acp/respond/r2": () => jsonResponse({ ok: true }) });
    render(<Page />);
    await say("edit");
    expect(await screen.findByText(/asks permission/)).toBeInTheDocument();
    expect(screen.getByText("Edit src/app.ts")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(screen.queryByText(/asks permission/)).toBeNull());
    expect(posts(calls, "/api/acp/respond/r1")[0].body).toEqual({ option_id: "o-allow" });

    s.push({ type: "external_ask", request_id: "r2", ask: "permission", agent: "claude_code", label: "Claude Code", options: [], seq: 2 });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(posts(calls, "/api/acp/respond/r2")[0].body).toEqual({ cancel: true });
    s.close();
  });

  it("answers a question with the chosen option, or skips it", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "external_ask", request_id: "q1", ask: "question", agent: "planner", label: "Planner",
        questions: [{ id: "qq1", options: [{ id: "opt-a", label: "Option A" }] }], seq: 1 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response, "POST /api/acp/respond/q1": () => jsonResponse({ ok: true }),
      "POST /api/acp/respond/q2": () => jsonResponse({ ok: true }) });
    render(<Page />);
    await say("plan");
    await userEvent.click(await screen.findByRole("button", { name: "Option A" }));
    expect(posts(calls, "/api/acp/respond/q1")[0].body).toEqual({ answers: [{ questionId: "qq1", selectedOptionIds: ["opt-a"] }] });
    s.push({ type: "external_ask", request_id: "q2", ask: "question", agent: "planner", label: "Planner", questions: [], seq: 2 });
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    expect(posts(calls, "/api/acp/respond/q2")[0].body).toEqual({ skip: true });
    s.close();
  });

  it("approves a proposed plan", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "external_ask", request_id: "p1", ask: "plan_approval", agent: "architect", label: "Architect", plan: "1. read\n2. write", seq: 1 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": () => s.response, "POST /api/acp/respond/p1": () => jsonResponse({ ok: true }) });
    render(<Page />);
    await say("design");
    expect(await screen.findByText(/proposes a plan/)).toBeInTheDocument();
    expect(screen.getByText(/1\. read/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve plan" }));
    await waitFor(() => expect(screen.queryByText(/proposes a plan/)).toBeNull());
    expect(posts(calls, "/api/acp/respond/p1")[0].body).toEqual({ accepted: true });
    s.close();
  });

  it("drops a permission request the agent withdrew", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "external_ask", request_id: "r9", ask: "permission", agent: "claude_code", label: "Claude Code", options: [], seq: 1 },
    ]);
    base({ "POST /api/chat/stream": () => s.response });
    render(<Page />);
    await say("go");
    expect(await screen.findByText(/asks permission/)).toBeInTheDocument();
    s.push({ type: "external_resolved", request_id: "r9", seq: 2 });
    await waitFor(() => expect(screen.queryByText(/asks permission/)).toBeNull());
    s.close();
  });

  it("a failed answer shows the server's reason as a note", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "external_ask", request_id: "r5", ask: "plan_approval", agent: "a", label: "A", plan: "x", seq: 1 },
    ]);
    base({ "POST /api/chat/stream": () => s.response,
      "POST /api/acp/respond/r5": () => jsonResponse({ detail: "No such request" }, { status: 404 }) });
    render(<Page />);
    await say("go");
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText("⚠ No such request")).toBeInTheDocument();
    s.close();
  });

  it("a sub-agent run opens the Agents panel, can be saved as an agent, and the panel closes", async () => {
    const runs = [
      { run_id: "root", parent: null, agent: "Engineer", spawned: false, task: "Fix", status: "done", steps: [], texts: [], calls: [], cost_usd: 0, started: 1, ended: 2, result: "ok" },
      { run_id: "sub-1", parent: "root", agent: "reviewer", spawned: true, task: "check the diff", status: "done", steps: [], texts: ["looks fine"], calls: [], cost_usd: 0, started: 1, ended: 2, result: "ok",
        definition: { name: "Diff Reviewer", tier: "flash", tools: [], mcp: [], instruction: "Review the diff" } },
    ];
    const { calls } = base({
      "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1", seq: 0 }, { type: "done", reply: "reviewed", model_calls: [], usage: {}, steps: [], runs }),
      "POST /api/agents": () => jsonResponse({ ok: true }),
    });
    vi.stubGlobal("prompt", vi.fn(() => "diff_reviewer"));
    render(<Page />);
    await say("review");
    await userEvent.click(await screen.findByRole("button", { name: /1 sub-agent \(agent-built\)/ }));
    expect(await screen.findByText("created by agent")).toBeInTheDocument();
    expect(screen.getByText("Click a lane to read that agent's thread.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open inspector/ })).toHaveAttribute("href", expect.stringMatching(/\/runs\/?\?s=/));
    expect(screen.getByText("looks fine")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save as agent" }));
    expect(await screen.findByText("Saved agent “diff_reviewer” — it's now in Agent Studio.")).toBeInTheDocument();
    const saved = posts(calls, "/api/agents")[0].body;
    expect(saved).toMatchObject({ id: "diff_reviewer", name: "Diff Reviewer", instruction: "Review the diff", tier: "flash", tools: [], mcp: [] });

    await userEvent.click(screen.getByTitle("Close"));
    expect(screen.queryByText("Click a lane to read that agent's thread.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Agents · 1/ }));
    expect(screen.getByText("Click a lane to read that agent's thread.")).toBeInTheDocument();
  });

  it("cancelling the save prompt saves nothing, and a refused save shows the reason", async () => {
    const runs = [
      { run_id: "root", parent: null, agent: "Engineer", spawned: false, task: "Fix", status: "done", steps: [], texts: [], calls: [], cost_usd: 0, started: 1, ended: 2, result: "ok" },
      { run_id: "sub-2", parent: "root", agent: "helper", spawned: true, task: "help", status: "done", steps: [], texts: [], calls: [], cost_usd: 0, started: 1, ended: 2, result: "ok" },
    ];
    const { calls } = base({
      "POST /api/chat/stream": () => streamOf({ type: "done", reply: "helped", model_calls: [], usage: {}, steps: [], runs }),
      "POST /api/agents": () => jsonResponse({ detail: "id already exists" }, { status: 409 }),
    });
    const prompt = vi.fn().mockReturnValueOnce(null).mockReturnValueOnce("helper");
    vi.stubGlobal("prompt", prompt);
    render(<Page />);
    await say("help");
    await userEvent.click(await screen.findByRole("button", { name: /sub-agent/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Save as agent" }));
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(posts(calls, "/api/agents")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Save as agent" }));
    expect(await screen.findByText("⚠ id already exists")).toBeInTheDocument();
  });
});

describe("chat page: context meter and summary", () => {
  async function chatWithSession() {
    const reload = vi.fn(() => jsonResponse({ session: { id: "s1", title: "Chat", provider: "anthropic" },
      messages: [{ role: "user", text: "q" }, { role: "bot", text: "a" }], active_job: null }));
    const r = base({
      "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1", seq: 0 }, { type: "done", reply: "the answer", model_calls: [], usage: {}, steps: [], runs: [] }),
      "GET /api/chat/sessions/s1": reload,
    });
    render(<Page />);
    await say("q");
    await screen.findByText("the answer");
    return { r, reload };
  }

  it("summarising the context reports the result as a toast and reloads the chat", async () => {
    const toasts: { text: string; tone: string }[] = [];
    const on = (e: Event) => toasts.push((e as CustomEvent).detail);
    window.addEventListener("as:toast", on);
    try {
      const { reload } = await chatWithSession();
      await userEvent.click(await screen.findByRole("button", { name: "Context window usage" }));
      const summarize = vi.fn(() => jsonResponse({ covered_messages: 4, cost_usd: 0.002 }));
      // a fresh router for the summary and the reload that follows it
      routeFetch({
        "POST /api/chat/sessions/s1/summarize": summarize,
        "GET /api/chat/sessions/s1": reload,
        "GET /api/chat/sessions/s1/context": () => jsonResponse(ctxInfo),
      });
      await userEvent.click(screen.getByRole("button", { name: "Summarize context" }));
      await waitFor(() => expect(toasts).toContainEqual({
        text: "Summarised 4 messages ($0.00200). The chat now continues from the summary.", tone: "ok",
      }));
      expect(summarize).toHaveBeenCalledTimes(1);
      expect(reload).toHaveBeenCalled();
    } finally {
      window.removeEventListener("as:toast", on);
    }
  });

  it("a failed summary shows the server's reason as an error toast", async () => {
    const toasts: { text: string; tone: string }[] = [];
    const on = (e: Event) => toasts.push((e as CustomEvent).detail);
    window.addEventListener("as:toast", on);
    try {
      await chatWithSession();
      await userEvent.click(await screen.findByRole("button", { name: "Context window usage" }));
      routeFetch({
        "POST /api/chat/sessions/s1/summarize": () => jsonResponse({ detail: "Nothing new to summarise" }, { status: 409 }),
        "GET /api/chat/sessions/s1/context": () => jsonResponse(ctxInfo),
      });
      await userEvent.click(screen.getByRole("button", { name: "Summarize context" }));
      await waitFor(() => expect(toasts).toContainEqual({ text: "Nothing new to summarise", tone: "err" }));
    } finally {
      window.removeEventListener("as:toast", on);
    }
  });
});

describe("chat page: timing", () => {
  it("after 20 seconds of running the page says the run keeps going if the tab closes", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
    const s = live([{ type: "job", job_id: "j1", seq: 0 }]);
    try {
      base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; } });
      render(<Page />);
      await act(async () => { await vi.advanceTimersByTimeAsync(10); });   // let the providers load: Enter needs an enabled one
      const box = textbox();
      fireEvent.change(box, { target: { value: "long one" } });
      fireEvent.keyDown(box, { key: "Enter" });
      await act(async () => { await vi.advanceTimersByTimeAsync(21000); });
      expect(screen.getByText(/keeps running if you close this tab/)).toBeInTheDocument();
    } finally {
      s.close();
      vi.useRealTimers();
    }
  });
});

describe("chat page: remaining paths", () => {
  it("a saved error offers other models and resends the question with the one chosen", async () => {
    nav.search = "s=s3";
    const { calls } = base({
      "GET /api/chat/sessions/s3": () => jsonResponse({ session: { id: "s3", title: "Down", provider: "anthropic", model: "claude-x" },
        messages: [{ role: "user", text: "help" }, { role: "bot", text: "", meta: { error: { title: "Model unavailable", retryable: false,
          suggestions: [{ provider: "openai", label: "OpenAI", model: "gpt-y" }] } } }], active_job: null }),
      "POST /api/chat/stream": () => streamOf({ type: "done", reply: "switched", model_calls: [], usage: {}, steps: [], runs: [] }),
    });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "OpenAI · gpt-y" }));
    expect(await screen.findByText("switched")).toBeInTheDocument();
    expect(streams(calls)[0].body).toEqual({ provider: "openai", model: "gpt-y", message: "help", session_id: "s3" });
  });

  // BUG (documented): the error event from the backend carries "suggestions" (backend app/errors.py:66-67),
  // but the live handler at page.tsx:219 drops them, so "Try another model" only appears after a reload.
  it("a live error event offers other models", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "error", title: "Model unavailable", retryable: false,
      suggestions: [{ provider: "openai", label: "OpenAI", model: "gpt-y" }] }) });
    render(<Page />);
    await say("help");
    expect(await screen.findByText("Model unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "OpenAI · gpt-y" })).toBeInTheDocument();
  });

  it("restores a chat whose last turn finished without an answer", async () => {
    nav.search = "s=s2";
    base({ "GET /api/chat/sessions/s2": () => jsonResponse({ session: { id: "s2", title: "Quiet", provider: "anthropic" },
      messages: [{ role: "user", text: "ping" }, { role: "bot", text: "", meta: { no_answer: true, calls: [], usage: {}, steps: [] } }], active_job: null }) });
    render(<Page />);
    expect(await screen.findByText("The model finished without an answer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("a live sub-agent opens the Agents panel with its lane while it runs, and a blank note is ignored", async () => {
    const s = live([
      { type: "job", job_id: "j1", seq: 0 },
      { type: "agent_start", run_id: "root", agent: "Engineer", task: "fix", spawned: false, parent: null, seq: 1 },
      { type: "agent_start", run_id: "sub-1", agent: "helper", task: "help", spawned: true, parent: "root", seq: 2 },
      { type: "tool_call", run_id: "sub-1", agent: "helper", tool: "grep", args: "needle", seq: 3 },
      { type: "agent_text", run_id: "sub-1", text: "found it", seq: 4 },
      { type: "model_call", run_id: "sub-1", agent: "helper", provider: "anthropic", model: "claude-x", cost_usd: 0.001, input_tokens: 10, output_tokens: 2, seq: 5 },
      { type: "agent_end", run_id: "sub-1", status: "done", result: "ok", cost_usd: 0.001, ended: 3, seq: 6 },
    ]);
    const { calls } = base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; } });
    render(<Page />);
    await say("fix it");
    expect(await screen.findByText("2 runs")).toBeInTheDocument();
    const panel = document.querySelector(".ap") as HTMLElement;
    expect(within(panel).getAllByText("helper").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Agents · 1/ })).toBeInTheDocument();
    await userEvent.type(textbox(), "   {Enter}");
    expect(posts(calls, "/api/chat/jobs/j1/notes")).toHaveLength(0);
    s.push({ type: "external_text", run_id: "sub-1", delta: " and more", seq: 7 });
    s.push({ type: "done", reply: "all good", model_calls: [], usage: {}, steps: [], runs: [], seq: 7 });
    s.close();
    expect(await screen.findByText("all good")).toBeInTheDocument();
  });

  it("if the cancel request fails, Stop still aborts the stream", async () => {
    const s = live([{ type: "job", job_id: "j1", seq: 0 }]);
    base({ "POST /api/chat/stream": (init) => { watchAbort(init, s); return s.response; },
      "POST /api/chat/jobs/j1/cancel": () => textResponse("gateway down", 502) });
    render(<Page />);
    await say("go");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  });

  it("the Send button sends the same as Enter", async () => {
    const { calls } = base({ "POST /api/chat/stream": () => streamOf({ type: "done", reply: "clicked ok", model_calls: [], usage: {}, steps: [], runs: [] }) });
    render(<Page />);
    await userEvent.type(textbox(), "by click");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("clicked ok")).toBeInTheDocument();
    expect(streams(calls)[0].body.message).toBe("by click");
  });
});

describe("chat page: provider and workspace popovers", () => {
  const popovers = () => document.querySelectorAll(".opts-pop");
  it("opens them separately and closes each on a click outside, Escape, or toggling its button", async () => {
    base();
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    expect(popovers()).toHaveLength(1);
    expect(screen.queryByText("Workspace for this chat")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Workspace" }));            // switching replaces it, never stacks two
    expect(popovers()).toHaveLength(1);
    expect(screen.getByText("Workspace for this chat")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("textbox", { name: "Message" }));             // outside
    expect(popovers()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
    await userEvent.keyboard("{Escape}");
    expect(popovers()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));             // the button toggles
    expect(popovers()).toHaveLength(0);
  });
  it("closes the provider popover once a provider is chosen", async () => {
    base();
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Provider" }));
    await userEvent.selectOptions(document.querySelector(".opts-pop select") as HTMLSelectElement, "openai");
    expect(popovers()).toHaveLength(0);
  });
  it("closes the workspace popover once a folder is picked or Enter is pressed, but not while typing", async () => {
    base({ "POST /api/fs/pick-folder": () => jsonResponse({ path: "H:/picked", cancelled: false }) });
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
    await userEvent.type(screen.getByPlaceholderText("default from Settings"), "H:/typ");
    expect(popovers()).toHaveLength(1);
    await userEvent.type(screen.getByPlaceholderText("default from Settings"), "ed{Enter}");
    expect(popovers()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Workspace" })).toHaveTextContent("typed");   // the pill names the folder
    await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
    await userEvent.click(screen.getByRole("button", { name: "Browse…" }));
    await waitFor(() => expect(popovers()).toHaveLength(0));
    expect(screen.getByRole("button", { name: "Workspace" })).toHaveTextContent("picked");
  });
});
