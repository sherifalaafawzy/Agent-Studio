import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SkillsPage from "@/app/skills/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/skills/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const list = [
  { name: "release", description: "Cut a release", mcp: [], source: "builtin" },
  { name: "triage", description: "Triage bug reports", mcp: ["git"], source: "user" },
];
const detailFor: Record<string, any> = {
  release: { name: "release", description: "Cut a release", mcp: [], source: "builtin", body: "## Steps\n- tag the **version**" },
  triage: { name: "triage", description: "Triage bug reports", mcp: ["git"], source: "user", body: "## Steps\n1. read the issue" },
};

function setup(over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/skills": over.list ?? (() => jsonResponse(list)),
    "GET /api/mcp": () => jsonResponse([{ id: "git", label: "Git", ready: true }, { id: "fs", label: "Filesystem", ready: true }]),
    "GET /api/skills/release": () => jsonResponse(detailFor.release),
    "GET /api/skills/triage": () => jsonResponse(detailFor.triage),
    "POST /api/skills": over.save ?? (() => jsonResponse({ name: "newskill", description: "Use it", mcp: [], source: "user", body: "## Steps" })),
    "DELETE /api/skills/triage": over.del ?? (() => jsonResponse({ ok: true })),
  });
}

describe("Skills: browsing", () => {
  it("opens the first skill and shows its details and body", async () => {
    setup();
    render(<SkillsPage />);
    expect(await screen.findByText("tag the")).toBeInTheDocument();
    expect(screen.getByText("Cut a release", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("built-in", { selector: ".card-head .pill" })).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("opens another skill and shows its MCP servers and ownership", async () => {
    setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("triage"));
    expect(await screen.findByText("git")).toBeInTheDocument();
    expect(screen.getByText("yours / agent-made")).toBeInTheDocument();
    expect(screen.getAllByText("yours")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("filters the list by name or description", async () => {
    setup();
    render(<SkillsPage />);
    await screen.findByText("tag the");
    await userEvent.type(screen.getByPlaceholderText("Search skills"), "bug");
    expect(document.querySelectorAll(".list .li")).toHaveLength(1);
    expect(screen.getByText("triage", { selector: ".list .t" })).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Search skills"), "zzz");
    expect(screen.getByText("No skills match.")).toBeInTheDocument();
  });

  it("explains when there are no skills at all", async () => {
    setup({ list: () => jsonResponse([]) });
    render(<SkillsPage />);
    expect(await screen.findByText("Select a skill, or create one.")).toBeInTheDocument();
  });

  it("reports when the backend is offline", async () => {
    setup({ list: () => textResponse("x", 500) });
    render(<SkillsPage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toBeInTheDocument();
  });
});

describe("Skills: writing", () => {
  it("starts a new skill with a template and previews the Markdown", async () => {
    setup();
    render(<SkillsPage />);
    await screen.findByText("tag the");
    await userEvent.click(screen.getByRole("button", { name: /New skill/ }));
    expect(screen.getByText("New skill", { selector: "h3" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.getByText("When to use")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Write" }));
    expect(document.querySelector("textarea")).toHaveValue("## When to use\n\n## Steps\n1. \n");
  });

  it("keeps Create disabled until a name and a use-case are given", async () => {
    setup();
    render(<SkillsPage />);
    await screen.findByText("tag the");
    await userEvent.click(screen.getByRole("button", { name: /New skill/ }));
    const create = screen.getByRole("button", { name: "Create skill" });
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText("release-checklist"), "newskill");
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText("the user asks to cut a release or bump versions"), "Use it");
    expect(create).toBeEnabled();
  });

  it("creates the skill with its MCP servers and selects it", async () => {
    const { calls } = setup();
    render(<SkillsPage />);
    await screen.findByText("tag the");
    await userEvent.click(screen.getByRole("button", { name: /New skill/ }));
    await userEvent.type(screen.getByPlaceholderText("release-checklist"), "newskill");
    await userEvent.type(screen.getByPlaceholderText("the user asks to cut a release or bump versions"), "Use it");
    await userEvent.click(screen.getByRole("checkbox", { name: "Git" }));
    await userEvent.click(screen.getByRole("button", { name: "Create skill" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "newskill", description: "Use it", mcp: ["git"], overwrite: false });
  });

  it("reports a rejected skill and keeps the draft", async () => {
    setup({ save: () => jsonResponse({ detail: "name must be lowercase" }, { status: 400 }) });
    render(<SkillsPage />);
    await screen.findByText("tag the");
    await userEvent.click(screen.getByRole("button", { name: /New skill/ }));
    await userEvent.type(screen.getByPlaceholderText("release-checklist"), "Bad Name");
    await userEvent.type(screen.getByPlaceholderText("the user asks to cut a release or bump versions"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Create skill" }));
    expect(await screen.findByText("⚠ name must be lowercase")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create skill" })).toBeInTheDocument();
  });

  it("cancels a draft", async () => {
    setup();
    render(<SkillsPage />);
    await screen.findByText("tag the");
    await userEvent.click(screen.getByRole("button", { name: /New skill/ }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("tag the")).toBeInTheDocument();
  });

  it("edits one of your own skills and saves it over the original", async () => {
    const { calls } = setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("triage"));
    await screen.findByText("read the issue");
    await userEvent.click(screen.getByTitle("Edit"));
    expect(screen.getByText('Edit “triage”')).toBeInTheDocument();
    expect(screen.getByPlaceholderText("release-checklist")).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "triage", overwrite: true });
  });

  it("does not offer to edit a built-in skill", async () => {
    setup();
    render(<SkillsPage />);
    await screen.findByText("tag the");
    expect(screen.queryByTitle("Edit")).toBeNull();
  });
});

describe("Skills: deleting", () => {
  it("deletes one of your skills after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("triage"));
    await screen.findByText("read the issue");
    await userEvent.click(screen.getByTitle("Delete"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(window.confirm).toHaveBeenCalledWith("Delete the skill “triage”?");
  });

  it("keeps the skill when deletion is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls } = setup();
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("triage"));
    await screen.findByText("read the issue");
    await userEvent.click(screen.getByTitle("Delete"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("reports a failed delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ del: () => jsonResponse({ detail: "locked" }, { status: 423 }) });
    render(<SkillsPage />);
    await userEvent.click(await screen.findByText("triage"));
    await screen.findByText("read the issue");
    await userEvent.click(screen.getByTitle("Delete"));
    expect(await screen.findByText("⚠ locked")).toBeInTheDocument();
  });
});
