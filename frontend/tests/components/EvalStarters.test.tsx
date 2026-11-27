import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

const toast = vi.fn();
vi.mock("@/components/Nav", () => ({ toast: (...a: unknown[]) => toast(...a) }));
import EvalStarters from "@/components/EvalStarters";
import EvalsPage from "@/app/evals/page";

const LIST = (added: boolean) => ({ starters: [{ id: "science-citations", name: "Science: citation validity", description: "Real DOIs", cases: 6, added },
  { id: "science-refusal", name: "Science: refusal behaviour", description: "Refuse the harmful", cases: 7, added: false }] });

describe("Ready-made evaluation sets", () => {
  it("adds one, then offers to reset it, and shows errors", async () => {
    let added = false;
    routeFetch({
      "GET /api/evals/starters": () => jsonResponse(LIST(added)),
      "POST /api/evals/starters/science-citations": () => { added = true; return jsonResponse({ id: "science-citations" }); },
      "POST /api/evals/starters/science-refusal": () => jsonResponse({ detail: "a test set needs between 1 and 200 cases" }, { status: 422 }),
    });
    const onAdded = vi.fn();
    const user = userEvent.setup();
    render(<EvalStarters onAdded={onAdded} />);
    await user.click(await screen.findByLabelText("Add Science: citation validity"));
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("science-citations"));
    expect(toast).toHaveBeenCalledWith("Science: citation validity added");
    await user.click(await screen.findByLabelText("Reset Science: citation validity"));
    expect(toast).toHaveBeenLastCalledWith("Science: citation validity reset to the original");
    await user.click(screen.getByLabelText("Add Science: refusal behaviour"));
    expect(await screen.findByRole("alert")).toHaveTextContent("between 1 and 200 cases");
  });

  it("shows nothing when the list cannot be loaded, and opens an added set on the Evals page", async () => {
    routeFetch({ "GET /api/evals/starters": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    const { container, unmount } = render(<EvalStarters onAdded={() => {}} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    unmount();
    const { calls } = routeFetch({
      "GET /api/evals/sets": () => jsonResponse({ sets: [], exports: {} }), "GET /api/agents": () => jsonResponse([]), "GET /api/providers": () => jsonResponse([]),
      "GET /api/evals/starters": () => jsonResponse(LIST(false)), "POST /api/evals/starters/science-citations": () => jsonResponse({ id: "science-citations" }),
      "GET /api/evals/sets/science-citations": () => jsonResponse({ id: "science-citations", name: "Science: citation validity", description: "", cases: [{ id: "c1", prompt: "doi?", expect: [] }] }),
      "GET /api/evals/runs": () => jsonResponse({ runs: [] }),
    });
    const user = userEvent.setup();
    render(<EvalsPage />);
    await user.click(await screen.findByLabelText("Add Science: citation validity"));
    expect(await screen.findByDisplayValue("doi?")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.pathname === "/api/evals/sets").length).toBeGreaterThan(1);
  });
});
