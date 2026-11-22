import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CanvasPanel from "@/components/canvas/CanvasPanel";
import { API_BASE } from "@/lib/api";
import type { Canvas, Proposal } from "@/lib/canvas";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("@/components/canvas/CodeEditor", () => ({
  default: (p: any) => (
    <div data-testid="code-editor" data-marks={JSON.stringify(p.marks)} data-type={p.type} data-lang={p.language}>
      <textarea aria-label={p.label} value={p.value} placeholder={p.placeholder} onChange={(e) => p.onChange(e.target.value)} />
      <button onClick={() => p.onSelect?.({ start: 0, end: 5 })}>select-5</button>
      <button onClick={() => p.onSelect?.({ start: 2, end: 2 })}>cursor</button>
    </div>),
}));

const canvas = (over: Partial<Canvas> = {}): Canvas => ({ id: "c1", title: "Memo", type: "markdown", language: "", version: 1, session_id: "s1", agent: "writer", artifact_id: "aaaaaaaaaaaa", size: 12, created: 1, updated: 2, pending: 0, content: "Hello world.", ...over });
const proposal = (over: Partial<Proposal> = {}): Proposal => ({ id: "p1", canvas_id: "c1", base_version: 1, author: "agent:writer", note: "warmer greeting", status: "pending", created: 1, resolved: 0,
  edits: [{ n: 1, old: "Hello", new: "Hi", fits: true, start: 0, end: 5 }], ...over });
const ver = (version: number, author = "user", note = "") => ({ version, author, note, created: 1000, size: 5 });

class FakeEventSource {
  static all: FakeEventSource[] = [];
  onmessage: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeEventSource.all.push(this); }
  close() { this.closed = true; }
}

let state: { canvas: Canvas; proposals: Proposal[] };
function setup(extra: Record<string, any> = {}, over: Partial<Canvas> = {}) {
  state = { canvas: canvas(over), proposals: [] };
  return routeFetch({
    "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
    "PUT /api/canvas/c1": (init) => { const b = JSON.parse(String(init?.body)); state.canvas = { ...state.canvas, ...b, version: b.content !== undefined && b.content !== state.canvas.content ? state.canvas.version + 1 : state.canvas.version }; return jsonResponse(state.canvas); },
    ...extra,
  });
}
const open = async (props: Partial<React.ComponentProps<typeof CanvasPanel>> = {}) => {
  const onClose = vi.fn(), onAsk = vi.fn(), onGone = vi.fn();
  const r = render(<CanvasPanel canvasId="c1" onClose={onClose} onAsk={onAsk} onGone={onGone} {...props} />);
  await screen.findByLabelText("Canvas title");
  return { ...r, onClose, onAsk, onGone };
};
const editor = () => screen.getByRole("textbox", { name: /Text of/ }) as HTMLTextAreaElement;
let toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);

beforeEach(() => { toasts = []; FakeEventSource.all = []; vi.stubGlobal("EventSource", FakeEventSource); window.addEventListener("as:toast", onToast); });
afterEach(() => { window.removeEventListener("as:toast", onToast); });

