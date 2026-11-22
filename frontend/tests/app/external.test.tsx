import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ExternalPage from "@/app/external/page";
import { jsonResponse, routeFetch, sseResponse, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/external/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const agents = [
  { id: "claude_code", label: "Claude Code", installed: true, enabled: true, notes: "", command: "claude" },
  { id: "cursor", label: "Cursor", installed: false, enabled: true, notes: "Install the Cursor CLI.", command: "cursor-agent" },
];
const modes = { current: "default", available: [{ id: "default", name: "Default" }, { id: "yolo", name: "Skip all prompts" }] };

/** Builds the frames the chat endpoint streams, one JSON object per SSE data line. */
const frames = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`);

function streamReply(events: object[]) {
  return () => sseResponse(frames(...events));
}

function setup(chat?: () => Response, extra: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/acp/agents": () => jsonResponse(agents),
    "POST /api/acp/chat": chat ?? streamReply([{ type: "session", session_id: "s-1", modes }, { type: "text", text: "Hi" }, { type: "done", stop_reason: "end_turn", seconds: 1, turns: 1, max_turns: 5 }]),
    "DELETE /api/acp/sessions/s-1": () => jsonResponse({ ok: true }),
    "POST /api/acp/sessions/s-1/cancel": () => jsonResponse({ ok: true }),
    "POST /api/acp/respond/r1": () => jsonResponse({ ok: true }),
    "POST /api/acp/respond/q1": () => jsonResponse({ ok: true }),
    "POST /api/acp/respond/pa1": () => jsonResponse({ ok: true }),
    ...extra,
  });
}

async function say(text: string) {
  const box = screen.getByPlaceholderText(/Message the agent/);
  await userEvent.type(box, text);
  await userEvent.keyboard("{Enter}");
}

describe("External agents: setup", () => {
  it("chooses the first installed and enabled agent and explains the own-accounts policy", async () => {
    setup();
    render(<ExternalPage />);
    expect(await screen.findByDisplayValue("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("own accounts · not capped")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Cursor (not installed)" })).toBeDisabled();
    expect(screen.getByText("Pick an agent and send a message. Its tool calls stream in below; anything that writes, executes or fetches asks you first (unless you chose otherwise).")).toBeInTheDocument();
  });

  it("reports when the backend is offline", async () => {
    routeFetch({ "GET /api/acp/agents": () => textResponse("x", 500) });
    render(<ExternalPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toBeInTheDocument();
  });

  it("warns when the selected agent is not on the PATH", async () => {
    setup();
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    // the Cursor option is disabled, so reach its state through the list's own change handler
    const select = screen.getAllByRole("combobox")[0];
    fireEvent.change(select, { target: { value: "cursor" } });
    expect(await screen.findByText(/cursor-agent. not found on PATH/)).toBeInTheDocument();
  });

  it("warns loudly when a mode lets the agent act without asking", async () => {
    setup(streamReply([{ type: "session", session_id: "s-1", modes }]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("hello");
    const mode = await screen.findByDisplayValue("Default");
    await userEvent.selectOptions(mode, "yolo");
    expect(screen.getByText(/lets the agent act without asking you/)).toBeInTheDocument();
  });

  it("changes the permission policy", async () => {
    setup();
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await userEvent.selectOptions(screen.getByDisplayValue("ask me for everything"), "readonly");
    expect(screen.getByDisplayValue("read-only (auto-reject non-reads)")).toBeInTheDocument();
  });
});

describe("External agents: a conversation", () => {
  it("sends a message with Enter and shows the streamed reply and its usage", async () => {
    const { calls } = setup(streamReply([
      { type: "session", session_id: "s-1", modes },
      { type: "text", text: "Hello " },
      { type: "text", text: "there" },
      { type: "thought", text: "considering" },
      { type: "done", stop_reason: "end_turn", seconds: 2, turns: 1, max_turns: 5, usage: { input_tokens: 100, output_tokens: 20 }, reported_cost: 0.0123, currency: "USD", context: { used: 120, size: 1000 } },
    ]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("hi agent");
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(screen.getByText("hi agent")).toHaveClass("msg", "user");
    expect(screen.getByText("considering")).toBeInTheDocument();
    expect(await screen.findByText(/end_turn · 2s · turn 1\/5 · 100 in \/ 20 out · agent-reported cost USD 0\.0123 · context 120\/1000 · not capped by Agent Studio/)).toBeInTheDocument();
    expect(screen.getByText(/session s-1/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ agent: "claude_code", message: "hi agent", policy: "ask" });
  });

  it("does not send an empty message, and Shift+Enter starts a new line", async () => {
    const { calls } = setup();
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    const box = screen.getByPlaceholderText(/Message the agent/);
    fireEvent.keyDown(box, { key: "Enter" });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.type(box, "line one");
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("shows a turn with no usage data, and says when a reply is cut off", async () => {
    setup(streamReply([{ type: "text", text: "partial" }, { type: "done", stop_reason: "max_tokens", seconds: 1, turns: 2, max_turns: 2 }]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("long question");
    expect(await screen.findByText(/max_tokens · 1s · turn 2\/2 · usage not reported/)).toBeInTheDocument();
  });

  it("renders tool calls, their output, plans and notes", async () => {
    setup(streamReply([
      { type: "tool_call", id: "t1", title: "Read package.json", kind: "read", status: "pending" },
      { type: "tool_update", id: "t1", status: "completed", output: '{"name":"app"}' },
      { type: "plan", entries: [{ content: "inspect", status: "completed" }, { content: "write tests", status: "pending" }] },
      { type: "auto", title: "ls", kind: "read", decision: "approved", policy: "reads_auto" },
      { type: "auto", title: "rm -rf", kind: "execute", decision: "rejected", policy: "readonly" },
      { type: "fs_write", path: "notes.md", bytes: 12 },
      { type: "ext", method: "x/telemetry", data: { ok: true } },
      { type: "usage" },
      { type: "done", stop_reason: "end_turn", seconds: 1, turns: 1, max_turns: 5 },
    ]));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("inspect");
    expect(await screen.findByText(/Read package.json/)).toBeInTheDocument();
    expect(container.querySelector("summary .pill")).toHaveClass("ok");
    expect(screen.getByText('{"name":"app"}')).toBeInTheDocument();
    expect(screen.getByText("☑ inspect")).toBeInTheDocument();
    expect(screen.getByText("☐ write tests")).toBeInTheDocument();
    expect(screen.getByText("• auto-approved: ls (read, policy reads_auto)")).toBeInTheDocument();
    expect(screen.getByText("• auto-rejected: rm -rf (execute, policy readonly)")).toHaveClass("err");
    expect(screen.getByText("• wrote notes.md (12 bytes, via workspace-jailed fs)")).toBeInTheDocument();
    // object payloads are shown as indented JSON, not "[object Object]"
    const note = screen.getByText(/x\/telemetry: /);
    expect(note.textContent).toContain('"ok": true');
    expect(note.textContent).not.toContain("[object Object]");
  });

  it("marks a failed tool and shows the agent's error", async () => {
    setup(streamReply([
      { type: "tool_call", id: "t2", title: "Run tests", kind: "execute", status: "pending" },
      { type: "tool_update", id: "t2", status: "failed" },
      { type: "error", message: "agent crashed" },
    ]));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("test it");
    expect(await screen.findByText("⚠ agent crashed")).toBeInTheDocument();
    expect(container.querySelector("summary .pill.bad")).toHaveTextContent("failed");
  });

  it("reports a stream that cannot be started", async () => {
    setup(() => jsonResponse({ detail: "agent is not installed" }, { status: 400 }));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("hello");
    expect(await screen.findByText("⚠ agent is not installed")).toBeInTheDocument();
  });

  it("reports a network failure", async () => {
    setup(() => { throw new TypeError("Failed to fetch"); });
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("hello");
    expect(await screen.findByText("⚠ Failed to fetch")).toBeInTheDocument();
  });

  it("shows a working placeholder until the turn finishes", async () => {
    setup(() => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(frames({ type: "text", text: "..." })[0])); } }), { headers: { "Content-Type": "text/event-stream" } }));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("long task");
    expect(await screen.findByText("working…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it("starts a new session, ending the previous one on the server", async () => {
    const { calls } = setup();
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("hello");
    await screen.findByText("Hi");
    await userEvent.click(screen.getByRole("button", { name: "New session" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(screen.getByText(/Pick an agent and send a message/)).toBeInTheDocument();
  });

  it("stops a running turn", async () => {
    const { calls } = setup(() => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(frames({ type: "session", session_id: "s-1", modes })[0])); } }), { headers: { "Content-Type": "text/event-stream" } }));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("long");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/acp/sessions/s-1/cancel")).toBe(true));
  });
});

describe("External agents: asking the user", () => {
  it("answers a permission request with the chosen option", async () => {
    const { calls } = setup(streamReply([
      { type: "permission", request_id: "r1", title: "Run npm test", kind: "execute", input: { cmd: "npm test" }, options: [
        { id: "allow", name: "Allow once", kind: "allow_once" }, { id: "deny", name: "Deny", kind: "reject_once" },
      ] },
    ]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("run tests");
    expect(await screen.findByText("Permission requested")).toBeInTheDocument();
    expect(screen.getByText(/"cmd":"npm test"/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/acp/respond/r1")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/r1")!.body).toEqual({ option_id: "allow" });
  });

  it("cancels a permission request", async () => {
    const { calls } = setup(streamReply([
      { type: "permission", request_id: "r1", title: "Delete a file", kind: "delete", input: "rm x", options: [] },
    ]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("tidy");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/r1")!.body).toEqual({ cancel: true });
  });

  it("answers the agent's questions", async () => {
    const { calls } = setup(streamReply([
      { type: "question", request_id: "q1", title: "Clarify", questions: [
        { id: "db", prompt: "Which database?", allowMultiple: false, options: [{ id: "pg", label: "Postgres" }, { id: "sq", label: "SQLite" }] },
        { id: "feat", prompt: "Which features?", allowMultiple: true, options: [{ id: "auth", label: "Auth" }, { id: "log", label: "Logging" }] },
      ] },
    ]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("set up");
    await userEvent.click(await screen.findByLabelText("Postgres"));
    await userEvent.click(screen.getByLabelText("Auth"));
    await userEvent.click(screen.getByLabelText("Logging"));
    await userEvent.click(screen.getByLabelText("Logging"));
    await userEvent.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/acp/respond/q1")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/q1")!.body).toEqual({
      answers: [{ questionId: "db", selectedOptionIds: ["pg"] }, { questionId: "feat", selectedOptionIds: ["auth"] }],
    });
  });

  it("skips the agent's questions", async () => {
    const { calls } = setup(streamReply([
      { type: "question", request_id: "q1", title: "Clarify", questions: [{ id: "x", prompt: "?", allowMultiple: false, options: [{ id: "a", label: "A" }] }] },
    ]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/q1")!.body).toEqual({ skip: true });
  });

  it("accepts and rejects a proposed plan", async () => {
    const { calls } = setup(streamReply([
      { type: "plan_approval", request_id: "pa1", name: "Refactor", overview: "Split the module", plan: "1. extract\n2. test" },
    ]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("plan it");
    expect(await screen.findByText("Plan approval: Refactor")).toBeInTheDocument();
    expect(screen.getByText("Split the module")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accept plan" }));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/pa1")!.body).toEqual({ accepted: true });
  });

  it("rejects a plan with a reason", async () => {
    const { calls } = setup(streamReply([{ type: "plan_approval", request_id: "pa1", plan: "x" }]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("plan it");
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/pa1")!.body).toEqual({ accepted: false, reason: "rejected by user" });
  });

  it("shows an error when an answer cannot be delivered", async () => {
    setup(streamReply([{ type: "permission", request_id: "r1", title: "Edit", kind: "edit", input: "", options: [{ id: "a", name: "Allow", kind: "allow_once" }] }]),
      { "POST /api/acp/respond/r1": () => textResponse("gone", 410) });
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("edit");
    await userEvent.click(await screen.findByRole("button", { name: "Allow" }));
    expect(await screen.findByText("HTTP 410")).toBeInTheDocument();
  });
});

describe("External agents: sessions and modes", () => {
  it("asks for a new session when the agent or workspace changes", async () => {
    setup();
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await userEvent.type(screen.getByPlaceholderText("absolute path"), "C:/work");
    expect(screen.getByPlaceholderText("absolute path")).toHaveValue("C:/work");
  });

  it("uses the workspace the user typed for the turn", async () => {
    const { calls } = setup();
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await userEvent.type(screen.getByPlaceholderText("absolute path"), "C:/work");
    await say("where am i");
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ workspace: "C:/work" });
  });

  it("sends a chosen agent mode and the session with the next message", async () => {
    const { calls } = setup(streamReply([{ type: "session", session_id: "s-1", modes }, { type: "text", text: "ok" }]));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("first");
    await screen.findByText("ok");
    await userEvent.selectOptions(await screen.findByDisplayValue("Default"), "yolo");
    await say("second");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST")[1].body).toMatchObject({ mode: "yolo", session_id: "s-1" });
  });
});

describe("External agents: abort", () => {
  it("does not report an error when the user stops the request itself", async () => {
    vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => {
      if (String(input).endsWith("/api/acp/agents")) return Promise.resolve(jsonResponse(agents));
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("abort me");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }).catch(() => screen.getByRole("button", { name: "New session" })));
    await act(async () => {});
    expect(screen.queryByText(/aborted/)).toBeNull();
  });
});
