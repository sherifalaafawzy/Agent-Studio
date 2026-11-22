import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SideAgent, { ROUTES, TARGETS, TOOLS, runTool } from "@/components/SideAgent";
import { STATE_KEY } from "@/lib/tour";
import { jsonResponse, routeFetch, sseResponse, textResponse } from "../helpers/http";

const nav = vi.hoisted(() => {
  const push = vi.fn();
  return { path: "/", push, router: { push } };
});

vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => nav.router,
}));

const settingsOk = () => jsonResponse({
  sidebot_enabled: true,
  sidebot_resolved: { provider: "anthropic", model: "claude-test" },
});

/** One AG-UI server-sent event frame. */
const frame = (e: Record<string, unknown>) => `data: ${JSON.stringify(e)}\n\n`;

/** A model round that streams the given text deltas as one assistant message. */
function textRound(...deltas: string[]) {
  const ev: Record<string, unknown>[] = [
    { type: "RUN_STARTED", threadId: "t1", runId: "r1" },
    { type: "TEXT_MESSAGE_START", messageId: "m1", role: "assistant" },
    ...deltas.map((delta) => ({ type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta })),
    { type: "TEXT_MESSAGE_END", messageId: "m1" },
    { type: "RUN_FINISHED", threadId: "t1", runId: "r1" },
  ];
  return () => sseResponse([ev.map(frame).join("")]);
}

/** A model round that calls one browser tool with the given arguments. */
function toolRound(name: string, args: Record<string, unknown>, callId = "call-1") {
  const ev: Record<string, unknown>[] = [
    { type: "RUN_STARTED", threadId: "t1", runId: "r1" },
    { type: "TOOL_CALL_START", toolCallId: callId, toolCallName: name, parentMessageId: "m1" },
    { type: "TOOL_CALL_ARGS", toolCallId: callId, delta: JSON.stringify(args) },
    { type: "TOOL_CALL_END", toolCallId: callId },
    { type: "RUN_FINISHED", threadId: "t1", runId: "r1" },
  ];
  return () => sseResponse([ev.map(frame).join("")]);
}

/** Serves the model rounds in order, one per POST /api/sidebot. */
function model(...rounds: (() => Response)[]) {
  const queue = [...rounds];
  return routeFetch({
    "GET /api/settings": () => settingsOk(),
    "POST /api/sidebot": () => {
      const next = queue.shift();
      if (!next) throw new Error("no model round left");
      return next();
    },
  });
}

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Open the assistant" }));
  return screen.getByRole("dialog", { name: "Assistant" });
}

beforeEach(() => {
  nav.path = "/";
  nav.push.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SideAgent panel", () => {
  it("opens and closes from the launcher and the close button", async () => {
    model();
    const user = userEvent.setup();
    render(<SideAgent />);
    const launcher = screen.getByRole("button", { name: "Open the assistant" });
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog", { name: "Assistant" })).not.toBeInTheDocument();

    const panel = await openPanel(user);
    expect(panel).toBeInTheDocument();
    expect(launcher).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "Close the assistant" }));
    expect(screen.queryByRole("dialog", { name: "Assistant" })).not.toBeInTheDocument();
  });

  it("loads the resolved model from settings when opened and shows it", async () => {
    const routes = model();
    const user = userEvent.setup();
    render(<SideAgent />);
    expect(routes.calls.some((c) => c.url.pathname === "/api/settings")).toBe(false);
    await openPanel(user);
    expect(await screen.findByText("anthropic · claude-test")).toBeInTheDocument();
    expect(routes.calls.filter((c) => c.url.pathname === "/api/settings")).toHaveLength(1);
  });

  it("says when the assistant is switched off or has no model", async () => {
    const user = userEvent.setup();
    routeFetch({
      "GET /api/settings": () => jsonResponse({ sidebot_enabled: false, sidebot_resolved: { provider: "anthropic", model: "x" } }),
    });
    const { unmount } = render(<SideAgent />);
    await openPanel(user);
    expect(await screen.findByText("switched off in Settings")).toBeInTheDocument();
    unmount();

    routeFetch({
      "GET /api/settings": () => jsonResponse({ sidebot_enabled: true, sidebot_resolved: { provider: null, model: null } }),
    });
    render(<SideAgent />);
    await openPanel(user);
    expect(await screen.findByText("no model set")).toBeInTheDocument();
  });

  it("shows no model line when settings cannot be loaded", async () => {
    const user = userEvent.setup();
    const routes = routeFetch({ "GET /api/settings": () => textResponse("down", 500) });
    render(<SideAgent />);
    await openPanel(user);
    await waitFor(() => expect(routes.calls.some((c) => c.url.pathname === "/api/settings")).toBe(true));
    const panel = screen.getByRole("dialog", { name: "Assistant" });
    await waitFor(() => expect(within(panel).getByTitle(/this assistant uses/)).toBeEmptyDOMElement());
  });

  it("shows starter chips until the first message, then the conversation", async () => {
    model(textRound("Here is the list."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    expect(within(panel).getByText(/Ask me how something works/)).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    expect(await within(panel).findByText("Here is the list.")).toBeInTheDocument();
    expect(within(panel).queryByText(/Ask me how something works/)).not.toBeInTheDocument();
    expect(within(panel).getByText("What can I do here?", { selector: ".sa-msg.user" })).toBeInTheDocument();
  });
});