describe("CanvasPanel: opening and saving", () => {
  it("shows the document, its kind and where it is saved", async () => {
    setup();
    await open();
    expect(screen.getByLabelText("Canvas title")).toHaveValue("Memo");
    expect(screen.getByText("Document")).toBeInTheDocument();
    expect(editor()).toHaveValue("Hello world.");
    expect(screen.getByRole("status")).toHaveTextContent("Saved · version 1");
    expect(screen.getByTestId("code-editor")).toHaveAttribute("data-type", "markdown");
  });

  it("saves a moment after you stop typing, with the version you were looking at", async () => {
    const f = setup();
    await open();
    fireEvent.change(editor(), { target: { value: "Hello there." } });
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved changes");
    await waitFor(() => expect(f.calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    expect(f.calls.find((c) => c.method === "PUT")!.body).toEqual({ content: "Hello there.", base_version: 1 });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved · version 2"));
  });

  it("says so when it could not save", async () => {
    setup({ "PUT /api/canvas/c1": () => jsonResponse({ detail: "the canvas is switched off in Settings" }, { status: 422 }) });
    await open();
    fireEvent.change(editor(), { target: { value: "changed" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("switched off");
    expect(screen.getByRole("status")).toHaveTextContent("Not saved");
  });

  it("explains a missing canvas, and tells the page when it is gone", async () => {
    routeFetch({ "GET /api/canvas/c1": () => jsonResponse({ detail: "no such canvas 'c1'" }, { status: 404 }), "GET /api/canvas/c1/proposals": () => jsonResponse({ detail: "no such canvas 'c1'" }, { status: 404 }) });
    const onGone = vi.fn();
    render(<CanvasPanel canvasId="c1" onClose={() => {}} onAsk={() => {}} onGone={onGone} />);
    await waitFor(() => expect(onGone).toHaveBeenCalled());
    routeFetch({ "GET /api/canvas/c1": () => jsonResponse({ detail: "disk on fire" }, { status: 500 }), "GET /api/canvas/c1/proposals": () => jsonResponse([]) });
    render(<CanvasPanel canvasId="c1" onClose={() => {}} onAsk={() => {}} />);
    expect(await screen.findByText("disk on fire")).toBeInTheDocument();
  });

  it("reloads when the page says the agent changed it, and listens for live updates", async () => {
    const f = setup();
    const { rerender, unmount } = await open();
    state.canvas = { ...state.canvas, content: "Agent wrote this.", version: 2 };
    rerender(<CanvasPanel canvasId="c1" refreshKey={1} onClose={() => {}} onAsk={() => {}} />);
    await waitFor(() => expect(editor()).toHaveValue("Agent wrote this."));
    expect(FakeEventSource.all[0].url).toBe(`${API_BASE}/api/canvas/c1/events`);
    state.canvas = { ...state.canvas, content: "Second.", version: 3 };
    await act(async () => { FakeEventSource.all.at(-1)!.onmessage!(); });
    await waitFor(() => expect(editor()).toHaveValue("Second."));
    state.canvas = { ...state.canvas, title: "Renamed" };                          // same version: only the details refresh
    await act(async () => { FakeEventSource.all.at(-1)!.onmessage!(); });
    expect(f.calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/canvas/c1").length).toBeGreaterThan(2);
    unmount();
    expect(FakeEventSource.all.at(-1)!.closed).toBe(true);
  });

  it("works where the browser has no live updates", async () => {
    vi.stubGlobal("EventSource", undefined);
    setup();
    await open();
    expect(editor()).toHaveValue("Hello world.");
  });
});

describe("CanvasPanel: when someone else changed it meanwhile", () => {
  async function typedWhileChanged(extra: Record<string, any> = {}) {
    setup({ "PUT /api/canvas/c1": () => jsonResponse({ detail: { message: "changed", current_version: 2, conflicts: [] } }, { status: 409 }), ...extra });
    const ui = await open();
    state.canvas = { ...state.canvas, content: "Their version.", version: 2 };
    fireEvent.change(editor(), { target: { value: "My version." } });
    return ui;
  }

  it("asks what to keep, and lets you keep yours", async () => {
    const f = await typedWhileChanged();
    const alert = await screen.findByText(/This canvas changed while you were typing/);
    expect(alert.parentElement).toHaveTextContent("now at version 2");
    f.rerender(<CanvasPanel canvasId="c1" onClose={() => {}} onAsk={() => {}} />);
    routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse([]),
      "PUT /api/canvas/c1": (init) => { state.canvas = { ...state.canvas, content: JSON.parse(String(init?.body)).content, version: 3 }; return jsonResponse(state.canvas); } });
    await userEvent.click(screen.getByRole("button", { name: "Keep my text" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved · version 3"));
    expect(screen.queryByText(/changed while you were typing/)).toBeNull();
    expect(editor()).toHaveValue("My version.");
  });

  it("lets you take the new version instead, or compare first", async () => {
    await typedWhileChanged();
    await screen.findByText(/changed while you were typing/);
    await userEvent.click(screen.getByRole("button", { name: "Compare" }));
    const diff = await screen.findByTestId("diff-view");
    expect(diff).toHaveTextContent("The new version 2 compared with what is in the canvas now");
    expect(diff).toHaveTextContent("− Their version.");
    expect(diff).toHaveTextContent("+ My version.");
    expect(within(diff).queryByRole("button", { name: "Bring this version back" })).toBeNull();
    await userEvent.click(within(diff).getByRole("button", { name: "Close" }));
    await userEvent.click(screen.getByRole("button", { name: "Take the new version" }));
    expect(editor()).toHaveValue("Their version.");
    expect(screen.queryByText(/changed while you were typing/)).toBeNull();
  });

  it("adopts the other copy quietly when it is the same text you typed", async () => {
    setup({ "PUT /api/canvas/c1": () => jsonResponse({ detail: { message: "changed", current_version: 2 } }, { status: 409 }) });
    await open();
    state.canvas = { ...state.canvas, content: "Same words.", version: 2 };
    fireEvent.change(editor(), { target: { value: "Same words." } });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved · version 2"));
    expect(screen.queryByText(/changed while you were typing/)).toBeNull();
  });

  it("notices a newer version that arrives while you have unsaved typing", async () => {
    setup();
    await open();
    fireEvent.change(editor(), { target: { value: "Typing…" } });
    state.canvas = { ...state.canvas, content: "Agent text.", version: 2 };
    await act(async () => { FakeEventSource.all.at(-1)!.onmessage!(); });
    expect(await screen.findByText(/changed while you were typing/)).toBeInTheDocument();
    expect(editor()).toHaveValue("Typing…");
  });

  it("says so when keeping your text fails", async () => {
    await typedWhileChanged();
    await screen.findByText(/changed while you were typing/);
    routeFetch({ "PUT /api/canvas/c1": () => jsonResponse({ detail: "no" }, { status: 422 }) });
    await userEvent.click(screen.getByRole("button", { name: "Keep my text" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "no", tone: "err" }));
  });
});

describe("CanvasPanel: the agent's changes", () => {
  const withProposals = (n = 1, over: Partial<Proposal> = {}) => { setup(); state.proposals = Array.from({ length: n }, (_, i) => proposal({ id: `p${i + 1}`, ...over })); };

  it("lists them, marks the text they would replace, and accepts one", async () => {
    withProposals();
    await open();
    const box = await screen.findByTestId("pending");
    expect(box).toHaveTextContent("1 change from the agent to review");
    expect(box).toHaveTextContent("Agent writer: warmer greeting");
    expect(box).toHaveTextContent("− Hello");
    expect(box).toHaveTextContent("+ Hi");
    expect(JSON.parse(screen.getByTestId("code-editor").getAttribute("data-marks")!)).toEqual([{ from: 0, to: 5 }]);
    const f = routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "POST /api/canvas/c1/proposals/p1/accept": () => { state.canvas = { ...state.canvas, content: "Hi world.", version: 2 }; state.proposals = []; return jsonResponse({ ...state.canvas, skipped: [] }); } });
    await userEvent.click(within(box).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(editor()).toHaveValue("Hi world."));
    expect(f.calls.find((c) => c.url.pathname.endsWith("/accept"))!.body).toEqual({ skip_conflicts: false });
    await waitFor(() => expect(screen.queryByTestId("pending")).toBeNull());
  });

  it("shows an addition at the start or end, and warns about a part that no longer fits", async () => {
    setup();
    state.proposals = [proposal({ note: "", edits: [{ n: 1, at: "end", new: "More.", fits: true, start: 12, end: 12 }, { n: 2, old: "gone", new: "x", fits: false, start: null, end: null }] })];
    await open();
    const box = await screen.findByTestId("pending");
    expect(box).toHaveTextContent("+ add at the end: More.");
    expect(box).toHaveTextContent("no longer fits the text");
    expect(JSON.parse(screen.getByTestId("code-editor").getAttribute("data-marks")!)).toEqual([]);
    state.proposals = [proposal({ edits: [{ n: 1, old: "x".repeat(300), new: "y".repeat(300), fits: true, start: 0, end: 300 }] })];
    await act(async () => { FakeEventSource.all.at(-1)!.onmessage!(); });
    expect(await screen.findByText(/x{140}…/)).toBeInTheDocument();
  });

  it("rejects one, or all of them", async () => {
    withProposals(2);
    await open();
    const box = await screen.findByTestId("pending");
    expect(box).toHaveTextContent("2 changes from the agent to review");
    const f = routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "POST /api/canvas/c1/proposals/p1/reject": () => { state.proposals = state.proposals.slice(1); return jsonResponse({ rejected: true }); },
      "POST /api/canvas/c1/reject-all": () => { state.proposals = []; return jsonResponse({ rejected: 1 }); } });
    await userEvent.click(within(box).getAllByRole("button", { name: "Reject" })[0]);
    await waitFor(() => expect(screen.getByTestId("pending")).toHaveTextContent("1 change from the agent"));
    await userEvent.click(screen.getByRole("button", { name: "Reject all" }));
    await waitFor(() => expect(screen.queryByTestId("pending")).toBeNull());
    expect(f.calls.map((c) => c.url.pathname)).toContain("/api/canvas/c1/reject-all");
  });

  it("accepts everything at once", async () => {
    withProposals(2);
    await open();
    await screen.findByTestId("pending");
    const f = routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "POST /api/canvas/c1/accept-all": () => { state.canvas = { ...state.canvas, content: "All applied.", version: 3 }; state.proposals = []; return jsonResponse({ ...state.canvas, accepted: 2 }); } });
    await userEvent.click(screen.getByRole("button", { name: "Accept all" }));
    await waitFor(() => expect(editor()).toHaveValue("All applied."));
    expect(f.calls.find((c) => c.url.pathname.endsWith("accept-all"))!.body).toEqual({ skip_conflicts: false });
  });

  it("explains a change that no longer fits and offers to apply the parts that do", async () => {
    withProposals();
    await open();
    const box = await screen.findByTestId("pending");
    const f = routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "POST /api/canvas/c1/proposals/p1/accept": (init) => JSON.parse(String(init?.body)).skip_conflicts
        ? (state.proposals = [], jsonResponse({ ...state.canvas, skipped: [1] })) : jsonResponse({ detail: { message: "edit 1: the text it replaces is no longer there", conflicts: [1] } }, { status: 409 }) });
    await userEvent.click(within(box).getByRole("button", { name: "Accept" }));
    expect(await screen.findByText(/the text it replaces is no longer there/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Leave it for now" }));
    expect(screen.queryByText(/no longer there/)).toBeNull();
    await userEvent.click(within(box).getByRole("button", { name: "Accept" }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply the parts that fit" }));
    await waitFor(() => expect(f.calls.filter((c) => c.url.pathname.endsWith("/accept")).at(-1)!.body).toEqual({ skip_conflicts: true }));
  });

  it("does the same when accepting everything hits a conflict, and reports other failures", async () => {
    withProposals();
    await open();
    await screen.findByTestId("pending");
    let skipped = false;
    routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "POST /api/canvas/c1/accept-all": (init) => { if (JSON.parse(String(init?.body)).skip_conflicts) { skipped = true; state.proposals = []; return jsonResponse({ ...state.canvas, accepted: 1 }); } return jsonResponse({ detail: { message: "a change does not fit" } }, { status: 409 }); },
      "POST /api/canvas/c1/proposals/p1/accept": () => jsonResponse({ detail: "boom" }, { status: 500 }), "POST /api/canvas/c1/proposals/p1/reject": () => jsonResponse({ detail: "nope" }, { status: 500 }) });
    await userEvent.click(screen.getByRole("button", { name: "Accept all" }));
    await userEvent.click(await screen.findByRole("button", { name: "Apply the parts that fit" }));
    await waitFor(() => expect(skipped).toBe(true));
    state.proposals = [proposal()];
    await act(async () => { FakeEventSource.all.at(-1)!.onmessage!(); });
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "boom", tone: "err" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "nope", tone: "err" }));
  });

  it("uses a default message when a conflict has none, and reports other failures of accept all and reject all", async () => {
    withProposals();
    await open();
    await screen.findByTestId("pending");
    routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "POST /api/canvas/c1/proposals/p1/accept": () => jsonResponse({ detail: "x" }, { status: 409 }), "POST /api/canvas/c1/accept-all": () => jsonResponse({ detail: "bad" }, { status: 500 }),
      "POST /api/canvas/c1/reject-all": () => jsonResponse({ detail: "worse" }, { status: 500 }) });
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(await screen.findByText("This change does not fit any more.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accept all" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "bad", tone: "err" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject all" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "worse", tone: "err" }));
    routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals), "POST /api/canvas/c1/accept-all": () => jsonResponse({ detail: { message: "" } }, { status: 409 }) });
    await userEvent.click(screen.getByRole("button", { name: "Accept all" }));
    expect(await screen.findByText("A change does not fit any more.")).toBeInTheDocument();
  });

  it("saves your typing before it accepts a change", async () => {
    withProposals();
    await open();
    await screen.findByTestId("pending");
    fireEvent.change(editor(), { target: { value: "Hello world. Typed." } });
    const f = routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse(state.proposals),
      "PUT /api/canvas/c1": () => jsonResponse({ ...state.canvas, content: "Hello world. Typed.", version: 2 }), "POST /api/canvas/c1/proposals/p1/accept": () => jsonResponse({ ...state.canvas, content: "Hi world. Typed.", version: 3, skipped: [] }) });
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(f.calls.map((c) => c.method + " " + c.url.pathname).filter((x) => x !== "GET /api/canvas/c1" && x !== "GET /api/canvas/c1/proposals")).toEqual(["PUT /api/canvas/c1", "POST /api/canvas/c1/proposals/p1/accept"]));
  });
});

