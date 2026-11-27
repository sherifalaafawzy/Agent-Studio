import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RunsPage from "@/app/runs/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/runs/",
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;
const sessions = [
  { id: "sess-1", title: "Refactor auth", provider: "openai", agent: "engineer", model: "gpt-x", workspace: "C:/app", created: now() - 3600, updated: now() - 60, messages: 4 },
  { id: "sess-2", title: "Plain question", provider: "ollama", agent: null, model: null, workspace: null, created: now() - 9000, updated: now() - 9000, messages: 2 },
];
const root = {
  run_id: "root", parent: null, agent: "engineer", spawned: false, task: "Refactor the auth module", status: "done",
  started: now() - 100, ended: now() - 80, steps: [
    { agent: "engineer", type: "call", tool: "read_file", args: "auth.ts", ts: now() - 95 },
    { agent: "engineer", type: "result", tool: "read_file", args: "auth.ts", ts: now() - 94, ms: 120, ok: true, result: "export function login() {}" },
  ],
  texts: ["Done refactoring."], calls: [{ agent: "engineer", provider: "openai", model: "gpt-x", cost_usd: 0.0021, input_tokens: 800, output_tokens: 120, t0: now() - 98, t1: now() - 90 }],
  result: "Auth refactored", cost_usd: 0.0021,
};
const reviewer = {
  run_id: "r1", parent: "root", agent: "team__reviewer", spawned: true, task: "Review the diff", status: "done",
  started: now() - 85, ended: now() - 82, steps: [], texts: [], calls: [], result: "LGTM", cost_usd: 0.0003,
  definition: { name: "Reviewer", parent: "engineer", instruction: "Check carefully", tier: "flash", tools: [], mcp: [] },
};

function setup(over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/chat/sessions": over.list ?? (() => jsonResponse(sessions)),
    "GET /api/chat/sessions/sess-1": over.detail ?? (() => jsonResponse({ session: sessions[0], messages: [{ role: "user", text: "hi" }, { role: "bot", text: "ok", meta: { runs: [root, reviewer] } }] })),
    "GET /api/chat/sessions/sess-2": () => jsonResponse({ session: sessions[1], messages: [] }),
    "POST /api/agents": over.save ?? (() => jsonResponse({ ok: true })),
  });
}

beforeEach(() => {
  nav.search = "";
});

/** Selects the sub-agent's lane in the timeline (the second lane; the first is the root agent). */
async function reviewerLane(container: HTMLElement): Promise<HTMLElement> {
  await screen.findByRole("heading", { name: "Refactor auth" });
  await waitFor(() => expect(container.querySelectorAll(".tl-label").length).toBeGreaterThan(1));
  return container.querySelectorAll<HTMLElement>(".tl-label")[1];
}

