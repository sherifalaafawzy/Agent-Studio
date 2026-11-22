import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RunsPage from "@/app/runs/page";
import { jsonResponse, routeFetch } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/runs/",
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: () => {} }),
}));

const now = () => Date.now() / 1000;
const session = { id: "s1", title: "Edge chat", provider: "openai", agent: null, model: null, workspace: null, created: now() - 100, updated: now() - 100, messages: 2 };

function run(over: Record<string, any>) {
  return { run_id: "root", parent: null, agent: "engineer", spawned: false, task: "task", status: "done", steps: [], texts: [], calls: [], result: null, cost_usd: 0, ...over };
}

function detail(messages: any[]) {
  return routeFetch({ "GET /api/chat/sessions/s1": () => jsonResponse({ session, messages }) });
}

beforeEach(() => {
  nav.search = "s=s1";
});

describe("Runs: relative times in the list", () => {
  it("shows minutes and days for older chats", async () => {
    nav.search = "";
    routeFetch({
      "GET /api/chat/sessions": () => jsonResponse([
        { ...session, id: "a", title: "Recent", updated: now() - 1000 },
        { ...session, id: "b", title: "Old", updated: now() - 3 * 86400 },
      ]),
    });
    render(<RunsPage />);
    expect((await screen.findByText("Recent")).closest("tr")).toHaveTextContent("17m ago");
    expect(screen.getByText("Old").closest("tr")).toHaveTextContent("3d ago");
  });
});

describe("Runs: inspector with sparse data", () => {
  it("treats bot messages without run metadata as having no runs, and says so in the side panels", async () => {
    detail([{ role: "bot", text: "plain" }, { role: "bot", text: "no runs", meta: {} }]);
    render(<RunsPage />);
    expect(await screen.findByText("No agent runs in this chat yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Events" }));
    expect(screen.getByText("No timed events.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Tools" }));
    expect(screen.getByText("No tool calls.")).toBeInTheDocument();
  });

  it("copes with runs that lack timestamps, and aggregates tool results without timings", async () => {
    const t = now() - 500;
    detail([
      { role: "bot", text: "a", meta: { runs: [
        run({
          started: t, ended: null, status: "running",
          steps: [
            { agent: "engineer", type: "result", tool: "grep", ts: t + 1, ok: false },
            { agent: "engineer", type: "result", tool: "grep", ms: 10, ok: true },
            { agent: "engineer", type: "call", tool: "grep" },
          ],
          calls: [{ agent: "engineer", provider: "openai", model: "gpt-x", cost_usd: 0, input_tokens: 0, output_tokens: 0 }],
        }),
        run({ run_id: "r1", parent: "root", agent: "team__helper", started: t + 2, ended: t + 3 }),
      ] } },
      { role: "bot", text: "b", meta: { runs: [run({})] } },
    ]);
    const { container } = render(<RunsPage />);
    await screen.findByRole("heading", { name: "Edge chat" });
    await waitFor(() => expect(container.querySelectorAll(".tl-label").length).toBeGreaterThan(1));
    // Active time: root 1 has no end (so zero), root 2 has neither start nor end
    expect(container.querySelector(".insp-title")).toHaveTextContent("⏱ 0ms");
    expect(container.querySelector(".insp-title")).toHaveTextContent("1 sub-agent");
    expect(container.querySelector(".insp-title")).not.toHaveTextContent("built");

    await userEvent.click(screen.getByRole("button", { name: "Events" }));
    const side = container.querySelector(".insp-side")!;
    expect(side).toHaveTextContent("engineer turn started");
    expect(side).toHaveTextContent("helper started");
    expect(side).not.toHaveTextContent("built by agent");
    expect(side).toHaveTextContent(/helper done · \$0/);
    expect(side).toHaveTextContent("grep · 0ms · failed");
    expect(side.querySelectorAll(".ev")).toHaveLength(4);

    await userEvent.click(screen.getByRole("button", { name: "Tools" }));
    const row = screen.getByText("grep", { selector: "code" }).closest("tr")!;
    expect(row).toHaveTextContent("grep25ms1");
  });
});

describe("Runs: saving agents built at runtime", () => {
  it("fills in defaults when an agent's definition is missing or partial", async () => {
    const t = now() - 500;
    const prompt = vi.spyOn(window, "prompt").mockImplementation((_m, d) => d ?? "");
    const sub = (over: Record<string, any>) => run({ parent: "root", spawned: true, started: t, ended: t + 1, ...over });
    const { calls } = routeFetch({
      "GET /api/chat/sessions/s1": () => jsonResponse({ session, messages: [{ role: "bot", text: "a", meta: { runs: [
        run({}),
        sub({ run_id: "r1", agent: "team__Alpha", task: "T1", instruction: "Do alpha" }),
        sub({ run_id: "r2", agent: "team__beta", task: "T2" }),
        sub({ run_id: "r3", agent: "team__gamma", task: "T3", definition: { instruction: "G instr", tier: "pro", tools: ["bash"], mcp: ["fs"] } }),
      ] } }] }),
      "POST /api/agents": () => jsonResponse({ ok: true }),
    });
    const { container } = render(<RunsPage />);
    await screen.findByRole("heading", { name: "Edge chat" });
    await waitFor(() => expect(container.querySelectorAll(".tl-label").length).toBe(4));

    const save = async (lane: number) => {
      await userEvent.click(container.querySelectorAll<HTMLElement>(".tl-label")[lane]);
      await userEvent.click(await screen.findByRole("button", { name: "Save as agent" }));
    };
    await save(1);
    await save(2);
    await save(3);
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(3));

    expect(prompt.mock.calls.map((c) => c[1])).toEqual(["team__alpha", "team__beta", "team__gamma"]);
    const bodies = calls.filter((c) => c.method === "POST").map((c) => c.body);
    expect(bodies[0]).toEqual({ id: "team__alpha", name: "team__alpha", description: "T1", instruction: "Do alpha", tier: "flash", tools: [], mcp: [] });
    expect(bodies[1]).toEqual({ id: "team__beta", name: "team__beta", description: "T2", instruction: "", tier: "flash", tools: [], mcp: [] });
    expect(bodies[2]).toEqual({ id: "team__gamma", name: "team__gamma", description: "T3", instruction: "G instr", tier: "pro", tools: ["bash"], mcp: ["fs"] });
  });
});