describe("CanvasPanel: versions", () => {
  it("lists them, compares one with now, and brings one back", async () => {
    setup({ "GET /api/canvas/c1/versions": () => jsonResponse([ver(2, "agent:writer", "warmer greeting"), ver(1)]), "GET /api/canvas/c1/versions/1": () => jsonResponse({ version: 1, content: "Hello world!", author: "user", created: 1 }) });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Versions" }));
    const list = await screen.findByTestId("versions");
    expect(list).toHaveTextContent("Version 2 · Agent writer · warmer greeting");
    await userEvent.click(within(list).getByRole("button", { name: "Version 1" }));
    const diff = await screen.findByTestId("diff-view");
    expect(diff).toHaveTextContent("Version 1 · You compared with what is in the canvas now");
    expect(diff).toHaveTextContent("− Hello world!");
    const f = routeFetch({ "GET /api/canvas/c1": () => jsonResponse(state.canvas), "GET /api/canvas/c1/proposals": () => jsonResponse([]), "GET /api/canvas/c1/versions": () => jsonResponse([ver(3, "restore", "restored version 1"), ver(2), ver(1)]),
      "POST /api/canvas/c1/restore": () => { state.canvas = { ...state.canvas, content: "Hello world!", version: 3 }; return jsonResponse(state.canvas); } });
    await userEvent.click(within(diff).getByRole("button", { name: "Bring this version back" }));
    await waitFor(() => expect(editor()).toHaveValue("Hello world!"));
    expect(f.calls.find((c) => c.url.pathname.endsWith("/restore"))!.body).toEqual({ version: 1 });
    expect(await screen.findByText(/Version 3/)).toBeInTheDocument();
    expect(toasts.at(-1)!.text).toBe("Version 1 is back");
    await userEvent.click(screen.getByRole("button", { name: "Versions" }));
    expect(screen.queryByTestId("versions")).toBeNull();
  });

  it("reports failures to list, read or restore", async () => {
    setup({ "GET /api/canvas/c1/versions": () => jsonResponse({ detail: "no list" }, { status: 500 }) });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Versions" }));
    await waitFor(() => expect(toasts.at(-1)!.text).toBe("no list"));
    routeFetch({ "GET /api/canvas/c1/versions": () => jsonResponse([ver(1)]), "GET /api/canvas/c1/versions/1": () => jsonResponse({ detail: "version 1 is not kept" }, { status: 422 }), "POST /api/canvas/c1/restore": () => jsonResponse({ detail: "cannot restore" }, { status: 422 }) });
    await userEvent.click(screen.getByRole("button", { name: "Versions" }));
    await userEvent.click(await screen.findByRole("button", { name: "Version 1" }));
    await waitFor(() => expect(toasts.at(-1)!.text).toBe("version 1 is not kept"));
  });
});

