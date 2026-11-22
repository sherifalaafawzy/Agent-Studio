import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SkillsPage from "@/app/skills/page";
import { API_BASE } from "@/lib/api";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/skills/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const list = [
  { name: "audit", description: "Audit code", mcp: [], source: "builtin", group: "Security", used_by: ["sec_agent", "engineer"] },
  { name: "debug", description: "Fix bugs", mcp: [], source: "builtin", group: "Coding", used_by: [] },
  { name: "review", description: "Review diffs", mcp: [], source: "builtin", group: "Coding", used_by: [] },
  { name: "mine", description: "My own playbook", mcp: ["git"], source: "user", group: "Finance", used_by: [] },
  { name: "misc", description: "Odds and ends", mcp: [], source: "builtin", used_by: [] },
];
const detail = (name: string) => ({ ...list.find((s) => s.name === name)!, body: `## ${name} body` });

function setup(over: Record<string, (init?: RequestInit) => Response> = {}) {
  return routeFetch({
    "GET /api/skills": () => jsonResponse(list),
    "GET /api/mcp": () => jsonResponse([{ id: "git", label: "Git", ready: true }]),
    "GET /api/skills/audit": () => jsonResponse(detail("audit")),
    "GET /api/skills/debug": () => jsonResponse(detail("debug")),
    "GET /api/skills/mine": () => jsonResponse(detail("mine")),
    "GET /api/skills/misc": () => jsonResponse(detail("misc")),
    "POST /api/skills": over.save ?? ((init) => jsonResponse({ ...detail("mine"), ...JSON.parse(String(init?.body)) })),
    "POST /api/skills/audit/duplicate": over.dup ?? (() => jsonResponse({ ...detail("audit"), name: "audit-custom", source: "user" })),
    "POST /api/skills/import": over.imp ?? (() => jsonResponse({ ...detail("mine"), name: "imported", source: "user" })),
  });
}

const headings = () => [...document.querySelectorAll(".sk-gh")].map((h) => h.textContent);
const ready = () => waitFor(() => expect(headings()).toHaveLength(4));

describe("Skills: groups", () => {
  it("lists skills under group headings with counts, General last", async () => {
    setup();
    render(<SkillsPage />);
    await ready();
    expect(headings()).toEqual(["▶Coding2", "▶Finance1", "▶Security1", "▶General1"]);
    const coding = document.querySelector('[data-group="Coding"]') as HTMLElement;
    expect(within(coding).getAllByText(/^(debug|review)$/)).toHaveLength(2);
    expect(document.querySelector('[data-group="General"]')).toHaveTextContent("misc");   // a skill with no group lands in General
  });

  it("badges built-in and your own skills in the list", async () => {
    setup();
    render(<SkillsPage />);
    await ready();
    const mine = document.querySelector('[data-group="Finance"] .li') as HTMLElement;
    expect(within(mine).getByText("yours")).toHaveClass("acc");
    const dbg = document.querySelector('[data-group="Coding"] .li') as HTMLElement;
    expect(within(dbg).getByText("built-in")).not.toHaveClass("acc");
  });

  it("collapses and expands a group", async () => {
    setup();
    render(<SkillsPage />);
    await ready();
    const head = document.querySelector('[data-group="Coding"] .sk-gh') as HTMLElement;
    expect(head).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(head);
    expect(head).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelectorAll('[data-group="Coding"] .li')).toHaveLength(0);
    expect(headings()[0]).toContain("2");                                  // count still shown
    await userEvent.click(head);
    expect(document.querySelectorAll('[data-group="Coding"] .li')).toHaveLength(2);
  });

  it("searches names, descriptions and group names, and ignores collapsing while searching", async () => {
    setup();
    render(<SkillsPage />);
    await ready();
    await userEvent.click(document.querySelector('[data-group="Coding"] .sk-gh') as HTMLElement);
    await userEvent.type(screen.getByPlaceholderText("Search skills"), "coding");
    expect(headings()).toEqual(["▶Coding2"]);
    expect(document.querySelectorAll(".li")).toHaveLength(2);              // open despite being collapsed
    await userEvent.clear(screen.getByPlaceholderText("Search skills"));
    await userEvent.type(screen.getByPlaceholderText("Search skills"), "playbook");
    expect(headings()).toEqual(["▶Finance1"]);
  });
});

