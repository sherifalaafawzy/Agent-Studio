import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SideAgent, { runTool } from "@/components/SideAgent";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => {
  const push = vi.fn();
  return { path: "/" as string | null, push, router: { push } };
});
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useRouter: () => nav.router,
}));

const frame = (e: Record<string, unknown>) => `data: ${JSON.stringify(e)}\n\n`;
const finished = () => sseResponse([
  frame({ type: "RUN_STARTED", threadId: "t", runId: "r" }) + frame({ type: "RUN_FINISHED", threadId: "t", runId: "r" }),
]);
const settings = (provider: string | null, model: string | null, enabled = true) => () =>
  jsonResponse({ sidebot_enabled: enabled, sidebot_resolved: { provider, model } });

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Open the assistant" }));
  return screen.getByRole("dialog", { name: "Assistant" });
}

beforeEach(() => {
  nav.path = "/";
  nav.push.mockReset();
});

describe("SideAgent: model label", () => {
  it("shows only the provider when the model name is unknown", async () => {
    routeFetch({ "GET /api/settings": settings("anthropic", null) });
    const user = userEvent.setup();
    render(<SideAgent />);
    await openPanel(user);
    const label = await screen.findByText("anthropic");
    expect(label).toHaveClass("sa-model");
    expect(label.textContent).toBe("anthropic");
  });
});

describe("SideAgent: current page context", () => {
  it("falls back to / when the router has no pathname", async () => {
    nav.path = null;
    const routes = routeFetch({ "GET /api/settings": settings("a", "b"), "POST /api/sidebot": () => finished() });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    await waitFor(() => expect(routes.calls.some((c) => c.method === "POST")).toBe(true));
    expect(routes.calls.find((c) => c.method === "POST")!.body.context[0]).toEqual({ description: "current page", value: "/" });
  });
});

describe("SideAgent: composing", () => {
  it("ignores Enter on an empty or whitespace-only draft", async () => {
    const routes = routeFetch({ "GET /api/settings": settings("a", "b") });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    const box = within(panel).getByRole("textbox", { name: "Message the assistant" });
    await user.click(box);
    await user.keyboard("{Enter}");
    await user.type(box, "   {Enter}");
    expect(routes.calls.filter((c) => c.method === "POST")).toHaveLength(0);
    expect(within(panel).getByText(/Ask me how something works/)).toBeInTheDocument();
    expect(within(panel).queryByText("working…")).not.toBeInTheDocument();
  });

  it("does not start a second request while one is running (Enter clears the draft but sends nothing)", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const routes = routeFetch({
      "GET /api/settings": settings("a", "b"),
      "POST /api/sidebot": async () => { await gate; return finished(); },
    });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "Show me around" }));
    await within(panel).findByText("working…");

    const box = within(panel).getByRole("textbox", { name: "Message the assistant" });
    await user.type(box, "second{Enter}");
    expect(box).toHaveValue("");
    expect(within(panel).queryByText("second")).not.toBeInTheDocument();
    expect(routes.calls.filter((c) => c.method === "POST")).toHaveLength(1);

    await act(async () => release());
    await waitFor(() => expect(within(panel).queryByText("working…")).not.toBeInTheDocument());
    expect(routes.calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("Escape does nothing in the composer (the panel stays open and the draft is kept)", async () => {
    routeFetch({ "GET /api/settings": settings("a", "b") });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    const box = within(panel).getByRole("textbox", { name: "Message the assistant" });
    await user.type(box, "keep me{Escape}");
    expect(screen.getByRole("dialog", { name: "Assistant" })).toBeInTheDocument();
    expect(box).toHaveValue("keep me");
  });
});

describe("SideAgent: scrolling", () => {
  it("scrolls the end of the conversation into view when it opens and when messages arrive", async () => {
    const spy = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
    routeFetch({
      "GET /api/settings": settings("a", "b"),
      "POST /api/sidebot": () => sseResponse([
        frame({ type: "RUN_STARTED", threadId: "t", runId: "r" })
        + frame({ type: "TEXT_MESSAGE_START", messageId: "m", role: "assistant" })
        + frame({ type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "hi" })
        + frame({ type: "TEXT_MESSAGE_END", messageId: "m" })
        + frame({ type: "RUN_FINISHED", threadId: "t", runId: "r" }),
      ]),
    });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    expect(spy).toHaveBeenCalledWith({ block: "end" });
    const afterOpen = spy.mock.calls.length;
    await user.click(within(panel).getByRole("button", { name: "What can I do here?" }));
    await within(panel).findByText("hi");
    expect(spy.mock.calls.length).toBeGreaterThan(afterOpen);
  });
});

describe("SideAgent: ids without crypto.randomUUID", () => {
  it("still sends the user message with a generated id", async () => {
    const routes = routeFetch({ "GET /api/settings": settings("a", "b"), "POST /api/sidebot": () => finished() });
    const user = userEvent.setup();
    render(<SideAgent />);
    const panel = await openPanel(user);
    // Older browsers: crypto exists but has no randomUUID; the component falls back to Math.random.
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    await user.click(within(panel).getByRole("button", { name: "Show me around" }));
    await waitFor(() => expect(routes.calls.some((c) => c.method === "POST")).toBe(true));
    const msg = routes.calls.find((c) => c.method === "POST")!.body.messages.at(-1);
    expect(msg).toMatchObject({ role: "user", content: "Show me around the app." });
    expect(msg.id).toMatch(/^[a-z0-9]+$/);
    expect(msg.id).not.toMatch(/-/);
  });
});

describe("runTool: missing arguments", () => {
  it("start_tour without a tour name reports an unknown tour", () => {
    expect(runTool("start_tour", {}, vi.fn())).toBe("unknown tour ");
  });

  it("explain without a target reports it is not on the page", () => {
    expect(runTool("explain", {}, vi.fn())).toBe(" is not on this page");
  });

  it("explain without a title or text still shows a card with no title", () => {
    const el = document.createElement("div");
    el.setAttribute("data-tour", "composer");
    document.body.appendChild(el);
    try {
      expect(runTool("explain", { target: "composer" }, vi.fn())).toBe("showed the card for composer");
      const pop = document.querySelector(".driver-popover") as HTMLElement;
      expect(pop).not.toBeNull();
      expect((pop.querySelector(".driver-popover-title") as HTMLElement).style.display).toBe("none");
      expect((pop.querySelector(".driver-popover-description") as HTMLElement).style.display).toBe("none");
      // an empty title is treated the same as none
      expect(runTool("explain", { target: "composer", title: "", text: "Body" }, vi.fn())).toBe("showed the card for composer");
      const pop2 = document.querySelector(".driver-popover") as HTMLElement;
      expect((pop2.querySelector(".driver-popover-title") as HTMLElement).style.display).toBe("none");
      expect(within(pop2).getByText("Body")).toBeInTheDocument();
    } finally {
      el.remove();
      document.querySelectorAll(".driver-popover, .driver-overlay").forEach((n) => n.remove());
    }
  });
});