describe("CanvasPanel: a restore that fails", () => {
  it("says why", async () => {
    setup({ "GET /api/canvas/c1/versions": () => jsonResponse([ver(1)]), "GET /api/canvas/c1/versions/1": () => jsonResponse({ version: 1, content: "Old text", author: "user", created: 1 }),
      "POST /api/canvas/c1/restore": () => jsonResponse({ detail: "cannot restore" }, { status: 422 }) });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Versions" }));
    await userEvent.click(await screen.findByRole("button", { name: "Version 1" }));
    await userEvent.click(await screen.findByRole("button", { name: "Bring this version back" }));
    await waitFor(() => expect(toasts.at(-1)).toMatchObject({ text: "cannot restore", tone: "err" }));
    expect(editor()).toHaveValue("Hello world.");
  });
});

describe("CanvasPanel: asking the agent about a selection", () => {
  it("sends the selection to the server and the question to the chat", async () => {
    const f = setup({ "POST /api/canvas/c1/selection": () => jsonResponse({ start: 0, end: 5, text: "Hello", version: 1 }) });
    const { onAsk } = await open();
    expect(screen.queryByLabelText("Ask the agent about the selection")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "select-5" }));
    expect(await screen.findByText("5 characters selected")).toBeInTheDocument();
    await waitFor(() => expect(f.calls.some((c) => c.url.pathname.endsWith("/selection"))).toBe(true));
    expect(f.calls.find((c) => c.url.pathname.endsWith("/selection"))!.body).toEqual({ start: 0, end: 5 });
    const send = screen.getByRole("button", { name: "Ask the agent" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Ask the agent about the selection"), "make this shorter");
    await userEvent.click(send);
    expect(onAsk).toHaveBeenCalledWith(expect.stringContaining('About the canvas "Memo" (id c1): make this shorter'));
    expect(onAsk.mock.calls[0][0]).toContain('"""\nHello\n"""');
    expect(screen.getByLabelText("Ask the agent about the selection")).toHaveValue("");
    fireEvent.submit(screen.getByLabelText("Ask the agent about the selection").closest("form")!);
    expect(onAsk).toHaveBeenCalledTimes(1);                                           // nothing typed: nothing sent
    await userEvent.click(screen.getByRole("button", { name: "cursor" }));
    expect(screen.queryByLabelText("Ask the agent about the selection")).toBeNull();
  });

  it("saves unsaved typing first, and does not mind if the selection cannot be stored", async () => {
    const f = setup({ "POST /api/canvas/c1/selection": () => jsonResponse({ detail: "outside the document" }, { status: 422 }) });
    await open();
    fireEvent.change(editor(), { target: { value: "Hello world. More." } });
    await userEvent.click(screen.getByRole("button", { name: "select-5" }));
    await waitFor(() => expect(f.calls.map((c) => c.method + " " + c.url.pathname)).toContain("POST /api/canvas/c1/selection"));
    expect(f.calls.findIndex((c) => c.method === "PUT")).toBeLessThan(f.calls.findIndex((c) => c.url.pathname.endsWith("/selection")));
  });
});

