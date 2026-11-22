import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AgentsPage from "@/app/agents/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/agents/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const agent = (skills: string[]) => ({
  id: "engineer", name: "Engineer", description: "", instruction: "", provider: null, tier: "pro", model: null,
  tools: [], mcp: [], skills, sub_agents: [], builtin: true, can_spawn: false, acp_agents: [], acp_policy: "ask",
});
const skills = [
  { name: "misc", description: "Odds", group: "General" },
  { name: "audit", description: "Audit code", group: "Security" },
  { name: "debug", description: "Fix bugs", group: "Coding" },
  { name: "review", description: "Review diffs", group: "Coding" },
  { name: "nogroup", description: "Legacy skill with no group field" },
];

function setup(initial: string[]) {
  return routeFetch({
    "GET /api/agents": () => jsonResponse([agent(initial)]),
    "GET /api/tools": () => jsonResponse([]),
    "GET /api/mcp": () => jsonResponse([]),
    "GET /api/skills": () => jsonResponse(skills),
    "GET /api/acp/agents": () => jsonResponse([]),
    "PUT /api/agents/engineer": () => jsonResponse({ ok: true }),
  });
}

const box = (group: string) => document.querySelector(`.ag-skillgroup[data-group="${group}"]`) as HTMLElement;
const groupCheck = (group: string) => box(group).querySelector(".ag-skillgroup-head input") as HTMLInputElement;
const save = async (calls: any[]) => {
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  return calls.find((c) => c.method === "PUT").body;
};

describe("Agents: attaching skill groups", () => {
  it("shows one block per group (General last) with a whole-group checkbox and per-skill checkboxes", async () => {
    setup([]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await waitFor(() => expect([...document.querySelectorAll(".ag-skillgroup")].map((b) => b.getAttribute("data-group"))).toEqual(["Coding", "Security", "General"]));
    expect(box("Coding")).toHaveTextContent("whole group · 2");
    expect(box("General")).toHaveTextContent("whole group · 2");        // misc + the group-less skill
    expect(box("General").querySelectorAll(".row input")).toHaveLength(2);
    expect(groupCheck("Coding")).not.toBeChecked();
  });

  it("attaches a whole group as a group: entry, keeping named skills and covering the members", async () => {
    const { calls } = setup(["audit"]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await waitFor(() => expect(box("Coding")).toBeTruthy());
    await userEvent.click(groupCheck("Coding"));
    expect(groupCheck("Coding")).toBeChecked();
    const members = [...box("Coding").querySelectorAll<HTMLInputElement>(".row input")];
    expect(members.every((m) => m.checked && m.disabled)).toBe(true);   // covered by the group
    expect(box("Security").querySelector<HTMLInputElement>(".row input")).toBeChecked();   // explicit name kept
    expect(await save(calls)).toMatchObject({ skills: ["audit", "group:Coding"] });
  });

  it("detaches a group again (matching the stored reference case-insensitively)", async () => {
    const { calls } = setup(["group:coding", "audit"]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await waitFor(() => expect(groupCheck("Coding")).toBeChecked());
    await userEvent.click(groupCheck("Coding"));
    expect(groupCheck("Coding")).not.toBeChecked();
    expect(await save(calls)).toMatchObject({ skills: ["audit"] });
  });

  it("still lets single skills be ticked, and 'all' hides the group blocks", async () => {
    const { calls } = setup([]);
    render(<AgentsPage />);
    await screen.findByDisplayValue("Engineer");
    await waitFor(() => expect(box("Security")).toBeTruthy());
    await userEvent.click(screen.getByLabelText("audit"));
    expect(await save(calls)).toMatchObject({ skills: ["audit"] });
    await userEvent.click(screen.getByLabelText("all"));
    expect(document.querySelectorAll(".ag-skillgroup")).toHaveLength(0);
  });
});
