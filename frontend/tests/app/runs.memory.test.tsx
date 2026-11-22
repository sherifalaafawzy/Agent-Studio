import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import RunsPage from "@/app/runs/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/runs/", useSearchParams: () => new URLSearchParams("s=sess-1"), useRouter: () => ({ push: () => {} }) }));

const now = () => Date.now() / 1000;
const session = { id: "sess-1", title: "Remembering", provider: "openai", agent: "engineer", model: "gpt-x", workspace: "C:/app", created: now() - 3600, updated: now() - 60, messages: 2 };
const root = { run_id: "root", parent: null, agent: "engineer", spawned: false, task: "Hi", status: "done", started: now() - 100, ended: now() - 80, steps: [], texts: ["ok"], calls: [], result: "ok", cost_usd: 0 };

describe("Runs: memory", () => {
  it("counts the memories used and saved in a chat and lists what was remembered", async () => {
    routeFetch({ "GET /api/chat/sessions/sess-1": () => jsonResponse({ session, messages: [{ role: "user", text: "hi" }, { role: "bot", text: "ok", meta: { runs: [root], memory: [
      { kind: "recalled", type: "semantic", text: "Billing is Xero" }, { kind: "saved", type: "semantic", text: "Deploys on Fridays" }, { kind: "pending", type: "semantic", text: "Timezone Paris" },
      { kind: "notes", file: "AGENTS.md" }] } }] }) });
    const { container } = render(<RunsPage />);
    await screen.findByRole("heading", { name: "Remembering" });
    const usage = await screen.findByText("Memories used");
    expect(usage.nextSibling).toHaveTextContent("1");
    expect(screen.getByText("Memories saved").nextSibling).toHaveTextContent("2");
    await userEvent.click(screen.getByText("What it remembered"));
    expect(screen.getByText(/recalled \(semantic\): Billing is Xero/)).toBeInTheDocument();
    expect(screen.getByText(/notes: AGENTS.md/)).toBeInTheDocument();
    expect(container.querySelector("details")).not.toBeNull();
  });

  it("shows no memory section for a chat without memory", async () => {
    routeFetch({ "GET /api/chat/sessions/sess-1": () => jsonResponse({ session, messages: [{ role: "bot", text: "ok", meta: { runs: [root] } }] }) });
    render(<RunsPage />);
    await screen.findByRole("heading", { name: "Remembering" });
    expect((await screen.findByText("Memories used")).nextSibling).toHaveTextContent("0");
    expect(screen.queryByText("What it remembered")).toBeNull();
  });
});
