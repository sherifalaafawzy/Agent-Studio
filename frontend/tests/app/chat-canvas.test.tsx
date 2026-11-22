import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import { forgetModels } from "@/components/ModelSelect";
import { loadVoice } from "@/components/Voice";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "", push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push, replace: nav.replace }) }));
vi.mock("@/components/canvas/CanvasPanel", () => ({
  default: (p: any) => (
    <div data-testid="canvas-panel" data-id={p.canvasId} data-tick={p.refreshKey}>
      <button onClick={p.onClose}>stub-close</button><button onClick={p.onGone}>stub-gone</button><button onClick={() => p.onAsk("please shorten the intro")}>stub-ask</button>
    </div>),
}));

const anthropic = { id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true };
const voiceOff = { configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0, chars_used_month: 0 };
const call = { agent: "engineer", provider: "anthropic", model: "claude-x", cost_usd: 0.0001, input_tokens: 10, output_tokens: 5 };
const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const streamOf = (...events: object[]) => sseResponse(events.map(frame));
const done = (reply = "All done") => ({ type: "done", reply, model_calls: [call], usage: { input_tokens: 10, output_tokens: 5, cost_usd: 0.0001 }, steps: [], runs: [], warnings: [] });
const canvasRow = (id: string) => ({ id, title: "Memo", type: "markdown", language: "", version: 1, session_id: "s1", agent: "", artifact_id: "", size: 0, created: 1, updated: 2, pending: 0 });

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
const panel = () => screen.queryByTestId("canvas-panel");
const chip = () => screen.getAllByRole("button", { name: "Canvas" }).find((b) => b.classList.contains("chip"))!;

beforeEach(async () => {
  nav.search = ""; nav.replace.mockClear(); nav.push.mockClear(); toasts = [];
  window.addEventListener("as:toast", onToast);
  forgetModels("anthropic");
  routeFetch({ "GET /api/voice/settings": () => jsonResponse(voiceOff) });
  await loadVoice(true);
});
afterEach(() => { window.removeEventListener("as:toast", onToast); vi.unstubAllGlobals(); });

describe("chat page: the canvas beside the chat", () => {
  it("opens when the agent opens one, reloads when it changes it, and switches between chat and canvas", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "canvas", action: "open", canvas_id: "c1", title: "Memo" }, { type: "text", delta: "Opened" },
      { type: "canvas", action: "proposed", canvas_id: "c1", version: 1, pending: 1 }, done("Opened it")) });
    render(<Page />);
    await say("write a memo");
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c1");
    await waitFor(() => expect(panel()).toHaveAttribute("data-tick", "2"));
    const tabs = screen.getByRole("group", { name: "Chat or canvas" });
    expect(within(tabs).getByRole("button", { name: "Canvas" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(tabs).getByRole("button", { name: "Chat" }));
    expect(within(tabs).getByRole("button", { name: "Chat" })).toHaveAttribute("aria-pressed", "true");
    expect(document.querySelector(".cv")).toHaveClass("show-chat");
    await userEvent.click(within(tabs).getByRole("button", { name: "Canvas" }));
    expect(document.querySelector(".cv")).toHaveClass("show-canvas");
    await userEvent.click(screen.getByRole("button", { name: "stub-close" }));
    expect(panel()).toBeNull();
    expect(screen.queryByRole("group", { name: "Chat or canvas" })).toBeNull();
  });

  it("a later change keeps the current tab, and the canvas going away closes it", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "canvas", action: "applied", canvas_id: "c1", version: 2, pending: 0 }, done()) });
    render(<Page />);
    await say("go");
    await screen.findByTestId("canvas-panel");
    expect(document.querySelector(".cv")).toHaveClass("show-chat");                   // only an "open" brings the canvas to the front
    await userEvent.click(screen.getByRole("button", { name: "stub-gone" }));
    expect(panel()).toBeNull();
  });

  it("the Canvas button starts a blank canvas, and closes it again", async () => {
    const f = base({ "POST /api/canvas": () => jsonResponse(canvasRow("c7")) });
    render(<Page />);
    await userEvent.click(chip());
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c7");
    expect(f.calls.find((c) => c.url.pathname === "/api/canvas" && c.method === "POST")!.body).toEqual({ title: "Untitled", type: "markdown", content: "", session_id: "", agent: "" });
    expect(chip()).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(chip());
    expect(panel()).toBeNull();
  });

  it("the Canvas button reopens this chat's newest canvas, and says why it could not", async () => {
    base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "hi" }, done("hi")), "GET /api/canvas?session_id=s1": () => jsonResponse([canvasRow("c3"), canvasRow("c2")]) });
    render(<Page />);
    await say("hello");
    await screen.findByText("hi", { selector: ".md p" });
    await userEvent.click(chip());
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c3");
    await userEvent.click(screen.getByRole("button", { name: "stub-close" }));
    routeFetch({ "GET /api/canvas?session_id=s1": () => jsonResponse({ detail: "canvas is off" }, { status: 422 }) });
    await userEvent.click(chip());
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "canvas is off", tone: "err" }));
    routeFetch({ "GET /api/canvas?session_id=s1": () => jsonResponse([]), "POST /api/canvas": () => jsonResponse(canvasRow("c9")) });
    await userEvent.click(chip());
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c9");
  });

  it("opens the canvas named in the address, or the newest one", async () => {
    nav.search = "canvas=c5";
    base();
    const { unmount } = render(<Page />);
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c5");
    unmount();
    nav.search = "canvas=latest";
    base({ "GET /api/canvas?limit=1": () => jsonResponse([canvasRow("c8")]) });
    const second = render(<Page />);
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c8");
    second.unmount();
    base({ "GET /api/canvas?limit=1": () => jsonResponse([]), "POST /api/canvas": () => jsonResponse(canvasRow("c10")) });
    const third = render(<Page />);
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c10");
    third.unmount();
    base({ "GET /api/canvas?limit=1": () => jsonResponse({ detail: "off" }, { status: 422 }) });
    render(<Page />);
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "off", tone: "err" }));
  });

  it("opens an answer in the canvas to edit it, titled from its first heading", async () => {
    const f = base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done("# Launch plan\n\nShip it on Friday.")), "POST /api/canvas": () => jsonResponse(canvasRow("c4")) });
    render(<Page />);
    await say("draft a plan");
    await userEvent.click(await screen.findByRole("button", { name: "Edit in canvas" }));
    expect(await screen.findByTestId("canvas-panel")).toHaveAttribute("data-id", "c4");
    expect(f.calls.find((c) => c.url.pathname === "/api/canvas")!.body).toEqual({ title: "Launch plan", type: "markdown", content: "# Launch plan\n\nShip it on Friday.", session_id: "s1", agent: "" });
  });

  it("titles an answer without a heading from its first line, and reports a failure", async () => {
    const f = base({ "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "x" }, done("Just a plain answer.\nSecond line.")), "POST /api/canvas": () => jsonResponse({ detail: "canvas is off" }, { status: 422 }) });
    render(<Page />);
    await say("hi");
    await userEvent.click(await screen.findByRole("button", { name: "Edit in canvas" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "canvas is off", tone: "err" }));
    expect(f.calls.find((c) => c.url.pathname === "/api/canvas")!.body.title).toBe("Just a plain answer.");
  });

  it("a question about a selection goes to the chat as the next message", async () => {
    const f = base({ "POST /api/canvas": () => jsonResponse(canvasRow("c6")), "POST /api/chat/stream": () => streamOf({ type: "session", session_id: "s1" }, { type: "text", delta: "ok" }, done("ok")) });
    render(<Page />);
    await userEvent.click(chip());
    await screen.findByTestId("canvas-panel");
    await userEvent.click(screen.getByRole("button", { name: "stub-ask" }));
    await waitFor(() => expect(f.calls.some((c) => c.url.pathname === "/api/chat/stream")).toBe(true));
    expect(f.calls.find((c) => c.url.pathname === "/api/chat/stream")!.body.message).toBe("please shorten the intro");
    expect((await screen.findAllByText("please shorten the intro")).length).toBeGreaterThan(0);
    expect(document.querySelector(".cv")).toHaveClass("show-chat");
  });
});

