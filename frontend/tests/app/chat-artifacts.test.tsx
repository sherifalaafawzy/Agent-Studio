import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push, replace: nav.replace }) }));

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const call = { agent: "engineer", provider: "anthropic", model: "claude-x", cost_usd: 0.0001, input_tokens: 10, output_tokens: 5 };
const art = (id: string, name: string, over: object = {}) => ({ id, name, path: name, type: "document", mime: "text/markdown", preview: "markdown", size: 2048, sha256: "a".repeat(64), version: 1, created: Date.now() / 1000,
  agent: "engineer", session_id: "s1", trace_id: "t", workspace: "", source: "auto", tags: [], summary: "", secrets: false, copies: [{ storage: "local", status: "synced", detail: "", size: 1, updated: 0 }], ...over });
const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const streamOf = (...events: object[]) => sseResponse(events.map(frame));
const run = (id: string, parent: string | null, status = "done") => ({ run_id: id, parent, agent: id === "root" ? "engineer" : "helper", spawned: false, task: "t", status, steps: [], texts: [], calls: [], cost_usd: 0.002 });
const done = (extra: object = {}) => ({ type: "done", reply: "All done", model_calls: [call], usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001 }, steps: [], runs: [], warnings: [], ...extra });

function base(extra: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/providers": () => jsonResponse([anthropic]), "GET /api/agents": () => jsonResponse([]),
    "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, cached: false, models: [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }] }),
    "GET /api/voice/settings": () => jsonResponse(voiceOff), "GET /api/auto": () => jsonResponse({ enabled: false, available: false, reason: "off", engine: "rules", preference: "balanced" }),
    ...extra,
  });
}
const say = (t: string) => userEvent.type(screen.getByLabelText("Message"), `${t}{Enter}`);
let toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);

beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear(); toasts = [];
  window.addEventListener("as:toast", onToast);
  forgetModels("anthropic");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});
afterEach(() => { window.removeEventListener("as:toast", onToast); vi.unstubAllGlobals(); });

describe("chat page: files an agent made", () => {
  it("shows each file under the answer as it appears, and keeps them in the final reply", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "Writing " }, { type: "artifact", artifact: art("aaaaaaaaaaaa", "report.md") },
      { type: "artifact", artifact: art("bbbbbbbbbbbb", "plot.png", { preview: "image", type: "image" }) }, done()) });
    render(<Page />);
    await say("make a report");
    const list = await screen.findByTestId("artifact-list");
    expect(within(list).getByText("2 files made in this turn", { exact: false })).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "report.md" })).toHaveAttribute("href", "/artifacts?id=aaaaaaaaaaaa");
    expect(within(list).getByAltText("plot.png")).toBeInTheDocument();
  });

  it("prefers the files the final reply lists, and shows the run state for a turn with tool calls", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, { type: "artifact", artifact: art("aaaaaaaaaaaa", "live.md") },
      done({ artifacts: [art("cccccccccccc", "final.md")], steps: [{ agent: "engineer", type: "call", tool: "write_file" }, { agent: "engineer", type: "result", tool: "write_file" }],
        runs: [run("root", null), run("r2", "root", "running")], model_calls: [call, call] })) });
    render(<Page />);
    await say("go");
    expect(await screen.findByRole("link", { name: "final.md" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "live.md" })).toBeNull();
    const panel = await screen.findByTestId("state-panel");
    expect(panel).toHaveTextContent("Run state · 2 agents · 1 tool call");
    expect(panel).toHaveTextContent("$0.0002");
    expect(panel).toHaveTextContent("helper");
  });

  it("shows no run state for a plain answer", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done()) });
    render(<Page />);
    await say("hi");
    await screen.findByText("All done");
    expect(screen.queryByTestId("state-panel")).toBeNull();
    expect(screen.queryByTestId("artifact-list")).toBeNull();
  });

  it("shows the files again when an earlier chat is reopened", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier", provider: "anthropic" }, messages: [
      { role: "user", text: "make it" }, { role: "bot", text: "Made it", meta: { calls: [call], usage: { input_tokens: 5, output_tokens: 1, cost_usd: 0 }, artifacts: [art("aaaaaaaaaaaa", "old-report.md")] } }] }) });
    render(<Page />);
    expect(await screen.findByRole("link", { name: "old-report.md" })).toBeInTheDocument();
  });
});

describe("chat page: run state of a reopened chat", () => {
  it("summarises helpers even when the saved turn recorded no calls or steps", async () => {
    nav.search = "s=s1";
    base({ "GET /api/chat/sessions/s1": () => jsonResponse({ session: { id: "s1", title: "Earlier", provider: "anthropic" }, messages: [
      { role: "user", text: "go" }, { role: "bot", text: "Done", meta: { usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0 }, runs: [{ ...run("root", null), cost_usd: undefined }, run("r2", "root", "error")] } }] }) });
    render(<Page />);
    const panel = await screen.findByTestId("state-panel");
    expect(panel).toHaveTextContent("Run state · 2 agents · 0 tool calls");
    expect(panel).toHaveTextContent("$0.0000");
  });
});

describe("chat page: keeping an answer and replying with a form", () => {
  it("keeps an answer as an artifact, named after its heading", async () => {
    const net = base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done({ reply: "# Launch plan: Q4!\nStep one" })),
      "POST /api/artifacts": () => jsonResponse(art("dddddddddddd", "Launch plan Q4.md")) });
    render(<Page />);
    await say("plan");
    await userEvent.click(await screen.findByRole("button", { name: /Keep/ }));
    await waitFor(() => expect(net.calls.find((c) => c.url.pathname === "/api/artifacts")?.body).toEqual({ name: "Launch plan Q4.md", content: "# Launch plan: Q4!\nStep one", agent: "", session_id: "s1" }));
    expect(toasts).toContainEqual({ text: "Kept on the Artifacts page", tone: "ok" });
  });

  it("names a kept answer after its first line when it has no heading, and reports a failure", async () => {
    const net = base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done({ reply: "???\nmore" })),
      "POST /api/artifacts": () => jsonResponse({ detail: "artifacts are switched off in Settings" }, { status: 422 }) });
    render(<Page />);
    await say("plan");
    await userEvent.click(await screen.findByRole("button", { name: /Keep/ }));
    await waitFor(() => expect(net.calls.find((c) => c.url.pathname === "/api/artifacts")?.body.name).toBe("answer.md"));
    await waitFor(() => expect(toasts).toContainEqual({ text: "artifacts are switched off in Settings", tone: "err" }));
  });

  it("sends a form in an answer back as the next message", async () => {
    const form = "```form\n{\"title\":\"Which plan?\",\"fields\":[{\"name\":\"plan\",\"label\":\"Plan\",\"type\":\"select\",\"options\":[\"pro\",\"team\"],\"required\":true}]}\n```";
    let n = 0;
    const net = base({ "POST /api/chat/stream": () => (n++ === 0 ? streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done({ reply: form }))
      : streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done({ reply: "Great, team it is" }))) });
    render(<Page />);
    await say("help me choose");
    await userEvent.selectOptions(await screen.findByLabelText(/Plan/), "team");
    await userEvent.click(within(screen.getByTestId("form")).getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Great, team it is")).toBeInTheDocument();
    const posts = net.calls.filter((c) => c.url.pathname === "/api/chat/stream");
    expect(posts[1].body.message).toBe("Which plan?\n- Plan: team");
  });
});