describe("Runs: session list", () => {
  it("lists every saved chat with its agent and provider", async () => {
    setup();
    render(<RunsPage />);
    expect(await screen.findByText("Refactor auth")).toBeInTheDocument();
    expect(screen.getByText("Plain question").closest("tr")).toHaveTextContent("plain chat");
    expect(screen.getByRole("link", { name: "Refactor auth" })).toHaveAttribute("href", expect.stringMatching(/\/runs\/?\?s=sess-1$/));
  });

  it("explains when there are no runs yet", async () => {
    setup({ list: () => jsonResponse([]) });
    render(<RunsPage />);
    expect(await screen.findByText("No runs yet — start a chat with an agent.")).toBeInTheDocument();
  });

  it("reports when the backend is offline", async () => {
    setup({ list: () => textResponse("x", 500) });
    render(<RunsPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toBeInTheDocument();
  });
});

describe("Runs: inspector", () => {
  it("shows the chat's totals, timeline, thread and cost", async () => {
    nav.search = "s=sess-1";
    setup();
    const { container } = render(<RunsPage />);
    expect(await screen.findByRole("heading", { name: "Refactor auth" })).toBeInTheDocument();
    expect(container.querySelector(".insp-title")).toHaveTextContent("1 sub-agent · 1 built");
    expect(container.querySelector(".insp-title")).toHaveTextContent("$0.0021");
    expect(container.querySelector(".insp-title")).toHaveTextContent("0.8K → 0.1K tok");
    expect(await screen.findByText("Auth refactored", { exact: false })).toBeInTheDocument();
    expect(container.querySelector(".insp-tl")).not.toBeEmptyDOMElement();
    expect(container.querySelector(".insp-thread")).toHaveTextContent("Refactor the auth module");
  });

  it("shows a chat with no agent runs", async () => {
    nav.search = "s=sess-2";
    setup();
    render(<RunsPage />);
    expect(await screen.findByText("No agent runs in this chat yet.")).toBeInTheDocument();
  });

  it("filters the thread by text and counts matching runs", async () => {
    nav.search = "s=sess-1";
    setup();
    render(<RunsPage />);
    await screen.findByRole("heading", { name: "Refactor auth" });
    await userEvent.type(screen.getByPlaceholderText("Find in transcript"), "review");
    expect(await screen.findByText("1 agent run match")).toBeInTheDocument();
  });

  it("switches zoom and the side panels", async () => {
    nav.search = "s=sess-1";
    setup();
    const { container } = render(<RunsPage />);
    await screen.findByRole("heading", { name: "Refactor auth" });
    await userEvent.click(screen.getByRole("button", { name: "4×" }));
    expect(screen.getByRole("button", { name: "4×" })).toHaveClass("on");
    await userEvent.click(screen.getByRole("tab", { name: "Events" }));
    expect(await screen.findByText(/read_file · 120ms/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Tools" }));
    expect(container.querySelector(".insp-side table")).toHaveTextContent("read_file");
    await userEvent.click(screen.getByRole("tab", { name: "Session" }));
    expect(screen.getByText("Workspace").nextSibling).toHaveTextContent("C:/app");
    expect(screen.getByText("Built by agents").nextSibling).toHaveTextContent("1");
  });

  it("saves a sub-agent that an agent built", async () => {
    nav.search = "s=sess-1";
    vi.spyOn(window, "prompt").mockReturnValue("reviewer");
    const { calls } = setup();
    const { container } = render(<RunsPage />);
    await userEvent.click(await reviewerLane(container));
    await userEvent.click(await screen.findByRole("button", { name: "Save as agent" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ id: "reviewer", name: "Reviewer", tier: "flash" });
    expect(await screen.findByText("Saved agent “reviewer”.")).toBeInTheDocument();
  });

  it("reports a failed save and does nothing when the save is cancelled", async () => {
    nav.search = "s=sess-1";
    const prompt = vi.spyOn(window, "prompt").mockReturnValueOnce(null).mockReturnValueOnce("reviewer");
    const { calls } = setup({ save: () => jsonResponse({ detail: "exists" }, { status: 409 }) });
    const { container } = render(<RunsPage />);
    await userEvent.click(await reviewerLane(container));
    await userEvent.click(await screen.findByRole("button", { name: "Save as agent" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.click(await screen.findByRole("button", { name: "Save as agent" }));
    expect(await screen.findByText("⚠ exists")).toBeInTheDocument();
    expect(prompt).toHaveBeenCalledTimes(2);
  });

  it("shows the reason when the chat cannot be loaded", async () => {
    nav.search = "s=sess-9";
    routeFetch({ "GET /api/chat/sessions/sess-9": () => textResponse("gone", 404) });
    render(<RunsPage />);
    expect(await screen.findByText("HTTP 404")).toBeInTheDocument();
  });

  it("auto-refreshes while the box is ticked", async () => {
    nav.search = "s=sess-1";
    setup();
    render(<RunsPage />);
    await screen.findByRole("heading", { name: "Refactor auth" });
    await userEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("checkbox")).toBeChecked();
  });
});
