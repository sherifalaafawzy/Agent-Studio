import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "@/app/memory/page";
import { dirtyIds, setDirty } from "@/lib/dirty";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const M = (o: Record<string, any> = {}) => ({ id: "m_1", type: "semantic", scope: "user", scope_id: "local-user", text: "The billing system is Xero", data: {}, key: "", confidence: 0.9, status: "active",
  source: { agent: "engineer", session: "s1", origin: "tool" }, created: 1_700_000_000, updated: 1_700_000_000, expires: null, embedded: true, hits: 0, redacted: [], ...o });
const STATS = { total: 2, pending: 1, by_type: { semantic: 1, procedural: 1 }, backend: "local" };

function setup(over: Record<string, any> = {}) {
  const state = { active: [M(), M({ id: "m_2", type: "procedural", text: "To deploy run make ship", expires: 1_900_000_000, redacted: ["email"], source: {} })], pending: [M({ id: "m_p", status: "pending", text: "The user's timezone is Paris" })] };
  const net = routeFetch({
    "GET /api/memories/stats": () => jsonResponse({ ...STATS, ...(over.stats ?? {}) }),
    "GET /api/memories": (_i: any, url: URL) => jsonResponse(url.searchParams.get("status") === "pending" ? state.pending : state.active),
    "POST /api/memories": () => jsonResponse(M()), "PUT /api/memories/m_1": () => jsonResponse(M({ text: "edited" })), "DELETE /api/memories/m_1": () => jsonResponse({ ok: true }),
    "POST /api/memories/m_2/promote": () => jsonResponse({ skill: { name: "to-deploy" } }),
    "POST /api/memories/m_p/approve": () => jsonResponse(M({ id: "m_p" })), "POST /api/memories/m_p/reject": () => jsonResponse(M({ id: "m_p", status: "rejected" })),
    "POST /api/memories/forget": (init: any) => jsonResponse({ forgotten: JSON.parse(init.body).dry_run ? [{ id: "m_1", text: "The billing system is Xero" }] : [], dry_run: JSON.parse(init.body).dry_run, warnings: [] }),
    "GET /api/memories/export": () => jsonResponse({ format: "agent-studio-memories", memories: [] }),
    "POST /api/memories/import": () => jsonResponse({ added: 2, skipped: 1, errors: ["x"] }), "POST /api/memories/maintain": () => jsonResponse({ expired: 1, rejected_removed: 0, duplicates_merged: 2 }),
    "POST /api/memories/wipe": () => jsonResponse({ deleted: 2 }),
    "GET /api/memories/notes": () => jsonResponse({ file: "AGENTS.md", path: "H:/ws/AGENTS.md", text: "# Notes\n" }), "PUT /api/memories/notes": () => jsonResponse({ chars: 5 }),
    "GET /api/memories/config": () => jsonResponse({ enabled: true, backend: "local", backend_config: {}, embedding: { provider: "", model: "" }, review: false, redact_pii: true, auto_extract: "rules",
      extract_model: { provider: "", model: "" }, types: { semantic: { enabled: true, ttl_days: 0, max_items: 10 }, episodic: { enabled: true, ttl_days: 0, max_items: 10 }, entity: { enabled: true, ttl_days: 0, max_items: 10 }, procedural: { enabled: true, ttl_days: 0, max_items: 10 } }, recall_budget_chars: 1500, recall_k: 5, recall_min_score: 0.2,
      consolidation: { dedupe: true, merge_contradictions: true, similarity: 0.95, interval_hours: 24 }, notes_file: "AGENTS.md", notes_max_chars: 4000, secrets_set: [], backend_cloud: false, backend_label: "On this PC", type_info: {} }),
    "GET /api/memories/kinds": () => jsonResponse([]), "GET /api/providers": () => jsonResponse([]),
    "GET /api/memory": () => jsonResponse({ file: "m.json", entities: 0, relations: 0, observations: 0, types: {}, exists: false }), "GET /api/memory/entities": () => jsonResponse([]),
    ...(over.routes ?? {}),
  });
  return { net, state };
}
const sent = (calls: Call[], method: string, path: string) => calls.filter((c) => c.method === method && c.url.pathname === path);

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); for (const id of dirtyIds()) setDirty(id, false); window.location.hash = ""; });

