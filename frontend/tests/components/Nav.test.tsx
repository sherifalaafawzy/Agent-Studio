import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Nav, { Frame, MobileBar, toast } from "@/components/Nav";
import { jsonResponse, routeFetch } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));

type Row = { id: string; title: string; agent: string | null; provider: string; updated: number; running?: boolean };
const rows: Row[] = [
  { id: "a1", title: "Planning chat", agent: null, provider: "ollama", updated: 1, running: true },
  { id: "b2", title: "Review chat", agent: "engineer", provider: "openai", updated: 2 },
];

beforeEach(() => {
  nav.path = "/";
  nav.search = "";
  nav.push.mockReset();
});

function withSessions(list: Row[] = rows) {
  return routeFetch({ "GET /api/chat/sessions": () => jsonResponse(list) });
}

describe("toast()", () => {
  it("dispatches an as:toast event with text and tone", () => {
    const seen: any[] = [];
    window.addEventListener("as:toast", (e) => seen.push((e as CustomEvent).detail));
    toast("Saved");
    toast("Broken", "err");
    toast("Careful", "warn");
    expect(seen).toEqual([
      { text: "Saved", tone: "ok" },
      { text: "Broken", tone: "err" },
      { text: "Careful", tone: "warn" },
    ]);
  });
});

describe("Sidebar: the set-up guide", () => {
  it("has a set-up guide button in the footer and a palette entry", async () => {
    withSessions([]);
    render(<Nav />);
    expect(screen.getByRole("link", { name: "Set-up guide" })).toHaveAttribute("href", expect.stringMatching(/\/setup\/?$/));
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await userEvent.type(await screen.findByPlaceholderText("Go to a page or chat…"), "set-up");
    await userEvent.keyboard("{Enter}");
    expect(nav.push).toHaveBeenCalledWith("/setup/");
  });
});

describe("Sidebar: navigation", () => {
  it("lists every page grouped under Work, Build and Observe", () => {
    withSessions([]);
    render(<Nav />);
    for (const label of ["Dashboard", "Chat", "Runs", "External agents", "Agent Studio", "Skills", "MCP servers", "Memory", "Telemetry", "Billing", "Settings"]) {
      expect(screen.getByTitle(label)).toHaveAttribute("href");
    }
    expect(screen.getByText("Work")).toBeInTheDocument();
    expect(screen.getByText("Build")).toBeInTheDocument();
    expect(screen.getByText("Observe")).toBeInTheDocument();
  });

  it("highlights the link for the current page", () => {
    nav.path = "/runs/";
    withSessions([]);
    render(<Nav />);
    expect(screen.getByTitle("Runs")).toHaveClass("on");
    expect(screen.getByTitle("Chat")).not.toHaveClass("on");
  });

  it("highlights a section link for nested paths", () => {
    nav.path = "/settings/providers/";
    withSessions([]);
    render(<Nav />);
    expect(screen.getByTitle("Settings")).toHaveClass("on");
  });

  it("highlights Chat only on the root path", () => {
    withSessions([]);
    render(<Nav />);
    expect(screen.getByTitle("Chat")).toHaveClass("on");
  });

  it("starts a new chat with a fresh query and does not follow the link", async () => {
    withSessions([]);
    render(<Nav />);
    await userEvent.click(screen.getByTitle("New chat"));
    expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/\?new=\d+$/));
  });
});

