import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

import BuilderPanel from "@/components/apps/BuilderPanel";
import PreviewPanel from "@/components/apps/PreviewPanel";
import PlanCard from "@/components/apps/PlanCard";
import StudioPanel from "@/components/apps/StudioPanel";
import DesignPanel from "@/components/apps/DesignPanel";
import ConnectPanel from "@/components/apps/ConnectPanel";
import { VersionsPanel } from "@/components/apps/AppPanels";
import type { AppRow, BuildPlan, PickedEl, Verify } from "@/lib/apps";

const APP: AppRow = { id: "a1", name: "Demo", slug: "demo", description: "", template: "static-chat", type: "static", mode: "advanced", session_id: "", version: 1, git: {}, agents: [], pending: 0, files: 3, size: 100, created: 1, updated: 1 };
const ev = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;
const MODEL = { saved: { mode: "best", provider: "", model: "" }, effective: { provider: "claude", label: "Claude", kind: "anthropic", model: "big", source: "best" } };
const OPTIONS = { options: [{ provider: "claude", label: "Claude", kind: "anthropic", model: "big", ready: true }, { provider: "off", label: "Off", kind: "x", model: "z", ready: false }] };
const PLAN: BuildPlan = { title: "Sales board", summary: "Shows sales.", pages: ["index.html"], features: ["chart"], data: ["data.json"], agents: ["coder"], steps: [{ title: "Pick the template", done: true }, { title: "Build it", done: false }], updated: 1 };
const BAD: Verify = { ok: false, problems: [{ level: "error", path: "public/app.js", text: "boom" }], console: [], preview_loaded: true, round: 1, max_rounds: 3, give_up: false, report: "- error in public/app.js: boom" };
const GOOD: Verify = { ok: true, problems: [], console: [], preview_loaded: true, round: 0, max_rounds: 3, give_up: false, report: "No problems found." };
const base = (over: Record<string, any> = {}) => ({
  "GET /api/apps/a1/model": () => jsonResponse(MODEL), "GET /api/apps/models": () => jsonResponse(OPTIONS), "GET /api/apps/a1/plan": () => jsonResponse({ plan: null }),
  "POST /api/apps/a1/verify/reset": () => jsonResponse({ round: 0 }), ...over,
});
const streams = (calls: { url: URL }[]) => calls.filter((c) => c.url.pathname === "/api/chat/stream") as any[];

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe("PlanCard", () => {
  it("shows the steps, what is done and the rest of the plan, and can be dismissed", async () => {
    const onClear = vi.fn();
    const { container, rerender } = render(<PlanCard plan={PLAN} onClear={onClear} />);
    expect(screen.getByTestId("plan-card")).toHaveTextContent("Sales board");
    expect(screen.getByLabelText("1 of 2 steps done")).toBeInTheDocument();
    expect(container.querySelectorAll("li.done")).toHaveLength(1);
    expect(screen.getByText(/Pages: index.html/)).toBeInTheDocument();
    expect(screen.getByText(/Features: chart/)).toBeInTheDocument();
    expect(screen.getByText(/Agents: coder/)).toBeInTheDocument();
    expect(screen.getByText(/Data: data.json/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss the plan" }));
    expect(onClear).toHaveBeenCalled();
    rerender(<PlanCard plan={{ ...PLAN, summary: "", pages: [], features: [], data: [], agents: [] }} />);
    expect(screen.queryByText(/Pages:/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss the plan" })).toBeNull();
    rerender(<PlanCard plan={null} />);
    expect(screen.queryByTestId("plan-card")).toBeNull();
  });
});

describe("BuilderPanel: plan and model", () => {
  it("loads the plan, can dismiss it, and reloads it when the app changes", async () => {
    let plan: BuildPlan | null = PLAN;
    routeFetch(base({ "GET /api/apps/a1/plan": () => jsonResponse({ plan }), "DELETE /api/apps/a1/plan": () => { plan = null; return jsonResponse({ plan: null }); } }));
    const { rerender } = render(<BuilderPanel app={APP} onChanged={() => {}} refreshKey={0} />);
    expect(await screen.findByTestId("plan-card")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss the plan" }));
    await waitFor(() => expect(screen.queryByTestId("plan-card")).toBeNull());
    plan = { ...PLAN, title: "Second plan" };
    rerender(<BuilderPanel app={APP} onChanged={() => {}} refreshKey={1} />);
    expect(await screen.findByText("Second plan")).toBeInTheDocument();
  });

  it("keeps the plan when dismissing fails, and shows a model that cannot be saved", async () => {
    routeFetch(base({ "GET /api/apps/a1/plan": () => jsonResponse({ plan: PLAN }), "DELETE /api/apps/a1/plan": () => jsonResponse({ detail: "x" }, { status: 500 }), "PUT /api/apps/a1/model": () => jsonResponse({ detail: "that provider is not switched on" }, { status: 422 }) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss the plan" }));
    expect(screen.getByTestId("plan-card")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Off — z \(not signed in\)/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Best available \(automatic\) — now Claude, big/ })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Builder model"), "claude|big");
    expect(await screen.findByRole("alert")).toHaveTextContent("not switched on");
  });

  it("goes back to the best model", async () => {
    const custom = { saved: { mode: "custom", provider: "claude", model: "big" }, effective: { ...MODEL.effective, source: "custom" } };
    routeFetch(base({ "GET /api/apps/a1/model": () => jsonResponse(custom), "PUT /api/apps/a1/model": (init: any) => jsonResponse({ ...MODEL, saved: JSON.parse(init.body) }) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    await waitFor(() => expect((screen.getByLabelText("Builder model") as HTMLSelectElement).value).toBe("claude|big"));
    await userEvent.selectOptions(screen.getByLabelText("Builder model"), "best");
    await waitFor(() => expect((screen.getByLabelText("Builder model") as HTMLSelectElement).value).toBe("best"));
  });
});

describe("BuilderPanel: checking and fixing", () => {
  const turn = (text = "ok") => sseResponse([ev({ type: "tool_call", name: "app_write_file" }), ev({ type: "app", action: "applied" }), ev({ type: "text", delta: text })]);

  it("checks the preview after a change, sends the builder what is wrong, and stops when it is clean", async () => {
    let verifies = 0;
    const { calls } = routeFetch(base({ "POST /api/chat/stream": () => turn(), "POST /api/apps/a1/verify": () => { verifies++; return jsonResponse(verifies === 1 ? BAD : GOOD); } }));
    render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    await userEvent.type(await screen.findByLabelText("Message to the builder"), "Add a header{Enter}");
    await waitFor(() => expect(screen.getByTestId("last-check")).toHaveTextContent("Checked: no problems found"));
    const sent = streams(calls);
    expect(sent).toHaveLength(2);
    expect(sent[0].body.message).toContain("Add a header");
    expect(sent[1].body.message).toContain("found problems (try 1 of 3)");
    expect(sent[1].body.message).toContain("error in public/app.js: boom");
    expect(sent[1].body.message.match(/\[App "Demo"/g)).toHaveLength(1);
    expect(screen.getByRole("log", { name: "Builder conversation" })).toHaveTextContent("Fixing what the check found (try 1 of 3)…");
    expect(calls.filter((c) => c.url.pathname.endsWith("/verify/reset"))).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("gives up after three tries and says what is still wrong", async () => {
    const { calls } = routeFetch(base({ "POST /api/chat/stream": () => turn(), "POST /api/apps/a1/verify": () => jsonResponse({ ...BAD, round: 1 }) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    await userEvent.type(await screen.findByLabelText("Message to the builder"), "go{Enter}");
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("after 3 tries"))).toBe(true));
    expect(streams(calls)).toHaveLength(4);
    expect(screen.getByRole("alert")).toHaveTextContent("error in public/app.js: boom");
    expect(screen.getByTestId("last-check")).toHaveTextContent("1 problem(s) left");
  });

  it("stops at once when the server says give_up", async () => {
    const { calls } = routeFetch(base({ "POST /api/chat/stream": () => turn(), "POST /api/apps/a1/verify": () => jsonResponse({ ...BAD, round: 4, give_up: true, console: [{ kind: "error", text: "x", page: "", source: "", line: 0, count: 1, at: 1 }] }) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    await userEvent.type(await screen.findByLabelText("Message to the builder"), "go{Enter}");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("after 3 tries"));
    expect(streams(calls)).toHaveLength(1);
    expect(screen.getByTestId("last-check")).toHaveTextContent("2 problem(s) left");
  });

  it("does not check when nothing changed, when switched off, or when the check cannot be read", async () => {
    const { calls } = routeFetch(base({ "POST /api/chat/stream": () => sseResponse([ev({ type: "text", delta: "just an answer" })]), "POST /api/apps/a1/verify": () => jsonResponse({ detail: "down" }, { status: 500 }) }));
    const user = userEvent.setup();
    const { unmount } = render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    await user.type(await screen.findByLabelText("Message to the builder"), "what can you do?{Enter}");
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("just an answer"));
    expect(calls.some((c) => c.url.pathname.endsWith("/verify"))).toBe(false);
    unmount();

    routeFetch(base({ "POST /api/chat/stream": () => turn(), "POST /api/apps/a1/verify": () => jsonResponse({ detail: "down" }, { status: 500 }) }));
    const second = render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    await user.type(await screen.findByLabelText("Message to the builder"), "change it{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    expect(screen.queryByTestId("last-check")).toBeNull();
    second.unmount();

    const off = routeFetch(base({ "POST /api/chat/stream": () => turn(), "POST /api/apps/a1/verify": () => jsonResponse(BAD) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    const box = screen.getByLabelText("Fix problems automatically");
    expect(box).toBeChecked();
    await user.click(box);
    expect(localStorage.getItem("apps-autofix")).toBe("off");
    await user.type(await screen.findByLabelText("Message to the builder"), "again{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    expect(off.calls.some((c) => c.url.pathname.endsWith("/verify"))).toBe(false);
    await user.click(box);
    expect(localStorage.getItem("apps-autofix")).toBe("on");
  });

  it("remembers that the automatic fixing is off", async () => {
    localStorage.setItem("apps-autofix", "off");
    routeFetch(base());
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("Fix problems automatically")).not.toBeChecked());
  });

  it("copes with storage that fails", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("no storage"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("no storage"); });
    routeFetch(base());
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    await userEvent.click(await screen.findByLabelText("Fix problems automatically"));
    expect(screen.getByLabelText("Fix problems automatically")).not.toBeChecked();
    vi.restoreAllMocks();
  });

  it("does not start a check when you press Stop while it waits", async () => {
    const { calls } = routeFetch(base({ "POST /api/chat/stream": () => turn(), "POST /api/apps/a1/verify": () => jsonResponse(BAD) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={150} />);
    await userEvent.type(await screen.findByLabelText("Message to the builder"), "go{Enter}");
    expect(await screen.findByTestId("builder-status")).toHaveTextContent("Checking the preview…");
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    expect(calls.some((c) => c.url.pathname.endsWith("/verify"))).toBe(false);
    expect(streams(calls)).toHaveLength(1);
  });

  it("does not send a fix after Stop pressed during a fix", async () => {
    let n = 0;
    const { calls } = routeFetch(base({
      "POST /api/chat/stream": (init: any) => { n++; return n === 1 ? turn() : new Promise<Response>((_r, rej) => { (init?.signal as AbortSignal).addEventListener("abort", () => rej(new DOMException("aborted", "AbortError"))); }); },
      "POST /api/apps/a1/verify": () => jsonResponse(BAD),
    }));
    render(<BuilderPanel app={APP} onChanged={() => {}} settleMs={0} />);
    await userEvent.type(await screen.findByLabelText("Message to the builder"), "go{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    expect(streams(calls)).toHaveLength(2);
    expect(calls.filter((c) => c.url.pathname.endsWith("/verify"))).toHaveLength(1);
  });
});

describe("BuilderPanel: requests from the preview", () => {
  it("sends a request as it is and shows a short label, and says so when it is busy", async () => {
    let release: () => void = () => {};
    const { calls } = routeFetch(base({ "POST /api/chat/stream": () => new Promise<Response>((res) => { release = () => res(sseResponse([ev({ type: "text", delta: "done" })])); }) }));
    const { rerender } = render(<BuilderPanel app={APP} onChanged={() => {}} request={null} />);
    await screen.findByLabelText("Message to the builder");
    rerender(<BuilderPanel app={APP} onChanged={() => {}} request={{ id: 1, text: '[App "Demo" (app_id a1).]\nMake it red', label: '<button> "Send": make it red' }} />);
    await waitFor(() => expect(streams(calls)).toHaveLength(1));
    expect(streams(calls)[0].body.message).toBe('[App "Demo" (app_id a1).]\nMake it red');
    expect(screen.getByRole("log")).toHaveTextContent('<button> "Send": make it red');
    rerender(<BuilderPanel app={APP} onChanged={() => {}} request={{ id: 2, text: "second" }} />);
    expect(await screen.findByText(/The builder is busy/)).toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    rerender(<BuilderPanel app={APP} onChanged={() => {}} request={{ id: 3, text: "third" }} />);
    await waitFor(() => expect(screen.getByRole("log")).toHaveTextContent("Change request from the preview"));
    expect(streams(calls)).toHaveLength(2);
    release();
  });
});

// ------------------------------------------------------------------------------------------------ the preview
describe("PreviewPanel: pick an element, size and reported problems", () => {
  const RUN = { kind: "static", status: "ready", url: "http://127.0.0.1:8767/apps-run/a1/" };
  const EL: PickedEl = { selector: "#send", tag: "button", text: "Send", html: "<button id=send>Send</button>", label: "", page: "index.html", box: { x: 1, y: 2, w: 3, h: 4 }, style: { color: "red" } };
  const routes = (over: Record<string, any> = {}) => routeFetch({ "POST /api/apps/a1/run": () => jsonResponse(RUN), "GET /api/apps/a1/console": () => jsonResponse({ loaded: true, errors: [] }), ...over });

  it("asks the page for an element, reports the one you clicked and ignores messages from anywhere else", async () => {
    routes();
    const onPicked = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<PreviewPanel app={APP} refreshKey={0} onPicked={onPicked} />);
    const frame = (await screen.findByTitle("App preview")) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    const pick = screen.getByRole("button", { name: "Pick an element" });
    expect(pick).toHaveAttribute("aria-pressed", "false");
    await user.click(pick);
    expect(post).toHaveBeenLastCalledWith({ asPick: true }, "*");
    expect(screen.getByRole("button", { name: "Click an element…" })).toHaveAttribute("aria-pressed", "true");
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { asPicked: EL }, source: window })); });          // not from the frame
    expect(onPicked).not.toHaveBeenCalled();
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: "text", source: frame.contentWindow })); });
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { other: 1 }, source: frame.contentWindow })); });
    expect(onPicked).not.toHaveBeenCalled();
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { asPicked: EL }, source: frame.contentWindow })); });
    expect(onPicked).toHaveBeenCalledWith(EL);
    expect(screen.getByRole("button", { name: "Pick an element" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pick an element" }));
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { asPickCancelled: true }, source: frame.contentWindow })); });
    expect(screen.getByRole("button", { name: "Pick an element" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pick an element" }));
    await user.click(screen.getByRole("button", { name: "Click an element…" }));
    expect(post).toHaveBeenLastCalledWith({ asPick: false }, "*");
    await user.click(screen.getByRole("button", { name: "Pick an element" }));
    rerender(<PreviewPanel app={APP} refreshKey={1} onPicked={onPicked} />);                                                // a reload ends picking
    await waitFor(() => expect(screen.getByRole("button", { name: "Pick an element" })).toBeInTheDocument());
  });

  it("offers picking only for pages, once they run, and only when somebody listens", async () => {
    routes({ "GET /api/apps/a1/status": () => jsonResponse({ kind: "node", status: "stopped" }) });
    const { unmount } = render(<PreviewPanel app={APP} refreshKey={0} />);
    await screen.findByTitle("App preview");
    expect(screen.queryByRole("button", { name: "Pick an element" })).toBeNull();
    unmount();
    render(<PreviewPanel app={{ ...APP, type: "node" }} refreshKey={0} onPicked={() => {}} />);
    await screen.findByText(/Press Run/);
    expect(screen.queryByRole("button", { name: "Pick an element" })).toBeNull();
  });

  it("switches the preview size", async () => {
    routes();
    const user = userEvent.setup();
    render(<PreviewPanel app={APP} refreshKey={0} />);
    const frame = (await screen.findByTitle("App preview")) as HTMLIFrameElement;
    expect(frame.style.maxWidth).toBe("100%");
    await user.click(screen.getByRole("button", { name: "Phone" }));
    expect(frame.style.maxWidth).toBe("390px");
    expect(screen.getByRole("button", { name: "Phone" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Tablet" }));
    expect(frame.style.maxWidth).toBe("768px");
    await user.click(screen.getByRole("button", { name: "Desktop" }));
    expect(frame.style.maxWidth).toBe("100%");
  });

  it("lists what the page reported (not warnings) and asks the builder to fix it", async () => {
    const errors = [{ kind: "error", text: "x is not defined", page: "index.html", source: "app.js", line: 9, count: 2, at: 1 }, { kind: "console.warn", text: "careful", page: "", source: "", line: 0, count: 1, at: 1 },
      { kind: "resource", text: "failed to load logo.png", page: "", source: "", line: 0, count: 1, at: 1 }, { kind: "blocked", text: "b1", page: "", source: "x.js", line: 0, count: 1, at: 1 }];
    let shown = errors;
    routes({ "GET /api/apps/a1/console": () => jsonResponse({ loaded: true, errors: shown }) });
    const onFix = vi.fn();
    const { rerender } = render(<PreviewPanel app={APP} refreshKey={0} consoleKey={0} onFix={onFix} />);
    const box = await screen.findByTestId("preview-errors");
    expect(box).toHaveTextContent("The preview reported 3 problems");
    expect(box).toHaveTextContent("x is not defined");
    expect(box).toHaveTextContent("(app.js:9)");
    expect(box).toHaveTextContent("×2");
    expect(box).toHaveTextContent("(x.js)");
    expect(box).not.toHaveTextContent("careful");
    await userEvent.click(within(box).getByRole("button", { name: "Ask the builder to fix this" }));
    expect(onFix.mock.calls[0][0]).toContain("- the preview reported (error, app.js:9): x is not defined (x2)");
    shown = [errors[0].kind === "error" ? { ...errors[0], count: 1, source: "" } : errors[0]];
    rerender(<PreviewPanel app={APP} refreshKey={0} consoleKey={1} onFix={onFix} />);
    await waitFor(() => expect(screen.getByTestId("preview-errors")).toHaveTextContent("1 problem"));
    shown = [];
    rerender(<PreviewPanel app={APP} refreshKey={0} consoleKey={2} />);
    await waitFor(() => expect(screen.queryByTestId("preview-errors")).toBeNull());
  });

  it("shows no problems box when the report cannot be loaded, and no fix button without a listener", async () => {
    routes({ "GET /api/apps/a1/console": () => jsonResponse({ detail: "x" }, { status: 500 }) });
    const { unmount } = render(<PreviewPanel app={APP} refreshKey={0} />);
    await screen.findByTitle("App preview");
    expect(screen.queryByTestId("preview-errors")).toBeNull();
    unmount();
    routes({ "GET /api/apps/a1/console": () => jsonResponse({ loaded: true, errors: [{ kind: "error", text: "e", page: "", source: "", line: 0, count: 1, at: 1 }] }) });
    render(<PreviewPanel app={APP} refreshKey={0} />);
    await screen.findByTestId("preview-errors");
    expect(screen.queryByRole("button", { name: "Ask the builder to fix this" })).toBeNull();
  });
});

// ------------------------------------------------------------------------------------------------ the studio
describe("StudioPanel", () => {
  const RUN = { kind: "static", status: "ready", url: "http://127.0.0.1:8767/apps-run/a1/" };
  const EL = { selector: "#send", tag: "button", text: "Send", html: "<button id=send>Send</button>", label: "", page: "index.html", box: { x: 1, y: 2, w: 3, h: 4 }, style: { color: "red" } };
  const routes = (over: Record<string, any> = {}) => routeFetch(base({ "POST /api/apps/a1/run": () => jsonResponse(RUN), "GET /api/apps/a1/console": () => jsonResponse({ loaded: true, errors: [{ kind: "error", text: "boom", page: "", source: "", line: 0, count: 1, at: 1 }] }), ...over }));
  const mount = (onAsk = vi.fn(), over: Record<string, any> = {}) => render(<StudioPanel app={APP} tick={0} consoleTick={0} onChanged={() => {}} start={null} onStarted={() => {}} request={null} onAsk={onAsk} {...over} />);

  it("shows the chat and the preview, with tabs for a narrow screen", async () => {
    routes();
    const user = userEvent.setup();
    mount();
    expect(screen.getByTestId("studio-panel")).toHaveAttribute("data-pane", "chat");
    await screen.findByTitle("App preview");
    expect(screen.getByTestId("builder-panel")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Preview" }));
    expect(screen.getByTestId("studio-panel")).toHaveAttribute("data-pane", "preview");
    await user.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByTestId("studio-panel")).toHaveAttribute("data-pane", "chat");
  });

  it("tells the builder exactly which element to change", async () => {
    routes();
    const onAsk = vi.fn();
    const user = userEvent.setup();
    mount(onAsk);
    const frame = (await screen.findByTitle("App preview")) as HTMLIFrameElement;
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { asPicked: EL }, source: frame.contentWindow })); });
    const bar = await screen.findByTestId("picked-bar");
    expect(bar).toHaveTextContent('<button> "Send"');
    expect(screen.getByTestId("studio-panel")).toHaveAttribute("data-pane", "preview");
    expect(within(bar).getByRole("button", { name: "Change this" })).toBeDisabled();
    await user.type(within(bar).getByLabelText("What should change about this element?"), "Make it bigger{Enter}");
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onAsk.mock.calls[0][0]).toContain("Make it bigger");
    expect(onAsk.mock.calls[0][0]).toContain("selector `#send`");
    expect(onAsk.mock.calls[0][0]).toContain("[App \"Demo\"");
    expect(onAsk.mock.calls[0][1]).toBe('<button> "Send": Make it bigger');
    expect(screen.queryByTestId("picked-bar")).toBeNull();
    expect(screen.getByTestId("studio-panel")).toHaveAttribute("data-pane", "chat");
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { asPicked: EL }, source: frame.contentWindow })); });
    await user.type(screen.getByLabelText("What should change about this element?"), "x");
    await user.click(screen.getByRole("button", { name: "Change this" }));
    expect(onAsk).toHaveBeenCalledTimes(2);
    act(() => { window.dispatchEvent(new MessageEvent("message", { data: { asPicked: EL }, source: frame.contentWindow })); });
    await user.type(screen.getByLabelText("What should change about this element?"), "{Shift>}{Enter}{/Shift}");
    expect(onAsk).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("picked-bar")).toBeNull();
    fireEvent.click(document.body);
  });

  it("hands what the preview reported to the builder", async () => {
    routes();
    const onAsk = vi.fn();
    mount(onAsk);
    await userEvent.click(await screen.findByRole("button", { name: "Ask the builder to fix this" }));
    expect(onAsk.mock.calls[0][0]).toContain("- the preview reported (error): boom");
    expect(onAsk.mock.calls[0][0]).toContain("[App \"Demo\"");
    expect(onAsk.mock.calls[0][1]).toBe("Fix the problems the preview reported");
  });
});