describe("Memory page: memories", () => {
  it("lists what is remembered with where it came from, filters it, and shows the totals", async () => {
    const { net } = setup();
    render(<MemoryPage />);
    expect(await screen.findByText("The billing system is Xero")).toBeInTheDocument();
    expect(screen.getByText("To deploy run make ship")).toBeInTheDocument();
    expect(screen.getByText("by engineer")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "from a chat" })).toHaveAttribute("href", "/?s=s1");
    expect(screen.getByText(/removed: email/)).toBeInTheDocument();
    expect(screen.getByText(/expires/)).toBeInTheDocument();
    expect(screen.getByText("on this PC")).toBeInTheDocument();
    expect(screen.getByText("Review").closest("button")).toHaveTextContent("1");
    await userEvent.type(screen.getByLabelText("Search memories"), "billing");
    await userEvent.selectOptions(screen.getByLabelText("Filter by kind"), "semantic");
    await userEvent.selectOptions(screen.getByLabelText("Filter by who can see it"), "user");
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/memories" && c.url.search.includes("q=billing") && c.url.search.includes("type=semantic") && c.url.search.includes("scope=user"))).toBe(true));
  });

  it("says when nothing is remembered and when a search matches nothing, and when the list cannot load", async () => {
    setup({ stats: { total: 0, pending: 0, by_type: {} } });
    const { unmount } = render(<MemoryPage />);
    expect(await screen.findByText(/Nothing is remembered yet/)).toBeInTheDocument();
    unmount();
    setup({ routes: { "GET /api/memories": () => jsonResponse([]) } });
    const again = render(<MemoryPage />);
    expect(await screen.findByText("No matches.")).toBeInTheDocument();
    again.unmount();
    setup({ routes: { "GET /api/memories/stats": () => jsonResponse({ detail: "offline" }, { status: 500 }) } });
    render(<MemoryPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("adds a memory, edits one, makes a how-to a skill and forgets one", async () => {
    const { net } = setup();
    render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    expect(screen.getByRole("button", { name: "Remember" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Who can see it"), "user");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "procedural");
    await userEvent.type(screen.getByPlaceholderText(/Our invoices/), "We use Stripe");
    await userEvent.click(screen.getByRole("button", { name: "Remember" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories")[0].body).toEqual({ type: "procedural", scope: "user", text: "We use Stripe" }));

    const [first, second] = screen.getAllByTestId("mem-card");
    await userEvent.click(within(first).getByRole("button", { name: "Edit" }));
    await userEvent.clear(within(first).getByLabelText("Memory text"));
    await userEvent.type(within(first).getByLabelText("Memory text"), "edited");
    await userEvent.click(within(first).getByRole("button", { name: "Cancel" }));
    expect(sent(net.calls, "PUT", "/api/memories/m_1")).toHaveLength(0);
    await userEvent.click(within(first).getByRole("button", { name: "Edit" }));
    await userEvent.type(within(first).getByLabelText("Memory text"), " now");
    await userEvent.click(within(first).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent(net.calls, "PUT", "/api/memories/m_1")[0].body).toEqual({ text: "The billing system is Xero now" }));

    await userEvent.click(within(second).getByRole("button", { name: "Make it a Skill" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/m_2/promote")).toHaveLength(1));

    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    await userEvent.click(within(first).getByRole("button", { name: "Forget" }));
    expect(sent(net.calls, "DELETE", "/api/memories/m_1")).toHaveLength(0);
    await userEvent.click(within(first).getByRole("button", { name: "Forget" }));
    await waitFor(() => expect(sent(net.calls, "DELETE", "/api/memories/m_1")).toHaveLength(1));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("reports a memory action that fails, and a memory that cannot be added", async () => {
    setup({ routes: { "PUT /api/memories/m_1": () => jsonResponse({ detail: "too long" }, { status: 422 }), "POST /api/memories": () => jsonResponse({ detail: "scope" }, { status: 422 }) } });
    render(<MemoryPage />);
    const [first] = await screen.findAllByTestId("mem-card");
    await userEvent.click(within(first).getByRole("button", { name: "Edit" }));
    await userEvent.click(within(first).getByRole("button", { name: "Save" }));
    await userEvent.type(screen.getByPlaceholderText(/Our invoices/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Remember" }));
    await waitFor(() => expect(screen.getAllByTestId("mem-card")).toHaveLength(2));
  });

  it("forgets by description only after showing what would go", async () => {
    const { net } = setup();
    render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    expect(screen.getByRole("button", { name: "Show what would be forgotten" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What to forget"), "billing");
    await userEvent.click(screen.getByRole("button", { name: "Show what would be forgotten" }));
    await userEvent.click(await screen.findByRole("button", { name: "Forget these 1" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/forget").map((c) => c.body.dry_run)).toEqual([true, false]));
    expect(screen.queryByRole("button", { name: /Forget these/ })).toBeNull();
  });

  it("says when a description matches nothing, and when the preview fails", async () => {
    setup({ routes: { "POST /api/memories/forget": () => jsonResponse({ forgotten: [], dry_run: true, warnings: [] }) } });
    const first = render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    await userEvent.type(screen.getByLabelText("What to forget"), "zzz");
    await userEvent.click(screen.getByRole("button", { name: "Show what would be forgotten" }));
    expect(await screen.findByText("Nothing matches that.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("What to forget"), "q");                       // editing the text hides the old preview
    expect(screen.queryByText("Nothing matches that.")).toBeNull();
    first.unmount();
    setup({ routes: { "POST /api/memories/forget": () => jsonResponse({ detail: "no" }, { status: 422 }) } });
    render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    await userEvent.type(screen.getByLabelText("What to forget"), "billing");
    await userEvent.click(screen.getByRole("button", { name: "Show what would be forgotten" }));
    await waitFor(() => expect(screen.queryByText("Nothing matches that.")).toBeNull());
  });

  it("reports a failed forget after the preview", async () => {
    let n = 0;
    setup({ routes: { "POST /api/memories/forget": () => (++n === 1 ? jsonResponse({ forgotten: [{ id: "m_1", text: "x" }], dry_run: true, warnings: [] }) : jsonResponse({ detail: "denied" }, { status: 422 })) } });
    render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    await userEvent.type(screen.getByLabelText("What to forget"), "billing");
    await userEvent.click(screen.getByRole("button", { name: "Show what would be forgotten" }));
    await userEvent.click(await screen.findByRole("button", { name: "Forget these 1" }));
    await waitFor(() => expect(n).toBe(2));
  });

  it("exports, imports, tidies and erases everything", async () => {
    const { net } = setup();
    URL.createObjectURL = vi.fn(() => "blob:x"); URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    await userEvent.click(screen.getByRole("button", { name: "Export memories" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    const file = new File(['{"format":"agent-studio-memories","memories":[]}'], "m.json", { type: "application/json" });
    await userEvent.upload(screen.getByLabelText("Import file"), file);
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/import")).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Import file"), { target: { files: [] } });            // cancelling the file dialog does nothing
    await userEvent.click(screen.getByRole("button", { name: "Import memories" }));
    await userEvent.click(screen.getByRole("button", { name: "Tidy up now" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/maintain")).toHaveLength(1));
    const prompt = vi.spyOn(window, "prompt").mockReturnValueOnce("nope").mockReturnValue("DELETE ALL MEMORY");
    await userEvent.click(screen.getByRole("button", { name: "Erase everything" }));
    expect(sent(net.calls, "POST", "/api/memories/wipe")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Erase everything" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/wipe")[0].body).toEqual({ confirm: "DELETE ALL MEMORY" }));
    expect(prompt).toHaveBeenCalledTimes(2);
  });

  it("reports failures of export, import, tidy and erase", async () => {
    const bad = () => jsonResponse({ detail: "nope" }, { status: 500 });
    setup({ routes: { "GET /api/memories/export": bad, "POST /api/memories/import": bad, "POST /api/memories/maintain": bad, "POST /api/memories/wipe": bad } });
    vi.spyOn(window, "prompt").mockReturnValue("DELETE ALL MEMORY");
    render(<MemoryPage />);
    await screen.findByText("The billing system is Xero");
    await userEvent.click(screen.getByRole("button", { name: "Export memories" }));
    await userEvent.upload(screen.getByLabelText("Import file"), new File(["{}"], "m.json", { type: "application/json" }));
    await userEvent.click(screen.getByRole("button", { name: "Tidy up now" }));
    await userEvent.click(screen.getByRole("button", { name: "Erase everything" }));
    await waitFor(() => expect(screen.getAllByTestId("mem-card")).toHaveLength(2));
  });
});

describe("Memory page: review, notes, settings, graph", () => {
  it("approves, edits-and-approves and rejects waiting memories", async () => {
    const { net } = setup();
    render(<MemoryPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Review/ }));
    expect(window.location.hash).toBe("#review");
    const card = await screen.findByText("The user's timezone is Paris");
    expect(card).toBeInTheDocument();
    expect(screen.getByText("waiting for review")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/m_p/approve")[0].body).toEqual({}));
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Memory text"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Save and approve" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/m_p/approve")[1].body).toEqual({ text: "The user's timezone is Paris!" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(sent(net.calls, "POST", "/api/memories/m_p/reject")).toHaveLength(1));
  });

  it("says when nothing waits for review", async () => {
    const { state } = setup({ stats: { total: 0, pending: 0, by_type: {} } });
    state.pending = [];
    render(<MemoryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Review" }));
    expect(await screen.findByText("Nothing is waiting for your review.")).toBeInTheDocument();
  });

  it("edits the project notes file and keeps unsaved edits guarded", async () => {
    const { net } = setup();
    window.location.hash = "#notes";
    render(<MemoryPage />);
    const box = await screen.findByLabelText("Project notes");
    await waitFor(() => expect(box).toHaveValue("# Notes\n"));
    expect(screen.getByRole("button", { name: "Save notes" })).toBeDisabled();
    await userEvent.type(box, "- use pnpm");
    expect(dirtyIds()).toContain("memory-notes");
    await userEvent.click(screen.getByRole("button", { name: "Save notes" }));
    await waitFor(() => expect(sent(net.calls, "PUT", "/api/memories/notes")[0].body).toEqual({ text: "# Notes\n- use pnpm" }));
    await waitFor(() => expect(dirtyIds()).not.toContain("memory-notes"));
  });

  it("reports notes that cannot be loaded or saved", async () => {
    setup({ routes: { "GET /api/memories/notes": () => jsonResponse({ detail: "no folder" }, { status: 500 }) } });
    window.location.hash = "#notes";
    const first = render(<MemoryPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    first.unmount();
    setup({ routes: { "PUT /api/memories/notes": () => jsonResponse({ detail: "too long" }, { status: 422 }) } });
    render(<MemoryPage />);
    await userEvent.type(await screen.findByLabelText("Project notes"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save notes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too long");
  });

  it("opens the settings and the older graph, remembering the tab in the address", async () => {
    setup();
    render(<MemoryPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Settings" }));
    expect(await screen.findByText("Memory is on")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Older graph" }));
    expect(await screen.findByText(/older knowledge graph/)).toBeInTheDocument();
    expect(window.location.hash).toBe("#graph");
  });
});