describe("chat page: the canvas divider", () => {
  const open = async () => { base({ "POST /api/canvas": () => jsonResponse(canvasRow("c1")) }); render(<Page />); await userEvent.click(chip()); return screen.findByRole("separator"); };
  const width = () => (document.querySelector(".cv-canvas") as HTMLElement).style.getPropertyValue("--canvas-w");

  it("widens and narrows with the arrow keys within limits, and remembers the width", async () => {
    const bar = await open();
    expect(width()).toBe("520px");
    fireEvent.keyDown(bar, { key: "ArrowLeft" });
    expect(width()).toBe("560px");
    expect(localStorage.getItem("canvas_w")).toBe("560");
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(width()).toBe("480px");
    fireEvent.keyDown(bar, { key: "ArrowUp" });
    expect(width()).toBe("480px");
    for (let i = 0; i < 12; i++) fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(width()).toBe("320px");
    for (let i = 0; i < 20; i++) fireEvent.keyDown(bar, { key: "ArrowLeft" });
    expect(width()).toBe("900px");
  });

  it("follows the pointer when dragged, and stops when released", async () => {
    const bar = await open();
    vi.stubGlobal("innerWidth", 1400);
    fireEvent.pointerDown(bar, { clientX: 880 });
    act(() => { window.dispatchEvent(new MouseEvent("pointermove", { clientX: 800 })); });
    expect(width()).toBe("600px");
    act(() => { window.dispatchEvent(new MouseEvent("pointermove", { clientX: 100 })); });
    expect(width()).toBe("900px");
    act(() => { window.dispatchEvent(new MouseEvent("pointerup")); });
    expect(localStorage.getItem("canvas_w")).toBe("900");
    act(() => { window.dispatchEvent(new MouseEvent("pointermove", { clientX: 1300 })); });
    expect(width()).toBe("900px");                                                    // the drag is over
  });

  it("starts from the remembered width, and from the default when storage cannot be read", async () => {
    localStorage.setItem("canvas_w", "700");
    const bar = await open();
    expect(bar).toBeInTheDocument();
    expect(width()).toBe("700px");
    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const bar2 = await open();
    expect(width()).toBe("520px");
    fireEvent.keyDown(bar2, { key: "ArrowLeft" });                                    // saving the width quietly fails
    expect(width()).toBe("560px");
    cleanup();
    localStorage.clear();
  });
});