describe("Skills: detail, used by, export", () => {
  it("shows the group and which agents use the skill", async () => {
    setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("audit"));
    expect(await screen.findByText("sec_agent", { selector: ".sk-used .pill" })).toBeInTheDocument();
    expect(screen.getByText("engineer", { selector: ".sk-used .pill" })).toBeInTheDocument();
    expect(screen.getByText("Group").nextSibling).toHaveTextContent("Security");
    await userEvent.click(screen.getByText("debug"));
    expect(await screen.findByText("no agent yet")).toBeInTheDocument();
  });

  it("falls back to 'General' and 'no agent yet' when the backend sends neither", async () => {
    routeFetch({
      "GET /api/skills": () => jsonResponse([{ name: "old", description: "d", mcp: [], source: "builtin" }]),
      "GET /api/mcp": () => jsonResponse([]),
      "GET /api/skills/old": () => jsonResponse({ name: "old", description: "d", mcp: [], source: "builtin", body: "b" }),
    });
    render(<SkillsPage />);
    expect(await screen.findByText("no agent yet")).toBeInTheDocument();
    expect(screen.getByText("Group").nextSibling).toHaveTextContent("General");
  });

  it("offers a download link for the SKILL.md", async () => {
    setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("audit"));
    const a = await screen.findByTitle("Export SKILL.md");
    expect(a).toHaveAttribute("href", `${API_BASE}/api/skills/audit/export`);
    expect(a).toHaveAttribute("download", "audit-SKILL.md");
  });
});

describe("Skills: group picker in the editor", () => {
  it("offers suggested and existing groups and saves the chosen group", async () => {
    const { calls } = setup();
    render(<SkillsPage />);
    await ready();
    await userEvent.click(screen.getByRole("button", { name: /New skill/ }));
    const opts = [...document.querySelectorAll("#skill-groups option")].map((o) => o.getAttribute("value"));
    expect(opts).toEqual(["Coding", "Security", "Research", "Writing", "Data", "Ops", "General", "Finance"]);
    const group = screen.getByLabelText("Group") as HTMLInputElement;
    expect(group).toHaveValue("General");
    await userEvent.clear(group);
    await userEvent.type(group, "Research");
    await userEvent.type(screen.getByPlaceholderText("release-checklist"), "new-one");
    await userEvent.type(screen.getByPlaceholderText("the user asks to cut a release or bump versions"), "Use it");
    await userEvent.click(screen.getByRole("button", { name: "Create skill" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "new-one", group: "Research" });
  });

  it("pre-fills the group when editing and reports a rejected group", async () => {
    setup({ save: () => jsonResponse({ detail: "group must be a short label" }, { status: 422 }) });
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("mine"));
    await userEvent.click(await screen.findByTitle("Edit"));
    expect(screen.getByLabelText("Group")).toHaveValue("Finance");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("⚠ group must be a short label")).toBeInTheDocument();
  });
});

describe("Skills: duplicate a built-in", () => {
  it("copies it and opens the copy in the editor", async () => {
    const { calls } = setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("audit"));
    await userEvent.click(await screen.findByTitle("Duplicate to customise"));
    expect(await screen.findByText("Edit “audit-custom”")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/skills/audit/duplicate")).toBe(true);
    expect(screen.getByLabelText("Group")).toHaveValue("Security");
    expect(screen.getByPlaceholderText("release-checklist")).toBeDisabled();
  });

  it("labels the button 'Duplicate' for your own skills and reports a failed copy", async () => {
    routeFetch({
      "GET /api/skills": () => jsonResponse(list),
      "GET /api/mcp": () => jsonResponse([]),
      "GET /api/skills/mine": () => jsonResponse(detail("mine")),
      "POST /api/skills/mine/duplicate": () => jsonResponse({ detail: "already exists" }, { status: 422 }),
    });
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("mine"));
    await userEvent.click(await screen.findByTitle("Duplicate"));
    expect(await screen.findByText("⚠ already exists")).toBeInTheDocument();
  });
});

describe("Skills: import", () => {
  const file = (text: string) => new File([text], "x-SKILL.md", { type: "text/markdown" });
  const input = () => screen.getByLabelText("Import SKILL.md") as HTMLInputElement;

  it("opens the file picker from the Import button", async () => {
    setup();
    render(<SkillsPage />);
    await ready();
    const click = vi.spyOn(input(), "click");
    await userEvent.click(screen.getByRole("button", { name: /Import/ }));
    expect(click).toHaveBeenCalled();
  });

  it("uploads the file text and selects the imported skill", async () => {
    const { calls } = setup();
    render(<SkillsPage />);
    await ready();
    fireEvent.change(input(), { target: { files: [file("---\nname: imported\n---\nbody")] } });
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/skills/import")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/skills/import")!.body).toEqual({ text: "---\nname: imported\n---\nbody" });
  });

  it("reports a rejected import and ignores an empty selection", async () => {
    const { calls } = setup({ imp: () => jsonResponse({ detail: "already exists" }, { status: 422 }) });
    render(<SkillsPage />);
    await ready();
    fireEvent.change(input(), { target: { files: [] } });
    expect(calls.some((c) => c.url.pathname === "/api/skills/import")).toBe(false);
    fireEvent.change(input(), { target: { files: [file("junk")] } });
    expect(await screen.findByText("⚠ Import failed: already exists")).toBeInTheDocument();
  });
});