describe("CanvasPanel: the rest of the toolbar", () => {
  it("renames the canvas when you leave the title, and puts the old name back on a failure", async () => {
    setup();
    await open();
    const title = screen.getByLabelText("Canvas title");
    await userEvent.clear(title);
    await userEvent.type(title, "New name");
    fireEvent.blur(title);
    await waitFor(() => expect(state.canvas.title).toBe("New name"));
    fireEvent.blur(title);                                                              // unchanged: nothing sent
    routeFetch({ "PUT /api/canvas/c1": () => jsonResponse({ detail: "title refused" }, { status: 422 }) });
    await userEvent.type(title, "!");
    fireEvent.blur(title);
    await waitFor(() => expect(toasts.at(-1)!.text).toBe("title refused"));
    expect(title).toHaveValue("New name");
  });

  it("changes the language of a code canvas", async () => {
    setup({}, { type: "code", language: "python", title: "Tool" });
    await open();
    expect(screen.getByText("Code")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Language"), "sql");
    await waitFor(() => expect(state.canvas.language).toBe("sql"));
    await waitFor(() => expect(screen.getByTestId("code-editor")).toHaveAttribute("data-lang", "sql"));
    routeFetch({ "PUT /api/canvas/c1": () => jsonResponse({ detail: "no language" }, { status: 422 }) });
    await userEvent.selectOptions(screen.getByLabelText("Language"), "yaml");
    await waitFor(() => expect(toasts.at(-1)!.text).toBe("no language"));
  });

  it("keeps the canvas as an artifact and links to it", async () => {
    setup({ "POST /api/canvas/c1/snapshot": () => jsonResponse({ ...state.canvas, artifact_id: "bbbbbbbbbbbb" }) });
    await open();
    expect(screen.getByRole("link", { name: "Open in Artifacts" })).toHaveAttribute("href", "/artifacts/?id=aaaaaaaaaaaa");
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", `${API_BASE}/api/artifacts/aaaaaaaaaaaa/content?download=true`);
    await userEvent.click(screen.getByRole("button", { name: "Keep in Artifacts" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Open in Artifacts" })).toHaveAttribute("href", "/artifacts/?id=bbbbbbbbbbbb"));
    expect(toasts.at(-1)!.text).toBe("Kept on the Artifacts page");
    routeFetch({ "POST /api/canvas/c1/snapshot": () => jsonResponse({ detail: "artifacts are off" }, { status: 422 }) });
    await userEvent.click(screen.getByRole("button", { name: "Keep in Artifacts" }));
    await waitFor(() => expect(toasts.at(-1)!.text).toBe("artifacts are off"));
  });

  it("has no artifact links before it has been kept", async () => {
    setup({}, { artifact_id: "" });
    await open();
    expect(screen.queryByRole("link", { name: "Open in Artifacts" })).toBeNull();
  });

  it("deletes only after you confirm", async () => {
    const f = setup({ "DELETE /api/canvas/c1": () => jsonResponse({ deleted: true }) });
    const { onGone, onClose } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    expect(screen.getByText("Delete this canvas and its versions?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(f.calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onGone).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Close canvas" }));
    expect(onClose).toHaveBeenCalled();
    routeFetch({ "DELETE /api/canvas/c1": () => jsonResponse({ detail: "cannot delete" }, { status: 500 }) });
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(toasts.at(-1)!.text).toBe("cannot delete"));
  });

  it("previews a document, a web page and a table, and has no preview for plain text", async () => {
    setup({}, { content: "# Big title\n\nSome *words*." });
    const { unmount } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(within(screen.getByTestId("preview")).getByRole("heading", { name: "Big title" })).toBeInTheDocument();
    expect(screen.queryByTestId("code-editor")).toBeNull();
    expect(screen.queryByLabelText("Ask the agent about the selection")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("code-editor")).toBeInTheDocument();
    unmount();
    setup({}, { type: "html", content: "<h1>Hi</h1>" });
    const second = await open();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByTitle(/preview \(sandboxed\)/).getAttribute("sandbox")).toBe("");
    expect(screen.queryByRole("button", { name: "Keep HTML" })).toBeNull();
    second.unmount();
    setup({}, { type: "table", content: "a,b\n1,2" });
    const third = await open();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByRole("columnheader", { name: /b/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Keep table" })).toBeNull();
    third.unmount();
    setup({}, { type: "text", content: "plain" });
    await open();
    expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
  });
});
