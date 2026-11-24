import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SideAgent, { openDocs } from "@/components/SideAgent";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: "/mcp/", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path, useRouter: () => ({ push: nav.push }) }));

const frame = (e: Record<string, unknown>) => `data: ${JSON.stringify(e)}\n\n`;
const text = (t: string) => () => sseResponse([[{ type: "RUN_STARTED", threadId: "t", runId: "r" }, { type: "TEXT_MESSAGE_START", messageId: "m", role: "assistant" },
  { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: t }, { type: "TEXT_MESSAGE_END", messageId: "m" }, { type: "RUN_FINISHED", threadId: "t", runId: "r" }].map(frame).join("")]);
const tool = (name: string, args: Record<string, unknown>, id = "c1") => () => sseResponse([[{ type: "RUN_STARTED", threadId: "t", runId: "r" },
  { type: "TOOL_CALL_START", toolCallId: id, toolCallName: name, parentMessageId: "m" }, { type: "TOOL_CALL_ARGS", toolCallId: id, delta: JSON.stringify(args) },
  { type: "TOOL_CALL_END", toolCallId: id }, { type: "RUN_FINISHED", threadId: "t", runId: "r" }].map(frame).join("")]);

const settings = (extra: Record<string, unknown> = {}) => jsonResponse({ sidebot_enabled: true, sidebot_resolved: { provider: "p", model: "m" }, ...extra });
function serve(extra: Record<string, unknown>, ...rounds: (() => Response)[]) {
  const q = [...rounds];
  return routeFetch({
    "GET /api/settings": () => settings(extra), "POST /api/sidebot": () => (q.shift() ?? text("done"))(),
    "POST /api/canvas": () => jsonResponse({ id: "cv-1" }),
  });
}
const open = async (user: ReturnType<typeof userEvent.setup>) => { await user.click(screen.getByRole("button", { name: "Open the assistant" })); return screen.getByRole("dialog", { name: "Assistant" }); };
const say = async (user: ReturnType<typeof userEvent.setup>, panel: HTMLElement, msg: string) => { await user.type(within(panel).getByLabelText("Message the assistant"), msg + "{Enter}"); };

beforeEach(() => { nav.path = "/mcp/"; nav.push.mockReset(); document.title = "t"; });
afterEach(() => { document.body.querySelectorAll("[data-fixture]").forEach((e) => e.remove()); });

function fixture(html: string) {
  const d = document.createElement("main"); d.setAttribute("data-fixture", "1"); d.innerHTML = html; document.body.appendChild(d); return d;
}

