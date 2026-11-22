import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import CatalogSkills from "@/components/CatalogSkills";
import { jsonResponse, routeFetch } from "../helpers/http";

const SKILLS = [
  { name: "bank-reconciliation", description: "Match bank lines to the ledger.", mcp: ["excel", "xero"], domain: "accounting", group: "Accounting", source: "builtin", used_by: [] },
  { name: "pipeline-review", description: "Review a sales pipeline.", mcp: [], domain: "sales", group: "Sales", source: "builtin", used_by: [{ id: "b", name: "Beta" }] },
];
const AGENTS = [{ id: "a", name: "Alpha", skills: [] as string[] }, { id: "b", name: "Beta", skills: ["group:Sales"] }, { id: "c", name: "Gamma", skills: ["*"] }];
const toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);
beforeEach(() => { toasts.length = 0; window.addEventListener("as:toast", onToast); });
afterEach(() => window.removeEventListener("as:toast", onToast));

function setup(routes: Record<string, any> = {}, props = { domain: "", q: "" }) {
  const r = routeFetch({ "GET /api/catalog/skills": () => jsonResponse(SKILLS), "GET /api/agents": () => jsonResponse(AGENTS), ...routes });
  const ui = render(<CatalogSkills {...props} />);
  return { ...r, ...ui };
}
const card = (n: string) => document.querySelector(`[data-skill="${n}"]`) as HTMLElement;

describe("CatalogSkills", () => {
  it("lists the skills with what they work with and who uses them", async () => {
    setup();
    expect(await screen.findByText("bank-reconciliation")).toBeInTheDocument();
    expect(within(card("bank-reconciliation")).getByText("excel")).toBeInTheDocument();
    expect(within(card("bank-reconciliation")).getByText("No agent uses it yet")).toBeInTheDocument();
    expect(within(card("pipeline-review")).getByText("Used by Beta")).toBeInTheDocument();
  });

  it("filters by domain and by search text, and says when nothing matches", async () => {
    const { rerender } = setup({}, { domain: "sales", q: "" });
    await screen.findByText("pipeline-review");
    expect(screen.queryByText("bank-reconciliation")).toBeNull();
    rerender(<CatalogSkills domain="" q="ledger" />);
    expect(await screen.findByText("bank-reconciliation")).toBeInTheDocument();
    expect(screen.queryByText("pipeline-review")).toBeNull();
    rerender(<CatalogSkills domain="" q="zzz" />);
    expect(screen.getByText("No skills match.")).toBeInTheDocument();
  });

  it("adds a skill to the chosen agent with one click", async () => {
    const { calls } = setup({ "PUT /api/agents/a": () => jsonResponse({}) });
    await screen.findByText("bank-reconciliation");
    await userEvent.click(within(card("bank-reconciliation")).getByRole("button", { name: "Add to agent" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ skills: ["bank-reconciliation"] }));
    expect(toasts[0].text).toContain("added to Alpha");
  });

  it("can pick another agent, and shows Added when it already has the skill (directly, by group, or by *)", async () => {
    const { calls } = setup({ "PUT /api/agents/b": () => jsonResponse({}) });
    await screen.findByText("bank-reconciliation");
    const sel = within(card("bank-reconciliation")).getByLabelText("Agent for bank-reconciliation");
    await userEvent.selectOptions(sel, "b");
    await userEvent.click(within(card("bank-reconciliation")).getByRole("button", { name: "Add to agent" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ skills: ["group:Sales", "bank-reconciliation"] }));
    await userEvent.selectOptions(within(card("pipeline-review")).getByLabelText("Agent for pipeline-review"), "b");
    expect(within(card("pipeline-review")).getByRole("button", { name: /Added/ })).toBeDisabled();
    await userEvent.selectOptions(within(card("pipeline-review")).getByLabelText("Agent for pipeline-review"), "c");
    expect(within(card("pipeline-review")).getByRole("button", { name: /Added/ })).toBeDisabled();
  });

  it("reports a failed add", async () => {
    setup({ "PUT /api/agents/a": () => jsonResponse({ detail: "built-in agents cannot be edited" }, { status: 422 }) });
    await screen.findByText("bank-reconciliation");
    await userEvent.click(within(card("bank-reconciliation")).getByRole("button", { name: "Add to agent" }));
    await waitFor(() => expect(toasts[0]).toEqual({ text: "built-in agents cannot be edited", tone: "err" }));
  });

  it("disables adding when there is no agent", async () => {
    const { calls } = setup({ "GET /api/agents": () => jsonResponse([]) });
    await screen.findByText("bank-reconciliation");
    expect(within(card("bank-reconciliation")).getByRole("button", { name: "Add to agent" })).toBeDisabled();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("reads a skill in place and hides it again", async () => {
    setup({ "GET /api/skills/bank-reconciliation": () => jsonResponse({ body: "# Bank reconciliation\n\n**When to use:** now" }) });
    await screen.findByText("bank-reconciliation");
    await userEvent.click(within(card("bank-reconciliation")).getByRole("button", { name: "Read" }));
    expect(await screen.findByText(/When to use/)).toBeInTheDocument();
    await userEvent.click(within(card("bank-reconciliation")).getByRole("button", { name: "Hide" }));
    expect(screen.queryByText(/When to use/)).toBeNull();
  });

  it("says so when a skill cannot be loaded", async () => {
    setup({ "GET /api/skills/pipeline-review": () => jsonResponse({ detail: "gone" }, { status: 404 }) });
    await screen.findByText("pipeline-review");
    await userEvent.click(within(card("pipeline-review")).getByRole("button", { name: "Read" }));
    expect(await screen.findByText("Could not load: gone")).toBeInTheDocument();
  });

  it("shows a message when the backend is offline", async () => {
    routeFetch({ "GET /api/catalog/skills": () => { throw new Error("connection refused"); }, "GET /api/agents": () => jsonResponse([]) });
    render(<CatalogSkills domain="" q="" />);
    expect(await screen.findByText(/Backend offline: connection refused/)).toBeInTheDocument();
  });
});
