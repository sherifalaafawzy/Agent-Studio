import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SkillsPage from "@/app/skills/page";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/skills/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const triage = { name: "triage", description: "Triage bug reports", mcp: ["git"], source: "user" };

function setup(detail: Record<string, any>) {
  return routeFetch({
    "GET /api/skills": () => jsonResponse([triage]),
    "GET /api/mcp": () => jsonResponse([{ id: "git", label: "Git", ready: true }, { id: "fs", label: "Filesystem", ready: true }]),
    "GET /api/skills/triage": () => jsonResponse(detail),
    "POST /api/skills": (init) => jsonResponse({ ...triage, ...JSON.parse(String(init?.body)) }),
  });
}

describe("Skills: edge cases", () => {
  it("unticks and ticks MCP servers while editing, and saves the result", async () => {
    const { calls } = setup({ ...triage, body: "## Steps" });
    render(<SkillsPage />);
    await userEvent.click(await screen.findByTitle("Edit"));
    const git = screen.getByLabelText("Git");
    const fs = screen.getByLabelText("Filesystem");
    expect(git).toBeChecked();
    expect(fs).not.toBeChecked();
    await userEvent.click(git);
    await userEvent.click(fs);
    expect(git).not.toBeChecked();
    expect(fs).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ name: "triage", mcp: ["fs"], overwrite: true, body: "## Steps" });
  });

  it("opens a skill that has no body as an empty skill and edits it with an empty body", async () => {
    const { calls } = setup({ ...triage });
    const { container } = render(<SkillsPage />);
    expect(await screen.findByText("Triage bug reports", { selector: "span" })).toBeInTheDocument();
    expect(container.querySelector(".card .md")).toBeEmptyDOMElement();
    await userEvent.click(screen.getByTitle("Edit"));
    expect(container.querySelector("textarea")).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ body: "", mcp: ["git"], overwrite: true });
  });
});