describe("SideAgent request and streaming", () => {
  it("sends the user message, the current page and the browser tools to /api/sidebot", async () => {
    nav.path = "/mcp/";
    const routes = model(textRound("ok"));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "Open MCP servers" }));
    await within(panel).findByText("ok");

    const post = routes.calls.find((c) => c.method === "POST")!;
    expect(post.url.pathname).toBe("/api/sidebot");
    expect(post.body.messages.at(-1)).toMatchObject({ role: "user", content: "Take me to MCP servers." });
    expect(post.body.context).toEqual([{ description: "current page", value: "/mcp/" }]);
    expect(post.body.tools.map((t: { name: string }) => t.name)).toEqual(["navigate", "start_tour", "explain"]);
  });

  it("sends a typed message when the Send button is clicked", async () => {
    const routes = model(textRound("Clicked send."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.type(within(panel).getByRole("textbox", { name: "Message the assistant" }), "Ping");
    await user.click(within(panel).getByRole("button", { name: "Send" }));
    expect(await within(panel).findByText("Clicked send.")).toBeInTheDocument();
    expect(routes.calls.find((c) => c.method === "POST")!.body.messages.at(-1).content).toBe("Ping");
  });

  it("streams assistant text deltas into one bubble", async () => {
    model(textRound("Hel", "lo ", "there"));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    expect(await within(panel).findByText("Hello there")).toHaveClass("sa-msg", "assistant");
  });

  it("sends a typed message on Enter, clears the draft, and keeps Shift+Enter as a newline", async () => {
    const routes = model(textRound("Typed reply"));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    const box = within(panel).getByRole("textbox", { name: "Message the assistant" });
    const send = within(panel).getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();

    await user.type(box, "Where is MCP");
    expect(send).toBeEnabled();
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(box).toHaveValue("Where is MCP\n");
    expect(routes.calls.filter((c) => c.method === "POST")).toHaveLength(0);

    await user.keyboard("more{Enter}");
    expect(await within(panel).findByText("Typed reply")).toBeInTheDocument();
    expect(box).toHaveValue("");
    const post = routes.calls.find((c) => c.method === "POST")!;
    expect(post.body.messages.at(-1).content).toBe("Where is MCP\nmore");
  });

  it("does not send a blank draft from the button", async () => {
    const routes = model();
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.type(within(panel).getByRole("textbox", { name: "Message the assistant" }), "   ");
    expect(within(panel).getByRole("button", { name: "Send" })).toBeDisabled();
    expect(routes.calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("shows a working indicator while the model is answering and disables sending", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    routeFetch({
      "GET /api/settings": () => settingsOk(),
      "POST /api/sidebot": async () => {
        await gate;
        return sseResponse([frame({ type: "RUN_STARTED", threadId: "t", runId: "r" }) + frame({ type: "RUN_FINISHED", threadId: "t", runId: "r" })]);
      },
    });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "Show me around" }));
    expect(await within(panel).findByText("working…")).toBeInTheDocument();
    await act(async () => release());
    await waitFor(() => expect(within(panel).queryByText("working…")).not.toBeInTheDocument());
  });
});

describe("SideAgent tool calls", () => {
  it("runs a navigate call, feeds the result back to the model and shows the note", async () => {
    const routes = model(toolRound("navigate", { page: "/mcp/" }), textRound("Opened MCP for you."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "Open MCP servers" }));

    expect(await within(panel).findByText("Opened MCP for you.")).toBeInTheDocument();
    expect(nav.push).toHaveBeenCalledWith("/mcp/");
    expect(within(panel).getByText("navigate: opened /mcp/", { selector: ".sa-msg.note" })).toBeInTheDocument();

    const posts = routes.calls.filter((c) => c.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[1].body.messages.at(-1)).toMatchObject({ role: "tool", toolCallId: "call-1", content: "opened /mcp/" });
  });

  it("reports an unknown page to the model without navigating", async () => {
    const routes = model(toolRound("navigate", { page: "/nope/" }), textRound("Sorry."));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "Show me around" }));
    expect(await within(panel).findByText("Sorry.")).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
    expect(within(panel).getByText("navigate: unknown page /nope/")).toBeInTheDocument();
    const posts = routes.calls.filter((c) => c.method === "POST");
    expect(posts[1].body.messages.at(-1).content).toBe("unknown page /nope/");
  });

  it("starts a tour by saving its state and announcing it", async () => {
    model(toolRound("start_tour", { tour: "mcp" }), textRound("Started."));
    const listener = vi.fn();
    window.addEventListener("as:tour-start", listener);
    const user = userEvent.setup();
    try {
      render(<SideAgent />);
      const panel = await openPanel(user);
      await user.click(within(panel).getByRole("button", { name: "Show me around" }));
      await within(panel).findByText("Started.");
    } finally {
      window.removeEventListener("as:tour-start", listener);
    }
    expect(listener).toHaveBeenCalledTimes(1);
    expect(JSON.parse(localStorage.getItem(STATE_KEY)!)).toEqual({ tour: "mcp", index: 0 });
  });

  it("highlights an on-page element with the explain tool", async () => {
    model(toolRound("explain", { target: "composer", title: "Type here", text: "Write your request." }), textRound("Look at the box."));
    const user = userEvent.setup();
    render(
      <>
        <div data-tour="composer">composer</div>
        <SideAgent />
      </>,
    );
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    expect(await within(panel).findByText("explain: showed the card for composer")).toBeInTheDocument();
    expect(await screen.findByText("Write your request.")).toBeInTheDocument();
    expect(screen.getByText("Type here")).toBeInTheDocument();
  });
});

