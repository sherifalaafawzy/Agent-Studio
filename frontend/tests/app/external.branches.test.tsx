import { render, screen, waitFor } from "@testing-library/react";
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
  { id: "kiro", label: "Kiro", installed: true, enabled: false, notes: "", command: "kiro" },
  { id: "cursor", label: "Cursor", installed: false, enabled: true, notes: "", command: "cursor-agent" },
];
const modes = { current: "default", available: [{ id: "default", name: "Default" }, { id: "yolo", name: "Skip all prompts" }] };
const frames = (...events: object[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`);
const session = { type: "session", session_id: "s-1" };
const done = { type: "done", stop_reason: "end_turn", seconds: 1, turns: 1, max_turns: 5 };

function setup(chat: () => Response | Promise<Response>, extra: Record<string, () => Response> = {}, list: any[] = agents) {
  return routeFetch({
    "GET /api/acp/agents": () => jsonResponse(list),
    "POST /api/acp/chat": chat,
    "DELETE /api/acp/sessions/s-1": () => jsonResponse({ ok: true }),
    "POST /api/acp/sessions/s-1/cancel": () => jsonResponse({ ok: true }),
    "POST /api/acp/respond/r1": () => jsonResponse({ ok: true }),
    "POST /api/acp/respond/q1": () => jsonResponse({ ok: true }),
    ...extra,
  });
}
const stream = (...events: object[]) => () => sseResponse(frames(...events));
/** A response that never ends, so the turn stays busy. */
const hanging = (...events: object[]) => () => new Response(new ReadableStream({ start(c) { for (const f of frames(...events)) c.enqueue(new TextEncoder().encode(f)); } }), { headers: { "Content-Type": "text/event-stream" } });

async function open() {
  render(<ExternalPage />);
  await screen.findByDisplayValue("Claude Code");
}
async function say(text: string) {
  await userEvent.type(screen.getByPlaceholderText(/Message the agent/), text);
  await userEvent.keyboard("{Enter}");
}

describe("External agents: agent list", () => {
  it("labels a disabled agent and cannot send when nothing is usable", async () => {
    setup(stream(), {}, [agents[1], agents[2]]);
    render(<ExternalPage />);
    expect(await screen.findByRole("option", { name: "Kiro (disabled)" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "Cursor (not installed)" })).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/Message the agent/), "hello");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});

describe("External agents: streamed blocks", () => {
  it("shows tool calls without a kind or status, and merges partial updates", async () => {
    setup(stream(
      session,
      { type: "tool_call", id: "t1", title: "Read file" },
      { type: "tool_update", id: "t1", output: "out1" },
      { type: "tool_update", id: "t1" },
      { type: "tool_update", id: "ghost", status: "completed", title: "Nope" },
      { type: "tool_call", id: "t2", title: "Edit file", kind: "edit", status: "running" },
      { type: "tool_call", id: "t3", title: "Bad call", status: "failed" },
      done,
    ));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    await screen.findByText(/Read file/);
    const sums = [...container.querySelectorAll("summary")].filter((s) => s.querySelector(".pill"));
    expect(sums.map((s) => s.textContent)).toEqual(["… Read file", "running edit: Edit file", "failed Bad call"]);
    expect(sums[0].querySelector(".pill")!.className).toBe("pill ");
    expect(sums[1].querySelector(".pill")!.className).toBe("pill ");
    expect(sums[2].querySelector(".pill")).toHaveClass("bad");
    expect(sums[0].parentElement!.querySelector("pre")).toHaveTextContent("out1");
    expect(screen.queryByText(/Nope/)).toBeNull();
  });

  it("renders notes for extension messages whether their data is text, an object or missing", async () => {
    setup(stream(
      session,
      { type: "ext", method: "m1", data: "raw text" },
      { type: "ext", method: "m2", data: { a: 1 } },
      { type: "ext", method: "m3" },
      done,
    ));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    await screen.findByText(/m1: raw text/);
    const notes = [...container.querySelectorAll(".msg.bot .small.mut")].map((n) => n.textContent);
    expect(notes).toContain("• m1: raw text");
    expect(notes).toContain('• m2: {\n  "a": 1\n}');
    expect(notes).toContain("• m3: undefined");
  });

  it("shows the agent-reported cost without a currency, plus context use", async () => {
    setup(stream(session, { ...done, usage: { input_tokens: 5, output_tokens: 6 }, reported_cost: 0.5, currency: null, context: { used: 10, size: 100 } }));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    await waitFor(() => expect(container.querySelector(".meta")).not.toBeNull());
    const meta = container.querySelector(".meta")!;
    expect(meta).toHaveTextContent("5 in / 6 out");
    expect(meta).toHaveTextContent("agent-reported cost 0.5000");
    expect(meta).toHaveTextContent("context 10/100");
    expect(meta).not.toHaveTextContent("usage not reported");
  });

  it("ignores comment frames and usage events, and keeps the mode it already has", async () => {
    setup(() => sseResponse([
      ": keep-alive\n\n",
      ...frames({ ...session, modes }, { type: "usage", input_tokens: 1 }, { type: "session", session_id: "s-1", modes: { current: "yolo", available: modes.available } }, { type: "text", text: "ok" }, done),
    ]));
    await open();
    await say("go");
    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Agent mode" })).toHaveValue("default");
    expect(screen.queryByText(/lets the agent act without asking/)).toBeNull();
  });

  it("handles a session event that carries no modes", async () => {
    setup(stream(session, { type: "text", text: "plain" }, done));
    await open();
    await say("go");
    expect(await screen.findByText("plain")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Agent mode" })).toBeNull();
    expect(screen.getByText(/session s-1/)).toBeInTheDocument();
  });
});

describe("External agents: requests from the agent", () => {
  it("names a permission request without a kind, ignores a resolution for another request, then records the real one", async () => {
    setup(hanging(
      session,
      { type: "permission", request_id: "r1", title: "Run ls", options: [{ id: "o1", kind: "allow_once", name: "Allow" }] },
      { type: "resolved", request_id: "other", decision: "nope" },
    ));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    expect(await screen.findByText("Permission requested")).toBeInTheDocument();
    expect(screen.getByText("tool", { selector: ".pill" })).toBeInTheDocument();
    expect(container).not.toHaveTextContent("nope");
    expect(screen.getByRole("button", { name: "Allow" })).toBeInTheDocument();
  });

  it("summarises resolved requests by title, name or type", async () => {
    setup(stream(
      session,
      { type: "permission", request_id: "r1", title: "Run ls", kind: "execute", options: [] },
      { type: "resolved", request_id: "r1", decision: "allowed" },
      { type: "plan_approval", request_id: "p1", name: "Migrate", plan: "step" },
      { type: "resolved", request_id: "p1", decision: "accepted" },
      { type: "question", request_id: "q9", questions: [] },
      { type: "resolved", request_id: "q9", decision: "skipped" },
      done,
    ));
    const { container } = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    await screen.findByText(/Run ls/);
    const lines = [...container.querySelectorAll(".msg.bot .small.mut")].map((n) => n.textContent);
    expect(lines).toEqual(["↳ Run ls: allowed", "↳ Migrate: accepted", "↳ question: skipped"]);
  });

  it("asks an untitled multiple-choice question and sends the ticked options", async () => {
    const { calls } = setup(hanging(
      session,
      { type: "question", request_id: "q1", questions: [{ id: "k", prompt: "Pick some", allowMultiple: true, options: [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta" }] }] },
    ));
    await open();
    await say("go");
    expect(await screen.findByText("The agent asks")).toBeInTheDocument();
    const a = screen.getByLabelText("Alpha"), b = screen.getByLabelText("Beta");
    expect(a).toHaveAttribute("type", "checkbox");
    await userEvent.click(a);
    await userEvent.click(b);
    await userEvent.click(a);
    expect(a).not.toBeChecked();
    expect(b).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/acp/respond/q1")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/acp/respond/q1")!.body).toEqual({ answers: [{ questionId: "k", selectedOptionIds: ["b"] }] });
  });
});

describe("External agents: failures and control", () => {
  it("shows the server's reason when a chat is refused with a JSON error", async () => {
    setup(() => jsonResponse({ detail: "agent not installed" }, { status: 400 }));
    await open();
    await say("go");
    expect(await screen.findByText("⚠ agent not installed")).toBeInTheDocument();
  });

  it("falls back to the status when a refusal has no readable body, or the response has no stream", async () => {
    setup(() => textResponse("bad gateway", 502));
    const first = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("one");
    expect(await screen.findByText("⚠ HTTP 502")).toBeInTheDocument();
    first.unmount();

    setup(() => new Response(null, { status: 200 }));
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("two");
    expect(await screen.findByText("⚠ HTTP 200")).toBeInTheDocument();
  });

  it("does not show an error when the request itself is aborted", async () => {
    setup(() => { throw new DOMException("The user aborted a request.", "AbortError"); });
    await open();
    await say("go");
    await waitFor(() => expect(screen.getByRole("button", { name: "New session" })).toBeEnabled());
    expect(screen.queryByText(/aborted/)).toBeNull();
    expect(screen.queryByText("working…")).toBeNull();
  });

  it("starts a fresh conversation with no server call when there was no session, and tolerates a failed delete", async () => {
    const { calls } = setup(stream(session, done), { "DELETE /api/acp/sessions/s-1": () => textResponse("x", 500) });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "New session" }));
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

    await say("go");
    await screen.findByText(/session s-1/);
    await userEvent.click(screen.getByRole("button", { name: "New session" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1));
    expect(await screen.findByText(/Pick an agent and send a message/)).toBeInTheDocument();
    expect(screen.queryByText(/session s-1/)).toBeNull();
  });

  it("stops quietly: nothing to cancel before the session exists, and a failed cancel is not an error", async () => {
    const early = setup(hanging());
    const first = render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(early.calls.some((c) => c.url.pathname.endsWith("/cancel"))).toBe(false);
    first.unmount();

    const { calls } = setup(hanging(session), { "POST /api/acp/sessions/s-1/cancel": () => textResponse("x", 500) });
    render(<ExternalPage />);
    await screen.findByDisplayValue("Claude Code");
    await say("go again");
    await screen.findByText(/session s-1/);
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/acp/sessions/s-1/cancel")).toBe(true));
    expect(screen.queryByText(/HTTP 500/)).toBeNull();
  });
});
