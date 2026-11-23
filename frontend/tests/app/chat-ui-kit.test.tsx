import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Page from "@/app/page";
import Markdown from "@/components/Markdown";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

const frame = (e: object) => `data: ${JSON.stringify(e)}\n\n`;
const KPI = { type: "card.kpi", version: 1, label: "Users", value: 12 };
const base = (extra: Record<string, any> = {}) => routeFetch({
  "GET /api/providers": () => jsonResponse([{ id: "anthropic", kind: "anthropic", label: "Anthropic (Claude)", enabled: true }]),
  "GET /api/agents": () => jsonResponse([{ id: "engineer", name: "Engineer", tier: "pro", description: "x", sub_agents: [], can_spawn: false }]),
  "GET /api/providers/anthropic/models": () => jsonResponse({ provider: "anthropic", source: "live", error: null, models: [{ id: "claude-x", label: "Claude X", input_per_m: 3, output_per_m: 15 }], cached: false }),
  "GET /api/voice/settings": () => jsonResponse({ configured: false, key_source: null, key_hint: null, voice_id: "", tts_model: "", stt_model: "", auto_read: false, dictation: false, char_cap: 0 }),
  "GET /api/uikit/config": () => jsonResponse({ basemap: { kind: "none" } }),
  ...extra,
});

describe("rich components in a chat answer", () => {
  it("draws a component the agent showed while streaming, and keeps it in the final message", async () => {
    base({ "POST /api/chat/stream": () => sseResponse([
      frame({ type: "session", session_id: "s1", agent: "engineer", provider: "anthropic", trace_id: "t" }), frame({ type: "text", delta: "Here it is" }),
      frame({ type: "ui", spec: KPI }),
      frame({ type: "done", session_id: "s1", reply: "Here it is", model_calls: [], usage: {}, steps: [], runs: [], uis: [KPI] })]) });
    render(<Page />);
    await screen.findByRole("option", { name: "Claude X (claude-x)" });
    await userEvent.type(screen.getByRole("textbox"), "show me");
    await userEvent.keyboard("{Enter}");
    expect((await screen.findAllByTestId("card"))[0]).toHaveTextContent("Users");
    await waitFor(() => expect(screen.getAllByTestId("card")).toHaveLength(1));
  });
  it("restores components from a saved conversation", async () => {
    nav.search = "s=s9";
    base({
      "GET /api/chat/sessions/s9": () => jsonResponse({ session: { id: "s9", title: "Old chat", provider: "anthropic", agent: "engineer" }, active_job: null,
        messages: [{ role: "user", text: "q" }, { role: "bot", text: "a", meta: { uis: [KPI], calls: [], usage: {} } }] }),
      "GET /api/chat/sessions/s9/context": () => jsonResponse({ model: "claude-x", window_tokens: 1000, used_tokens: 1, percent: 0.1, level: "ok", source: "Live", messages: 2, summarized: false, summarized_messages: 0, messages_after_summary: 2 }),
    });
    render(<Page />);
    expect(await screen.findByTestId("card")).toHaveTextContent("Users");
  });
});

describe("```ui blocks in Markdown", () => {
  it("draws a valid block and explains an invalid one", () => {
    base();
    render(<Markdown text={"Here:\n\n```ui\n" + JSON.stringify(KPI) + "\n```\n\n```ui\n{\"type\": \"chart.bar\"}\n```\n"} />);
    expect(screen.getAllByTestId("card")).toHaveLength(1); expect(screen.getByTestId("ui-fallback")).toHaveTextContent(/chart\.bar needs/);
  });
});