describe("SideAgent error paths", () => {
  it("shows a run error event as an error bubble", async () => {
    model(() => sseResponse([frame({ type: "RUN_STARTED", threadId: "t", runId: "r" }) + frame({ type: "RUN_ERROR", message: "model is down", code: "X" })]));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    expect(await within(panel).findByText("model is down", { selector: ".sa-msg.error" })).toBeInTheDocument();
    expect(within(panel).queryByText("working…")).not.toBeInTheDocument();
  });

  it("shows an HTTP failure from the endpoint and re-enables sending", async () => {
    model(() => textResponse("boom", 500));
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    expect(await within(panel).findByText("HTTP 500: boom", { selector: ".sa-msg.error" })).toBeInTheDocument();
    expect(within(panel).queryByText("working…")).not.toBeInTheDocument();
    await user.type(within(panel).getByRole("textbox", { name: "Message the assistant" }), "again");
    expect(within(panel).getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("stops after four model rounds even if the model keeps calling tools", async () => {
    const routes = model(
      toolRound("navigate", { page: "/" }, "c1"),
      toolRound("navigate", { page: "/" }, "c2"),
      toolRound("navigate", { page: "/" }, "c3"),
      toolRound("navigate", { page: "/" }, "c4"),
      toolRound("navigate", { page: "/" }, "c5"),
    );
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    await waitFor(() => expect(within(panel).queryByText("working…")).not.toBeInTheDocument());
    expect(routes.calls.filter((c) => c.method === "POST")).toHaveLength(4);
    expect(nav.push).toHaveBeenCalledTimes(4);
  });

  it("aborts the in-flight request when unmounted", async () => {
    let signal: AbortSignal | undefined;
    routeFetch({
      "GET /api/settings": () => settingsOk(),
      "POST /api/sidebot": (init) => {
        signal = init?.signal ?? undefined;
        return new Promise<Response>((_, reject) => {
          signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        });
      },
    });
    const user = userEvent.setup();
    const { unmount } = render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal!.aborted).toBe(false);
    unmount();
    expect(signal!.aborted).toBe(true);
  });
});

describe("runTool", () => {
  it("exposes the navigate, start_tour and explain tools with the known routes and targets", () => {
    expect(TOOLS.map((t) => t.name)).toEqual(["navigate", "start_tour", "explain"]);
    expect(ROUTES.map((r) => r.path)).toContain("/settings/");
    expect(TARGETS).toEqual(expect.arrayContaining(["sidebar", "composer", "assistant"]));
    expect(new Set(TARGETS).size).toBe(TARGETS.length);
  });

  it("navigates only to known pages", () => {
    const go = vi.fn();
    expect(runTool("navigate", { page: "/billing/" }, go)).toBe("opened /billing/");
    expect(go).toHaveBeenCalledWith("/billing/");
    expect(runTool("navigate", { page: "/secret/" }, go)).toBe("unknown page /secret/");
    expect(runTool("navigate", {}, go)).toBe("unknown page ");
    expect(go).toHaveBeenCalledTimes(1);
  });

  it("starts only known tours", () => {
    const listener = vi.fn();
    window.addEventListener("as:tour-start", listener);
    try {
      const go = vi.fn();
      expect(runTool("start_tour", { tour: "chat" }, go)).toBe("started the chat tour");
      expect(runTool("start_tour", { tour: "nope" }, go)).toBe("unknown tour nope");
    } finally {
      window.removeEventListener("as:tour-start", listener);
    }
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("reports missing targets and rejects malformed selectors", () => {
    const go = vi.fn();
    expect(runTool("explain", { target: "sidebar", text: "x" }, go)).toBe("sidebar is not on this page");
    expect(runTool("explain", { target: 'a"b', text: "x" }, go)).toMatch(/^failed: /);
  });

  it("answers unknown tool names", () => {
    expect(runTool("delete_everything", {}, vi.fn())).toBe("unknown tool delete_everything");
  });

  it("highlights a target that exists on the page", () => {
    const el = document.createElement("div");
    el.setAttribute("data-tour", "composer");
    document.body.appendChild(el);
    try {
      expect(runTool("explain", { target: "composer", title: "Box", text: "Type here" }, vi.fn())).toBe("showed the card for composer");
      expect(screen.getByText("Type here")).toBeInTheDocument();
    } finally {
      el.remove();
    }
  });
});
