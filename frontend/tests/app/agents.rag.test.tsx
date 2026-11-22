import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/agents/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const agent = (over: Record<string, unknown> = {}) => ({
  id: "engineer", name: "Engineer", description: "", instruction: "x", provider: null, tier: "pro", model: null,
  tools: [], mcp: [], skills: [], sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask", ...over,
});
const kbs = [{ id: "docs", label: "Product docs", description: "All the manuals" }, { id: "faq", label: "FAQ" }];

function setup(agents: unknown[], kbRoute: () => Response = () => jsonResponse(kbs)) {
  return routeFetch({
    "GET /api/agents": () => jsonResponse(agents),
    "GET /api/tools": () => jsonResponse([]),
    "GET /api/mcp": () => jsonResponse([]),
    "GET /api/skills": () => jsonResponse([]),
    "GET /api/acp/agents": () => jsonResponse([]),
    "GET /api/rag/kbs": kbRoute,
    "PUT /api/agents/engineer": () => jsonResponse({ ok: true }),
  });
}
const saved = (calls: ReturnType<typeof setup>["calls"]) => calls.find((c) => c.method === "PUT")!.body as any;

describe("Agent editor: knowledge bases", () => {
  it("lists the knowledge bases with their descriptions as tooltips", async () => {
    setup([agent()]);
    render(<AgentsPage />);
    expect(await screen.findByRole("checkbox", { name: "Product docs" })).toBeInTheDocument();
    expect(screen.getByText("Product docs").closest("label")).toHaveAttribute("title", "All the manuals");
    expect(screen.getByRole("checkbox", { name: "FAQ" })).not.toBeChecked();
    expect(screen.getByText(/rag_search/)).toBeInTheDocument();
  });

  it("attaches and detaches knowledge bases and saves them as the agent's rag list", async () => {
    const { calls } = setup([agent({ rag: ["faq"] })]);
    render(<AgentsPage />);
    const faq = await screen.findByRole("checkbox", { name: "FAQ" });
    await waitFor(() => expect(faq).toBeChecked());                      // the agent's own list is applied a moment after the boxes appear
    await userEvent.click(screen.getByRole("checkbox", { name: "Product docs" }));
    await userEvent.click(faq);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(saved(calls).rag).toEqual(["docs"]);
  });

  it("attaches all knowledge bases with the * value and hides the individual boxes", async () => {
    const { calls } = setup([agent()]);
    render(<AgentsPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "all knowledge bases" }));
    expect(screen.queryByRole("checkbox", { name: "Product docs" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(saved(calls).rag).toEqual(["*"]);
  });

  it("un-ticks all knowledge bases", async () => {
    const { calls } = setup([agent({ rag: ["*"] })]);
    render(<AgentsPage />);
    const all = await screen.findByRole("checkbox", { name: "all knowledge bases" });
    await waitFor(() => expect(all).toBeChecked());
    await userEvent.click(all);
    expect(await screen.findByRole("checkbox", { name: "Product docs" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(saved(calls).rag).toEqual([]);
  });

  it("works for an agent saved before the rag field existed", async () => {
    const { calls } = setup([agent()]);
    render(<AgentsPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "FAQ" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(saved(calls).rag).toEqual(["faq"]);
  });

  it("starts a new agent with no knowledge bases attached", async () => {
    const { calls } = setup([agent()]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await userEvent.click(screen.getByRole("button", { name: "+ New" }));
    await userEvent.click(await screen.findByRole("button", { name: /New agent .wizard./ }));
    await userEvent.click(await screen.findByRole("button", { name: "Advanced editor" }));         // the guided creator opens first
    expect(screen.getByRole("checkbox", { name: "FAQ" })).not.toBeChecked();
    expect(calls.length).toBeGreaterThan(0);
  });

  it("points to the Knowledge page when there are none, and survives the list failing", async () => {
    setup([agent()], () => jsonResponse([]));
    const { unmount } = render(<AgentsPage />);
    const link = await screen.findByRole("link", { name: "Knowledge" });
    expect(link).toHaveAttribute("href", "/knowledge/");
    unmount();
    setup([agent()], () => textResponse("down", 500));
    render(<AgentsPage />);
    expect(await screen.findByRole("link", { name: "Knowledge" })).toBeInTheDocument();
    expect(await screen.findByDisplayValue("Engineer")).toBeInTheDocument();
  });
});
