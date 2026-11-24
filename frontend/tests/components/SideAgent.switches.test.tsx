import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SideAgent, { ROUTES, runTool } from "@/components/SideAgent";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => { const push = vi.fn(); return { push, router: { push } }; });
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => nav.router }));

const frame = (e: Record<string, unknown>) => `data: ${JSON.stringify(e)}\n\n`;
const text = (t: string) => () => sseResponse([[{ type: "RUN_STARTED", threadId: "t", runId: "r" }, { type: "TEXT_MESSAGE_START", messageId: "m", role: "assistant" },
  { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: t }, { type: "TEXT_MESSAGE_END", messageId: "m" }, { type: "RUN_FINISHED", threadId: "t", runId: "r" }].map(frame).join("")]);
const tool = (name: string, args: object) => () => sseResponse([[{ type: "RUN_STARTED", threadId: "t", runId: "r" }, { type: "TOOL_CALL_START", toolCallId: "c1", toolCallName: name, parentMessageId: "m" },
  { type: "TOOL_CALL_ARGS", toolCallId: "c1", delta: JSON.stringify(args) }, { type: "TOOL_CALL_END", toolCallId: "c1" }, { type: "RUN_FINISHED", threadId: "t", runId: "r" }].map(frame).join("")]);

const settings = (tools?: { name: string; enabled: boolean }[]) => jsonResponse({ sidebot_enabled: true, sidebot_resolved: { provider: "p", model: "m" }, ...(tools ? { sidebot_tools: tools } : {}) });
beforeEach(() => nav.push.mockReset());

function serve(tools: { name: string; enabled: boolean }[] | undefined, ...rounds: (() => Response)[]) {
  const q = [...rounds];
  return routeFetch({ "GET /api/settings": () => settings(tools), "POST /api/sidebot": () => q.shift()!() });
}
const ask = async (msg = "hello") => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open the assistant" }));
  await user.type(screen.getByLabelText("Message the assistant"), msg + "{Enter}");
};

describe("switched-off assistant tools", () => {
  it("the set-up guide is a page the assistant can open", () => {
    expect(ROUTES).toContainEqual({ path: "/setup/", label: "Set-up guide" });
    const go = vi.fn();
    expect(runTool("navigate", { page: "/setup/" }, go)).toBe("opened /setup/");
    expect(go).toHaveBeenCalledWith("/setup/");
  });

  it("runTool refuses a switched-off tool before doing anything", () => {
    const go = vi.fn();
    expect(runTool("navigate", { page: "/" }, go, ["navigate"])).toBe("navigate is switched off in Settings");
    expect(go).not.toHaveBeenCalled();
    expect(runTool("navigate", { page: "/" }, go, ["explain"])).toBe("opened /");
  });

  it("does not offer switched-off tools to the model", async () => {
    const { calls } = serve([{ name: "navigate", enabled: false }, { name: "start_tour", enabled: true }, { name: "explain", enabled: true }], text("ok"));
    render(<SideAgent />);
    await ask();
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/sidebot")).toBe(true));
    const sent = calls.find((c) => c.url.pathname === "/api/sidebot")!.body;
    expect(sent.tools.map((t: { name: string }) => t.name)).toEqual(["start_tour", "explain", "guide_me", "read_page", "open_docs", "search_docs", "set_field", "fill_form", "open_canvas"]);
  });

  it("offers every tool when the settings do not list any (an older server)", async () => {
    const { calls } = serve(undefined, text("ok"));
    render(<SideAgent />);
    await ask();
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/sidebot")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/sidebot")!.body.tools.map((t: { name: string }) => t.name)).toEqual(["navigate", "start_tour", "explain", "guide_me", "read_page", "open_docs", "search_docs", "set_field", "fill_form", "open_canvas"]);
  });

  it("refuses in the browser too if the model calls a switched-off tool anyway, and tells the model", async () => {
    const { calls } = serve([{ name: "navigate", enabled: false }], tool("navigate", { page: "/settings/" }), text("I could not open it."));
    render(<SideAgent />);
    await ask("take me to settings");
    expect(await screen.findByText("navigate: navigate is switched off in Settings")).toBeInTheDocument();
    expect(nav.push).not.toHaveBeenCalled();
    await screen.findByText("I could not open it.");
    const second = calls.filter((c) => c.url.pathname === "/api/sidebot")[1].body;
    expect(second.messages.at(-1)).toMatchObject({ role: "tool", content: "navigate is switched off in Settings" });
  });
});