describe("the Docs tab", () => {
  it("shows the documentation beside the screen, with help for the page you are on", async () => {
    serve({});
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    expect(within(panel).queryByTestId("docs-panel")).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "Docs" }));
    expect(await within(panel).findByTestId("docs-panel")).toBeInTheDocument();
    expect(within(panel).getByTestId("docs-here")).toHaveTextContent("MCP servers");
    expect(panel).toHaveClass("wide");
    expect(within(panel).queryByLabelText("Message the assistant")).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "Assistant" }));
    expect(within(panel).getByLabelText("Message the assistant")).toBeInTheDocument();
    expect(panel).not.toHaveClass("wide");
  });

  it("opens and closes with Ctrl+/ and opens with ? only when no text box has focus", async () => {
    serve({});
    render(<SideAgent />);
    fireEvent.keyDown(window, { key: "/", ctrlKey: true });
    const panel = await screen.findByRole("dialog", { name: "Assistant" });
    expect(await within(panel).findByTestId("docs-panel")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "/", ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Assistant" })).toBeNull());
    fireEvent.keyDown(window, { key: "/", metaKey: true });
    expect(await screen.findByRole("dialog", { name: "Assistant" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close the assistant" }));
    const box = fixture(`<input id="typing">`).querySelector("input")!;
    box.focus();
    fireEvent.keyDown(box, { key: "?" });
    expect(screen.queryByRole("dialog", { name: "Assistant" })).toBeNull();
    fireEvent.keyDown(document.body, { key: "?", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Assistant" })).toBeNull();
    fireEvent.keyDown(document.body, { key: "?" });
    expect(await screen.findByRole("dialog", { name: "Assistant" })).toBeInTheDocument();
  });

  it("opens a page when something asks for it, from an event or from openDocs()", async () => {
    serve({});
    render(<SideAgent />);
    act(() => { openDocs({ slug: "live-mode", anchor: "latency" }); });
    const panel = await screen.findByRole("dialog", { name: "Assistant" });
    expect(await within(panel).findByTestId("doc-live-mode")).toBeInTheDocument();
    act(() => { window.dispatchEvent(new CustomEvent("as:docs", { detail: { slug: "chat" } })); });
    expect(await within(panel).findByTestId("doc-chat")).toBeInTheDocument();
    act(() => { window.dispatchEvent(new CustomEvent("as:docs")); });          // no detail: just show the docs
    expect(within(panel).getByTestId("doc-chat")).toBeInTheDocument();
    act(() => { openDocs(); });
  });

  it("follows an app link in the docs to the router and opens the full page", async () => {
    serve({});
    const user = userEvent.setup();
    render(<SideAgent />);
    act(() => { openDocs({ slug: "first-steps" }); });
    const panel = await screen.findByRole("dialog", { name: "Assistant" });
    await within(panel).findByTestId("doc-first-steps");
    await user.click(within(panel).getByRole("button", { name: "Open full page" }));
    expect(nav.push).toHaveBeenCalledWith("/docs/first-steps/");
    expect(screen.queryByRole("dialog", { name: "Assistant" })).toBeNull();
    act(() => { openDocs({ slug: "providers" }); });
    const again = await screen.findByRole("dialog", { name: "Assistant" });
    const appLink = within(again).getAllByRole("link").find((l) => l.getAttribute("href")?.startsWith("/settings/"));
    if (appLink) { await user.click(appLink); expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/settings\//)); }
  });
});

describe("what the assistant is told about the screen", () => {
  it("sends the form fields on screen, never a secret, fresh on every send", async () => {
    const { calls } = serve({}, text("ok"));
    fixture(`<h1>Settings</h1><label>Workspace<input value="C:/work"></label><label>OpenAI API key<input value="sk-proj-abcdefghijklmnop1234"></label>`);
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    await say(user, panel, "hello");
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/sidebot")).toBe(true));
    const ctx = calls.find((c) => c.url.pathname === "/api/sidebot")!.body.context;
    expect(ctx[0]).toEqual({ description: "current page", value: "/mcp/" });
    expect(ctx[1].description).toMatch(/what is on the screen now/);
    expect(ctx[1].value).toContain("Workspace");
    expect(ctx[1].value).toContain("C:/work");
    expect(ctx[1].value).not.toContain("sk-proj");
    expect(ctx[1].value).toContain('"label":"OpenAI API key"');
  });

  it("sends only the page when the setting says the screen is private, and says so", async () => {
    const { calls } = serve({ sidebot_page_context: false }, text("ok"));
    fixture(`<label>Workspace<input value="C:/work"></label>`);
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    await waitFor(() => expect(within(panel).getByText(/I cannot see what is on your screen/)).toBeInTheDocument());
    await say(user, panel, "hello");
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/sidebot")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/sidebot")!.body.context).toEqual([{ description: "current page", value: "/mcp/" }]);
  });

  it("tells the person what it can and cannot see", async () => {
    serve({});
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    expect(within(panel).getByText(/never passwords or keys/)).toBeInTheDocument();
  });
});

describe("changes the assistant proposes", () => {
  const form = () => fixture(`<label>Agent name<input id="nm" value="old name"></label><label>Secret key<input id="sk" value="x"></label>`);
  const setField = (id: string) => tool("set_field", { ref: (document.querySelector(id) as HTMLElement).getAttribute("data-as-ref"), value: "new name", reason: "You asked for a new name" });

  async function propose(user: ReturnType<typeof userEvent.setup>) {
    form();
    // the model needs the reference, which is assigned when the screen is read: read it once first, the way read_page does
    const first = tool("read_page", {});
    const q: (() => Response)[] = [first];
    const calls = routeFetch({
      "GET /api/settings": () => settings(), "POST /api/sidebot": () => {
        const next = q.shift();
        if (next) return next();
        return text("All done.")();
      },
    }).calls;
    render(<SideAgent />);
    const panel = await open(user);
    return { panel, calls, q };
  }

  it("shows a before-and-after card, waits for yes, applies the change, then tells the model", async () => {
    const user = userEvent.setup();
    const { panel, calls, q } = await propose(user);
    await say(user, panel, "rename it");
    await waitFor(() => expect(within(panel).getByText(/read_page:/)).toBeInTheDocument());
    // by now the field has a reference
    q.push(setField("#nm"), text("Renamed."));
    await say(user, panel, "go on");
    const card = await within(panel).findByTestId("sa-approve");
    expect(card).toHaveTextContent("Change this field?");
    expect(card).toHaveTextContent("You asked for a new name");
    expect(within(card).getByText("old name")).toHaveClass("del");
    expect(within(card).getByText("new name")).toHaveClass("ins");
    expect((document.querySelector("#nm") as HTMLInputElement).value).toBe("old name");       // nothing changed yet
    expect(within(panel).queryByText("working…")).toBeNull();
    await user.click(within(card).getByRole("button", { name: "Apply" }));
    await within(panel).findByText("Renamed.");
    expect((document.querySelector("#nm") as HTMLInputElement).value).toBe("new name");
    expect(screen.queryByTestId("sa-approve")).toBeNull();
    const posts = calls.filter((c) => c.url.pathname === "/api/sidebot");
    expect(posts.at(-1)!.body.messages.at(-1)).toMatchObject({ role: "tool", content: "done: Agent name: old name -> new name" });
  });

  it("changes nothing when the person says not now", async () => {
    const user = userEvent.setup();
    const { panel, calls, q } = await propose(user);
    await say(user, panel, "x");
    await waitFor(() => expect(within(panel).getByText(/read_page:/)).toBeInTheDocument());
    q.push(setField("#nm"), text("OK, left it."));
    await say(user, panel, "rename");
    const card = await within(panel).findByTestId("sa-approve");
    await user.click(within(card).getByRole("button", { name: "Not now" }));
    await within(panel).findByText("OK, left it.");
    expect((document.querySelector("#nm") as HTMLInputElement).value).toBe("old name");
    expect(calls.filter((c) => c.url.pathname === "/api/sidebot").at(-1)!.body.messages.at(-1).content).toBe("the user declined the change; nothing was changed");
  });

  it("shows a card for several fields and refuses the secret one without asking", async () => {
    const user = userEvent.setup();
    const { panel, q } = await propose(user);
    await say(user, panel, "x");
    await waitFor(() => expect(within(panel).getByText(/read_page:/)).toBeInTheDocument());
    const ref = (id: string) => (document.querySelector(id) as HTMLElement).getAttribute("data-as-ref");
    q.push(tool("fill_form", { changes: [{ ref: ref("#nm"), value: "A" }, { ref: ref("#sk"), value: "B" }] }), text("fine"));
    await say(user, panel, "fill");
    const card = await within(panel).findByTestId("sa-approve");
    expect(card.querySelectorAll(".sa-diff")).toHaveLength(1);          // the secret field is not on the card
    await user.click(within(card).getByRole("button", { name: "Apply" }));
    await within(panel).findByText("fine");
    expect((document.querySelector("#sk") as HTMLInputElement).value).toBe("x");
  });

  it("shows a card for two or more fields with the right heading", async () => {
    const user = userEvent.setup();
    fixture(`<label>Agent name<input id="nm" value="old name"></label><label>Other<input id="ot" value="1"></label>`);
    const q: (() => Response)[] = [tool("read_page", {})];
    routeFetch({ "GET /api/settings": () => settings(), "POST /api/sidebot": () => (q.shift() ?? text("ok"))() });
    render(<SideAgent />);
    const panel = await open(user);
    await say(user, panel, "x");
    await waitFor(() => expect(within(panel).getByText(/read_page:/)).toBeInTheDocument());
    const ref = (id: string) => (document.querySelector(id) as HTMLElement).getAttribute("data-as-ref");
    q.push(tool("fill_form", { changes: [{ ref: ref("#nm"), value: "A" }, { ref: ref("#ot"), value: "2" }] }), text("ok"));
    await say(user, panel, "fill");
    const card = await within(panel).findByTestId("sa-approve");
    expect(card).toHaveTextContent("Change 2 fields?");
    await user.click(within(card).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect((document.querySelector("#ot") as HTMLInputElement).value).toBe("2"));
  });

  it("lets a newer proposal replace an older one, which counts as a no", async () => {
    const user = userEvent.setup();
    const { panel, calls, q } = await propose(user);
    await say(user, panel, "x");
    await waitFor(() => expect(within(panel).getByText(/read_page:/)).toBeInTheDocument());
    const ref = (document.querySelector("#nm") as HTMLElement).getAttribute("data-as-ref");
    const two = () => sseResponse([[{ type: "RUN_STARTED", threadId: "t", runId: "r" },
      { type: "TOOL_CALL_START", toolCallId: "a1", toolCallName: "set_field", parentMessageId: "m" }, { type: "TOOL_CALL_ARGS", toolCallId: "a1", delta: JSON.stringify({ ref, value: "first" }) }, { type: "TOOL_CALL_END", toolCallId: "a1" },
      { type: "TOOL_CALL_START", toolCallId: "a2", toolCallName: "set_field", parentMessageId: "m" }, { type: "TOOL_CALL_ARGS", toolCallId: "a2", delta: JSON.stringify({ ref, value: "second" }) }, { type: "TOOL_CALL_END", toolCallId: "a2" },
      { type: "RUN_FINISHED", threadId: "t", runId: "r" }].map(frame).join("")]);
    q.push(two, text("Settled."));
    await say(user, panel, "two changes");
    const card = await within(panel).findByTestId("sa-approve");
    await waitFor(() => expect(within(card).getByText("second")).toBeInTheDocument());
    await user.click(within(card).getByRole("button", { name: "Apply" }));
    await within(panel).findByText("Settled.");
    expect((document.querySelector("#nm") as HTMLInputElement).value).toBe("second");
    const tools = calls.filter((c) => c.url.pathname === "/api/sidebot").at(-1)!.body.messages.filter((m: { role: string }) => m.role === "tool").slice(-2);
    expect(tools.map((t: { content: string }) => t.content)).toEqual(["the user declined the change; nothing was changed", "done: Agent name: old name -> second"]);
  });

  it("counts leaving the page as a no", async () => {
    const user = userEvent.setup();
    const { panel, q } = await propose(user);
    await say(user, panel, "x");
    await waitFor(() => expect(within(panel).getByText(/read_page:/)).toBeInTheDocument());
    q.push(setField("#nm"));
    await say(user, panel, "rename");
    await within(panel).findByTestId("sa-approve");
    cleanup();                                                    // the assistant goes away with the card still open
    expect((document.querySelector("#nm") as HTMLInputElement).value).toBe("old name");
  });
});

describe("other tools in the panel", () => {
  it("opens a canvas and goes to it", async () => {
    const { calls } = serve({}, tool("open_canvas", { title: "Meeting notes", type: "markdown", content: "# Notes" }), text("Opened."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    await say(user, panel, "open a canvas");
    await within(panel).findByText("Opened.");
    const post = calls.find((c) => c.url.pathname === "/api/canvas")!;
    expect(post.body).toMatchObject({ title: "Meeting notes", type: "markdown", content: "# Notes", session_id: "", agent: "" });
    expect(nav.push).toHaveBeenCalledWith("/?canvas=cv-1");
  });

  it("opens a documentation page beside the screen when the model asks", async () => {
    serve({}, tool("open_docs", { page: "live-mode", anchor: "latency" }), text("Here is the page."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    await say(user, panel, "how fast is live mode?");
    expect(await within(panel).findByTestId("doc-live-mode")).toBeInTheDocument();          // the panel switched to the Docs tab by itself
    await user.click(within(panel).getByRole("button", { name: "Assistant" }));
    await within(panel).findByText("Here is the page.");
  });

  it("waits for the new screen before the next round when it navigates", async () => {
    nav.push.mockImplementation((p: string) => window.history.replaceState({}, "", p));
    const { calls } = serve({}, tool("navigate", { page: "/settings/", target: "#providers" }), text("You are there."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await open(user);
    await say(user, panel, "take me to the providers");
    await within(panel).findByText("You are there.");
    expect(nav.push).toHaveBeenCalledWith("/settings/#providers");
    expect(calls.filter((c) => c.url.pathname === "/api/sidebot").at(-1)!.body.messages.at(-1).content).toBe("opened /settings/#providers. Call read_page to see it.");
    window.history.replaceState({}, "", "/");
  });
});