// ------------------------------------------------------------------------------------------------ design
describe("DesignPanel", () => {
  const T = (id: string, label: string) => ({ id, label, note: id === "warm" ? "the standard look" : "", light: { bg: "#fff", card: "#fff", accent: "#c00", fg: "#000" }, dark: {} });
  const INFO = { theme: "warm", font: "system", radius: "soft", themes: [T("warm", "Warm"), T("ocean", "Ocean")], fonts: [{ id: "system", label: "System" }, { id: "serif", label: "Serif" }], radii: ["sharp", "soft", "round"] };
  const PARTS = [{ id: "table", description: "A table", usage: "UI.table(...)", needs_js: true }, { id: "hero", description: "A hero", usage: "<section>", needs_js: false }];

  it("applies a theme, a font and corners", async () => {
    const { calls } = routeFetch({ "GET /api/apps/a1/theme": () => jsonResponse(INFO), "GET /api/apps/components": () => jsonResponse(PARTS), "PUT /api/apps/a1/theme": () => jsonResponse({ version: 2 }) });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<DesignPanel app={APP} onChanged={onChanged} onAsk={() => {}} />);
    expect(await screen.findByLabelText("Warm")).toBeChecked();
    await user.click(screen.getByLabelText("Ocean"));
    await user.selectOptions(screen.getByLabelText("Font"), "serif");
    await user.selectOptions(screen.getByLabelText("Corners"), "round");
    await user.click(screen.getByRole("button", { name: "Apply the theme" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ theme: "ocean", font: "serif", radius: "round" });
  });

  it("starts from the defaults when the app has no theme block, and reports a failed apply", async () => {
    routeFetch({ "GET /api/apps/a1/theme": () => jsonResponse({ ...INFO, theme: "", font: "", radius: "" }), "GET /api/apps/components": () => jsonResponse(PARTS), "PUT /api/apps/a1/theme": () => jsonResponse({ detail: "theme must be one of" }, { status: 422 }) });
    render(<DesignPanel app={{ ...APP, type: "node" }} onChanged={() => {}} onAsk={() => {}} />);
    expect(await screen.findByLabelText("Warm")).toBeChecked();
    expect(screen.getByRole("note")).toHaveTextContent("server app");
    await userEvent.click(screen.getByRole("button", { name: "Apply the theme" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("theme must be one of");
  });

  it("adds parts and shows how to use them, or asks the builder to place them", async () => {
    const { calls } = routeFetch({ "GET /api/apps/a1/theme": () => jsonResponse(INFO), "GET /api/apps/components": () => jsonResponse(PARTS), "POST /api/apps/a1/components": () => jsonResponse({ version: 3, usage: { table: "UI.table(...)" } }) });
    const onChanged = vi.fn(), onAsk = vi.fn();
    const user = userEvent.setup();
    render(<DesignPanel app={APP} onChanged={onChanged} onAsk={onAsk} />);
    await screen.findByLabelText("table");
    expect(screen.getByRole("button", { name: /Add\s+selected/ })).toBeDisabled();
    await user.click(screen.getByLabelText("table"));
    await user.click(screen.getByLabelText("hero"));
    await user.click(screen.getByLabelText("hero"));
    await user.click(screen.getByRole("button", { name: "Add 1 selected" }));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ names: ["table"] });
    expect(await screen.findByTestId("parts-usage")).toHaveTextContent("UI.table(...)");
    expect(onChanged).toHaveBeenCalled();
    await user.click(screen.getByLabelText("hero"));
    await user.click(screen.getByRole("button", { name: "Ask the builder to use them" }));
    expect(onAsk.mock.calls[0][0]).toContain("hero");
    expect(onAsk.mock.calls[0][1]).toBe("Use these parts: hero");
    expect(screen.getByRole("button", { name: /Add\s+selected/ })).toBeDisabled();
  });

  it("reports a failed add and a failed load", async () => {
    routeFetch({ "GET /api/apps/a1/theme": () => jsonResponse(INFO), "GET /api/apps/components": () => jsonResponse(PARTS), "POST /api/apps/a1/components": () => jsonResponse({ detail: "unknown component" }, { status: 422 }) });
    const { unmount } = render(<DesignPanel app={APP} onChanged={() => {}} onAsk={() => {}} />);
    await userEvent.click(await screen.findByLabelText("table"));
    await userEvent.click(screen.getByRole("button", { name: "Add 1 selected" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown component");
    unmount();
    routeFetch({ "GET /api/apps/a1/theme": () => jsonResponse({ detail: "no such app" }, { status: 404 }), "GET /api/apps/components": () => jsonResponse(PARTS) });
    render(<DesignPanel app={APP} onChanged={() => {}} onAsk={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("no such app");
  });
});

// ------------------------------------------------------------------------------------------------ connections
describe("ConnectPanel", () => {
  const CONN = {
    agents: [{ id: "coder", name: "Coder", description: "writes code", api_access: true, connected: true, purpose: "p", runtime: "local" },
      { id: "quiet", name: "Quiet", description: "", api_access: false, connected: false, purpose: "", runtime: "agentcore" },
      { id: "off", name: "Off", description: "", api_access: false, connected: true, purpose: "", runtime: "docker" },
      { id: "free", name: "Free", description: "", api_access: true, connected: false, purpose: "", runtime: "local" }],
    data: [{ id: "sales", label: "Sales", kind: "sqlite", description: "orders", enabled: true, connected: false, purpose: "" }, { id: "hr", label: "HR", kind: "postgres", description: "", enabled: false, connected: false, purpose: "" },
      { id: "fin", label: "Finance", kind: "postgres", description: "", enabled: true, connected: true, purpose: "" }],
  };
  const routes = (over: Record<string, any> = {}) => routeFetch({ "GET /api/apps/a1/connections": () => jsonResponse(CONN), ...over });

  it("lists agents and data sources with what each needs", async () => {
    routes();
    render(<ConnectPanel app={APP} onChanged={() => {}} />);
    const agents = await screen.findByRole("list", { name: "Agents" });
    expect(agents).toHaveTextContent("runs here");
    expect(agents).toHaveTextContent("runs on agentcore");
    expect(agents).toHaveTextContent("runs in Docker");
    expect(agents).toHaveTextContent("API access is off for this agent");
    expect(screen.getByRole("button", { name: "Disconnect Coder" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Quiet and turn on API access" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect Free and turn on API access" })).toBeNull();
    expect(screen.getByRole("button", { name: "Connect HR" })).toBeDisabled();
    expect(screen.getByRole("list", { name: "Data sources" })).toHaveTextContent("switched off");
    expect(screen.getByText(/SELECT queries only/)).toBeInTheDocument();
  });

  it("connects, connects with API access (only by that explicit button) and disconnects", async () => {
    const { calls } = routes({
      "POST /api/apps/a1/connect/agent": () => jsonResponse({ version: 2 }), "DELETE /api/apps/a1/connect/agent/coder": () => jsonResponse({ version: 3 }),
      "POST /api/apps/a1/connect/data": () => jsonResponse({ version: 4 }), "DELETE /api/apps/a1/connect/data/fin": () => jsonResponse({ version: 5 }),
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<ConnectPanel app={APP} onChanged={onChanged} />);
    await screen.findByRole("list", { name: "Agents" });
    await user.click(screen.getByRole("button", { name: "Connect Quiet" }));
    await user.click(screen.getByRole("button", { name: "Connect Free" }));
    await user.click(screen.getByRole("button", { name: "Connect Quiet and turn on API access" }));
    await user.click(screen.getByRole("button", { name: "Turn on API access for Off" }));
    await user.click(screen.getByRole("button", { name: "Disconnect Coder" }));
    await user.click(screen.getByRole("button", { name: "Connect Sales" }));
    await user.click(screen.getByRole("button", { name: "Disconnect Finance" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(7));
    const posts = calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.url.pathname.replace("/api/apps/a1/", "")} ${JSON.stringify(c.body ?? null)}`);
    expect(posts).toEqual([
      'POST connect/agent {"id":"quiet","enable_api":false}', 'POST connect/agent {"id":"free","enable_api":false}', 'POST connect/agent {"id":"quiet","enable_api":true}', 'POST connect/agent {"id":"off","enable_api":true}',
      "DELETE connect/agent/coder null", 'POST connect/data {"id":"sales"}', "DELETE connect/data/fin null",
    ]);
  });

  it("shows empty lists, load errors and failed actions", async () => {
    routes({ "GET /api/apps/a1/connections": () => jsonResponse({ agents: [], data: [] }) });
    const { unmount } = render(<ConnectPanel app={APP} onChanged={() => {}} />);
    expect(await screen.findByText("You have no agents yet.")).toBeInTheDocument();
    expect(screen.getByText(/No data source is set up yet/)).toBeInTheDocument();
    unmount();
    routes({ "GET /api/apps/a1/connections": () => jsonResponse(CONN), "POST /api/apps/a1/connect/agent": () => jsonResponse({ detail: "an app can call at most 10 agents" }, { status: 422 }) });
    const second = render(<ConnectPanel app={APP} onChanged={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Connect Free" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 10");
    second.unmount();
    routes({ "GET /api/apps/a1/connections": () => jsonResponse({ detail: "no such app" }, { status: 404 }) });
    render(<ConnectPanel app={APP} onChanged={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("no such app");
  });
});

// ------------------------------------------------------------------------------------------------ versions: undo and compare
describe("VersionsPanel: undo and compare two versions", () => {
  const V = [{ version: 3, author: "user", note: "", created: Date.now() / 1000, size: 10 }, { version: 2, author: "agent:App Builder", note: "added about", created: Date.now() / 1000 - 3600, size: 9 }, { version: 1, author: "user", note: "created", created: 1, size: 8 }];

  it("goes back one change", async () => {
    const { calls } = routeFetch({ "GET /api/apps/a1/versions": () => jsonResponse(V), "POST /api/apps/a1/undo": () => jsonResponse({ version: 4 }) });
    const onChanged = vi.fn();
    render(<VersionsPanel appId="a1" onChanged={onChanged} refreshKey={0} />);
    await userEvent.click(await screen.findByRole("button", { name: "Undo the last change" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST" && c.url.pathname.endsWith("/undo"))).toBe(true);
  });

  it("disables undo when there is only one version and reports a failed undo", async () => {
    routeFetch({ "GET /api/apps/a1/versions": () => jsonResponse([V[2]]) });
    const { unmount } = render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    expect(await screen.findByRole("button", { name: "Undo the last change" })).toBeDisabled();
    unmount();
    routeFetch({ "GET /api/apps/a1/versions": () => jsonResponse(V), "POST /api/apps/a1/undo": () => jsonResponse({ detail: "there is no earlier version to go back to" }, { status: 422 }) });
    render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await userEvent.click(await screen.findByRole("button", { name: "Undo the last change" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no earlier version");
  });

  it("compares two picked versions with each other", async () => {
    const { calls } = routeFetch({
      "GET /api/apps/a1/versions": () => jsonResponse(V),
      "GET /api/apps/a1/versions/compare": () => jsonResponse([{ path: "a.txt", status: "changed", old: "x", new: "y" }]),
    });
    const user = userEvent.setup();
    render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await screen.findByText(/added about/);
    expect(screen.queryByRole("button", { name: "Compare the two picked versions" })).toBeNull();
    await user.click(screen.getByLabelText("Pick version 3 to compare"));
    await user.click(screen.getByLabelText("Pick version 1 to compare"));
    await user.click(screen.getByLabelText("Pick version 2 to compare"));                       // the oldest pick drops out: 1 and 2 stay... 3 drops
    await user.click(screen.getByLabelText("Pick version 2 to compare"));
    await user.click(screen.getByLabelText("Pick version 3 to compare"));
    await user.click(screen.getByRole("button", { name: "Compare the two picked versions" }));
    const diff = await screen.findByTestId("version-diff");
    expect(diff).toHaveTextContent("Version 1 compared with version 3");
    const call = calls.find((c) => c.url.pathname.endsWith("/versions/compare"))!;
    expect(call.url.search).toBe("?a=1&b=3");
  });

  it("reports a comparison that fails", async () => {
    routeFetch({ "GET /api/apps/a1/versions": () => jsonResponse(V), "GET /api/apps/a1/versions/compare": () => jsonResponse({ detail: "version 1 is not kept" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await screen.findByText(/added about/);
    await user.click(screen.getByLabelText("Pick version 1 to compare"));
    await user.click(screen.getByLabelText("Pick version 2 to compare"));
    await user.click(screen.getByRole("button", { name: "Compare the two picked versions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not kept");
  });
});
