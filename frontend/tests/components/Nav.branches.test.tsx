import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Nav from "@/components/Nav";
import { jsonResponse, routeFetch } from "../helpers/http";

const nav = vi.hoisted(() => ({ path: null as string | null, search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push }),
}));

beforeEach(() => { nav.path = null; nav.search = ""; });

describe("Sidebar without a resolved pathname", () => {
  it("treats a null pathname as the home page, so the ?s= chat is highlighted and the home link is active", async () => {
    nav.search = "s=b2";
    routeFetch({
      "GET /api/chat/sessions": () => jsonResponse([
        { id: "a1", title: "Planning chat", agent: null, provider: "ollama", updated: 1 },
        { id: "b2", title: "Review chat", agent: "engineer", provider: "openai", updated: 2 },
      ]),
    });
    render(<Nav />);
    await screen.findByText("Review chat");
    expect(screen.getByTitle("engineer · openai")).toHaveClass("on");
    expect(screen.getByTitle("plain chat · ollama")).not.toHaveClass("on");
    expect(screen.getByTitle("Chat")).toHaveClass("on");   // "/" is the fallback path
    expect(screen.getByTitle("Settings")).not.toHaveClass("on");
  });
});

describe("Sidebar guided tour button", () => {
  it("asks the tour runner to start the welcome tour", async () => {
    routeFetch({ "GET /api/chat/sessions": () => jsonResponse([]) });
    const seen: any[] = [];
    window.addEventListener("as:tour-start", (e) => seen.push((e as CustomEvent).detail));
    render(<Nav />);
    await waitFor(() => expect(screen.getByText("Connected")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Take the guided tour" }));
    expect(seen).toEqual([{ tour: "welcome" }]);
  });
});
