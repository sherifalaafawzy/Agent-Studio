import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import MemoryPage from "@/components/LegacyMemoryGraph";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/memory/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const summary = { file: "C:/mem/memory.json", entities: 2, relations: 0, observations: 1, types: { person: 1, project: 1 }, exists: true };
const entities = [
  { name: "Ada", entityType: "person", observations: ["likes math"], relations: [] },
  { name: "Engine", entityType: "project", observations: [], relations: [] },
];

describe("Memory: edge cases", () => {
  it("keeps showing the opened memory when a later search no longer lists it", async () => {
    routeFetch({
      "GET /api/memory": () => jsonResponse(summary),
      "GET /api/memory/entities": (_i, url) => {
        const q = (url.searchParams.get("q") ?? "").toLowerCase();
        return jsonResponse(entities.filter((e) => !q || e.name.toLowerCase().includes(q)));
      },
    });
    render(<MemoryPage />);
    await userEvent.click(await screen.findByText("Ada"));
    expect(screen.getByText("likes math")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Search memories and facts"), "engine");
    await waitFor(() => expect(screen.queryByText("Ada", { selector: ".li .t" })).toBeNull());
    expect(screen.getByText("Engine", { selector: ".li .t" })).toBeInTheDocument();
    expect(screen.getByText("Ada", { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText("likes math")).toBeInTheDocument();
  });
});