describe("Sidebar: recent chats", () => {
  it("shows each chat with its running dot and provider tooltip", async () => {
    withSessions();
    render(<Nav />);
    const first = await screen.findByText("Planning chat");
    const link = first.closest("a")!;
    expect(link).toHaveAttribute("href", "/?s=a1");
    expect(link).toHaveAttribute("title", "plain chat · ollama");
    expect(link.querySelector(".run-dot")).not.toBeNull();
    const second = screen.getByText("Review chat").closest("a")!;
    expect(second).toHaveAttribute("title", "engineer · openai");
    expect(second.querySelector(".run-dot")).toBeNull();
  });

  it("marks the open chat as selected", async () => {
    nav.search = "s=b2";
    withSessions();
    render(<Nav />);
    const link = (await screen.findByText("Review chat")).closest("a")!;
    expect(link).toHaveClass("on");
    expect(screen.getByText("Planning chat").closest("a")).not.toHaveClass("on");
  });

  it("does not mark any chat on a non-root page", async () => {
    nav.path = "/runs/";
    nav.search = "s=b2";
    withSessions();
    render(<Nav />);
    await screen.findByText("Review chat");
    expect(screen.getByText("Review chat").closest("a")).not.toHaveClass("on");
  });

  it("shows a hint when there are no chats yet", async () => {
    withSessions([]);
    render(<Nav />);
    expect(await screen.findByText("Your chats will appear here.")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("scrolls the current page's link into view, because the page list scrolls on its own", async () => {
    const into = vi.fn();
    const proto = HTMLElement.prototype as unknown as { scrollIntoView?: unknown };
    const had = proto.scrollIntoView;
    proto.scrollIntoView = into;
    try {
      nav.path = "/runs/";
      withSessions([]);
      render(<Nav />);
      await screen.findByText("Connected");
      expect(into).toHaveBeenCalledWith({ block: "nearest" });
      expect((into.mock.instances[0] as HTMLElement).classList.contains("on")).toBe(true);
    } finally { proto.scrollIntoView = had; }
  });

  it("keeps the status and the shortcut on one line and the tools on the next, and names the theme button", async () => {
    withSessions([]);
    render(<Nav />);
    await screen.findByText("Connected");
    const status = document.querySelector(".sb-foot .sb-status")!, tools = document.querySelector(".sb-foot .sb-tools")!;
    expect(status).toHaveTextContent("Connected");
    expect(status.querySelector("kbd")).toHaveTextContent("Ctrl K");
    expect(tools.querySelectorAll("a, button").length).toBe(5);                                     // bell, set-up guide, docs, tour, theme
    expect(tools).toContainElement(screen.getByRole("button", { name: /^Theme: / }));
  });

  it("reports the backend as offline when the list cannot be loaded", async () => {
    routeFetch({ "GET /api/chat/sessions": () => { throw new TypeError("Failed to fetch"); } });
    render(<Nav />);
    expect(await screen.findByText("The server is not answering. It is tried again every 20 seconds.")).toBeInTheDocument();
    expect(screen.getByText("Offline")).toBeInTheDocument();
    expect(document.querySelector(".sb-foot .dot.bad")).not.toBeNull();
  });

  it("shows a pending connection state before the first response", () => {
    routeFetch({ "GET /api/chat/sessions": () => new Promise<Response>(() => {}) });
    render(<Nav />);
    expect(screen.getByText("…")).toBeInTheDocument();
  });

  it("reloads when an as:sessions event is raised", async () => {
    const { fn } = withSessions([]);
    render(<Nav />);
    await screen.findByText("Your chats will appear here.");
    const before = fn.mock.calls.length;
    act(() => { window.dispatchEvent(new Event("as:sessions")); });
    await waitFor(() => expect(fn.mock.calls.length).toBeGreaterThan(before));
  });

  it("refreshes on the 20 second timer", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const { fn } = withSessions([]);
      render(<Nav />);
      await act(async () => { await Promise.resolve(); });
      const before = fn.mock.calls.length;
      await act(async () => { vi.advanceTimersByTime(20000); });
      expect(fn.mock.calls.length).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("deletes a chat after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = routeFetch({
      "GET /api/chat/sessions": () => jsonResponse(rows),
      "DELETE /api/chat/sessions/b2": () => jsonResponse({ ok: true }),
    });
    render(<Nav />);
    const link = (await screen.findByText("Review chat")).closest("a")!;
    fireEvent.click(within(link).getByTitle("Delete chat"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(window.confirm).toHaveBeenCalledWith("Delete this chat and its saved agent runs?");
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("leaves the chat when deleting the open one", async () => {
    nav.search = "s=b2";
    vi.spyOn(window, "confirm").mockReturnValue(true);
    routeFetch({
      "GET /api/chat/sessions": () => jsonResponse(rows),
      "DELETE /api/chat/sessions/b2": () => jsonResponse({ ok: true }),
    });
    render(<Nav />);
    const link = (await screen.findByText("Review chat")).closest("a")!;
    fireEvent.click(within(link).getByTitle("Delete chat"));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith(expect.stringMatching(/^\/\?new=/)));
  });

  it("keeps the chat when the user cancels the delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls } = withSessions();
    render(<Nav />);
    const link = (await screen.findByText("Review chat")).closest("a")!;
    fireEvent.click(within(link).getByTitle("Delete chat"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("ignores a failed delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    routeFetch({
      "GET /api/chat/sessions": () => jsonResponse(rows),
      "DELETE /api/chat/sessions/b2": () => jsonResponse({ detail: "locked" }, { status: 409 }),
    });
    render(<Nav />);
    const link = (await screen.findByText("Review chat")).closest("a")!;
    fireEvent.click(within(link).getByTitle("Delete chat"));
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(screen.getByText("Review chat")).toBeInTheDocument();
  });
});

describe("Sidebar: collapse and theme", () => {
  it("collapses and expands, remembering the choice", async () => {
    withSessions([]);
    render(<Nav />);
    const sb = document.querySelector("aside.sb")!;
    await userEvent.click(screen.getByTitle("Collapse sidebar"));
    expect(sb).toHaveClass("min");
    expect(localStorage.getItem("as-sb-min")).toBe("1");
    await userEvent.click(screen.getByTitle("Expand sidebar"));
    expect(sb).not.toHaveClass("min");
    expect(localStorage.getItem("as-sb-min")).toBe("0");
  });

  it("restores a collapsed sidebar from storage", () => {
    localStorage.setItem("as-sb-min", "1");
    withSessions([]);
    render(<Nav />);
    expect(document.querySelector("aside.sb")).toHaveClass("min");
  });

  it("cycles system, light, dark and persists the theme", async () => {
    withSessions([]);
    render(<Nav />);
    const btn = screen.getByTitle("Theme: system");
    await userEvent.click(btn);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(localStorage.getItem("as-theme")).toBe("light");
    await userEvent.click(screen.getByTitle("Theme: light"));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    await userEvent.click(screen.getByTitle("Theme: dark"));
    expect(document.documentElement).not.toHaveAttribute("data-theme");
    expect(localStorage.getItem("as-theme")).toBe("system");
  });

  it("starts with the saved theme", async () => {
    localStorage.setItem("as-theme", "dark");
    withSessions([]);
    render(<Nav />);
    expect(await screen.findByTitle("Theme: dark")).toBeInTheDocument();
  });

  it("still applies the theme when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    withSessions([]);
    render(<Nav />);
    await userEvent.click(screen.getByTitle("Theme: system"));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });
});

describe("Command palette", () => {
  function openPalette() {
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    return screen.getByPlaceholderText("Go to a page or chat…");
  }
  /** The palette's result list (the sidebar repeats some labels, so queries must be scoped). */
  const palette = () => document.querySelector(".palette-list") as HTMLElement;

  it("opens with Ctrl+K and lists the new-chat action and pages", async () => {
    withSessions();
    render(<Nav />);
    await screen.findByText("Planning chat");
    openPalette();
    const list = within(palette());
    expect(list.getByText("New chat")).toBeInTheDocument();
    expect(list.getByText("Live")).toBeInTheDocument();
    expect(list.getAllByText("page").length).toBeGreaterThan(0);
  });

  it("finds recent chats by title and labels running ones", async () => {
    withSessions();
    render(<Nav />);
    await screen.findByText("Planning chat");
    const input = openPalette();
    await userEvent.type(input, "planning");
    expect(within(palette()).getByText("running chat")).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, "review");
    expect(within(palette()).getByText("chat")).toBeInTheDocument();
  });

  it("opens with Cmd+K and toggles closed on a second press", () => {
    withSessions([]);
    render(<Nav />);
    fireEvent.keyDown(window, { key: "K", metaKey: true });
    expect(screen.getByPlaceholderText("Go to a page or chat…")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.queryByPlaceholderText("Go to a page or chat…")).toBeNull();
  });

  it("filters as you type and shows an empty state", async () => {
    withSessions([]);
    render(<Nav />);
    const input = openPalette();
    await userEvent.type(input, "bill");
    expect(within(palette()).getByText("Billing")).toBeInTheDocument();
    expect(within(palette()).queryByText("Telemetry")).toBeNull();
    await userEvent.clear(input);
    await userEvent.type(input, "zzzz");
    expect(screen.getByText("No matches.")).toBeInTheDocument();
  });

  it("moves the highlight with arrow keys and navigates on Enter", async () => {
    withSessions([]);
    render(<Nav />);
    const input = openPalette();
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    // order with no query: New chat, Dashboard, Chat, ...; two downs highlight Chat ("/")
    fireEvent.keyDown(input, { key: "Enter" });
    expect(nav.push).toHaveBeenCalledWith("/");
    expect(screen.queryByPlaceholderText("Go to a page or chat…")).toBeNull();
  });

  it("does nothing on Enter when nothing matches", async () => {
    withSessions([]);
    render(<Nav />);
    const input = openPalette();
    await userEvent.type(input, "qqqq");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("navigates when a result is clicked", async () => {
    withSessions();
    render(<Nav />);
    await screen.findByText("Planning chat");
    const input = openPalette();
    await userEvent.type(input, "review");
    await userEvent.click(within(palette()).getByText("Review chat"));
    expect(nav.push).toHaveBeenCalledWith("/?s=b2");
  });

  it("highlights the item under the pointer", () => {
    withSessions([]);
    render(<Nav />);
    openPalette();
    // re-query after each change: the "New chat" row's key includes Date.now(), so React re-mounts it on render
    const item = (n: number) => document.querySelectorAll(".palette-item")[n];
    expect(item(0)).toHaveClass("on");
    fireEvent.mouseEnter(item(2));
    expect(item(2)).toHaveClass("on");
    expect(item(0)).not.toHaveClass("on");
  });

  it("closes on Escape and on a click outside the box", async () => {
    withSessions([]);
    render(<Nav />);
    openPalette();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByPlaceholderText("Go to a page or chat…")).toBeNull();
    openPalette();
    fireEvent.click(document.querySelector(".palette")!);
    expect(screen.queryByPlaceholderText("Go to a page or chat…")).toBeNull();
  });

  it("keeps the palette open when clicking inside its box", () => {
    withSessions([]);
    render(<Nav />);
    const input = openPalette();
    fireEvent.click(input);
    expect(input).toBeInTheDocument();
  });
});

describe("Toasts", () => {
  it("shows a toast with its tone and removes it after its timeout", async () => {
    withSessions([]);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      render(<Nav />);
      act(() => { toast("Saved file"); });
      const t = screen.getByText("Saved file");
      expect(t).toHaveClass("toast", "ok");
      act(() => { vi.advanceTimersByTime(3500); });
      expect(screen.queryByText("Saved file")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps error toasts a little longer", () => {
    withSessions([]);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      render(<Nav />);
      act(() => { toast("Failure", "err"); });
      act(() => { vi.advanceTimersByTime(3500); });
      expect(screen.getByText("Failure")).toHaveClass("err");
      act(() => { vi.advanceTimersByTime(3500); });
      expect(screen.queryByText("Failure")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows at most four toasts at once", () => {
    withSessions([]);
    render(<Nav />);
    act(() => {
      for (const n of [1, 2, 3, 4, 5]) toast(`msg ${n}`);
    });
    expect(screen.queryByText("msg 1")).toBeNull();
    for (const n of [2, 3, 4, 5]) expect(screen.getByText(`msg ${n}`)).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

describe("MobileBar", () => {
  it("toggles the drawer class from the menu button", async () => {
    render(<MobileBar />);
    await userEvent.click(screen.getByLabelText("Open menu"));
    expect(document.body).toHaveClass("nav-open");
    await userEvent.click(screen.getByLabelText("Open menu"));
    expect(document.body).not.toHaveClass("nav-open");
  });

  it("closes the drawer when the page changes", () => {
    const { rerender } = render(<MobileBar />);
    document.body.classList.add("nav-open");
    nav.path = "/skills/";
    rerender(<MobileBar />);
    expect(document.body).not.toHaveClass("nav-open");
  });

  it("falls back to the root path when none is known", () => {
    (nav as any).path = undefined;
    render(<MobileBar />);
    expect(screen.getByText("Agent Studio")).toBeInTheDocument();
    nav.path = "/";
  });
});

describe("Frame", () => {
  it("gives the chat and runs pages a full-width frame", () => {
    nav.path = "/";
    const { container, unmount } = render(<Frame><p>chat body</p></Frame>);
    expect(container.querySelector(".page")).toBeNull();
    unmount();
    nav.path = "/runs/";
    const runs = render(<Frame><p>run body</p></Frame>);
    expect(runs.container.querySelector(".page")).toBeNull();
  });

  it("wraps other pages in the centred reading column", () => {
    nav.path = "/billing/";
    const { container } = render(<Frame><p>bill</p></Frame>);
    expect(container.querySelector(".page > p")?.textContent).toBe("bill");
  });

  it("falls back to the root path when none is known", () => {
    (nav as any).path = null;
    const { container } = render(<Frame><p>x</p></Frame>);
    expect(container.querySelector(".page")).toBeNull();
    nav.path = "/";
  });
});
