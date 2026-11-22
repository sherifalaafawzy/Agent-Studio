import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "@/components/LegacyMemoryGraph";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/memory/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const summary = { file: "C:/mem/memory.json", entities: 2, relations: 1, observations: 2, types: { person: 1, project: 1 }, exists: true };
const entities = [
  { name: "Ada", entityType: "person", observations: ["likes math", "works at Analytical"], relations: [{ from: "Ada", to: "Engine", relationType: "owns" }] },
  { name: "Engine", entityType: "project", observations: [], relations: [] },
];

/** Filters the fixture the way the server does, so search and type filters behave realistically. */
function searchable(url: URL) {
  const q = (url.searchParams.get("q") ?? "").toLowerCase();
  const type = url.searchParams.get("type") ?? "";
  return entities.filter((e) => (!q || e.name.toLowerCase().includes(q)) && (!type || e.entityType === type));
}

function toasts() {
  const seen: { text: string; tone: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
}

function setup(over: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/memory": over.summary ?? (() => jsonResponse(summary)),
    "GET /api/memory/entities": over.entities ?? ((_init, url) => jsonResponse(searchable(url))),
    "PUT /api/memory/entities/Ada": () => jsonResponse({ ok: true }),
    "DELETE /api/memory/entities/Ada": () => jsonResponse({ ok: true }),
    "DELETE /api/memory/entities/Ada/observations/0": () => jsonResponse({ ok: true }),
    "POST /api/memory/clear": () => jsonResponse({ ok: true }),
  });
}

async function openAda() {
  await userEvent.click(await screen.findByText("Ada"));
}

beforeEach(() => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("Memory: browsing", () => {
  it("shows the totals and the list of remembered things", async () => {
    setup();
    render(<MemoryPage />);
    expect(await screen.findByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("Things remembered").nextSibling).toHaveTextContent("2");
    expect(screen.getByText("Facts").nextSibling).toHaveTextContent("2");
    expect(screen.getByText("Links between them").nextSibling).toHaveTextContent("1");
    expect(screen.getByText("person")).toBeInTheDocument();
    expect(screen.getByText("Pick a memory to see its facts and links.")).toBeInTheDocument();
  });

  it("shows a memory's facts and links when it is picked", async () => {
    setup();
    render(<MemoryPage />);
    await userEvent.click(await screen.findByText("Ada"));
    expect(screen.getByText("likes math")).toBeInTheDocument();
    expect(screen.getByText("works at Analytical")).toBeInTheDocument();
    expect(screen.getByText("owns").closest(".small")).toHaveTextContent("Ada owns Engine");
  });

  it("says a memory with no facts has none", async () => {
    setup();
    render(<MemoryPage />);
    await userEvent.click(await screen.findByText("Engine"));
    expect(screen.getByText("No facts.")).toBeInTheDocument();
    expect(screen.queryByText("Links")).toBeNull();
  });

  it("explains an empty memory", async () => {
    setup({ summary: () => jsonResponse({ ...summary, exists: false, entities: 0 }), entities: () => jsonResponse([]) });
    render(<MemoryPage />);
    expect(await screen.findByText(/Nothing is remembered yet/)).toBeInTheDocument();
  });

  it("says when a search finds nothing", async () => {
    setup();
    render(<MemoryPage />);
    await screen.findByText("Ada");
    await userEvent.type(screen.getByPlaceholderText("Search memories and facts"), "zzz");
    expect(await screen.findByText("No matches.")).toBeInTheDocument();
  });

  it("searches on the server, after a short pause", async () => {
    const { calls } = setup();
    render(<MemoryPage />);
    await screen.findByText("Ada");
    await userEvent.type(screen.getByPlaceholderText("Search memories and facts"), "ada");
    await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("q") === "ada")).toBe(true));
  });

  it("filters by type", async () => {
    const { calls } = setup();
    render(<MemoryPage />);
    await screen.findByText("Ada");
    await userEvent.selectOptions(screen.getByRole("combobox"), "person");
    await waitFor(() => expect(calls.some((c) => c.url.searchParams.get("type") === "person")).toBe(true));
  });

  it("reports a failure to load the list", async () => {
    setup({ entities: () => textResponse("db locked", 500) });
    render(<MemoryPage />);
    expect(await screen.findByText("HTTP 500")).toHaveClass("err");
  });
});

describe("Memory: correcting facts", () => {
  it("edits facts, adds one, removes one and saves", async () => {
    const t = toasts();
    const { calls } = setup();
    const { container } = render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getByTitle("Edit facts"));
    expect(container.querySelectorAll(".card input")).toHaveLength(2);
    const first = screen.getByDisplayValue("likes math");
    await userEvent.clear(first);
    await userEvent.type(first, "loves maths");
    // drop the second fact using its own row's remove button
    const second = screen.getByDisplayValue("works at Analytical");
    await userEvent.click(second.closest(".row")!.querySelector("button")!);
    await userEvent.click(screen.getByRole("button", { name: "+ Add a fact" }));
    const inputs = container.querySelectorAll<HTMLInputElement>(".card input");
    await userEvent.type(inputs[inputs.length - 1], "new fact");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(t.seen.some((x) => x.text.startsWith("Memory updated"))).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({ observations: ["loves maths", "new fact"] });
    t.stop();
  });

  it("cancels an edit without saving", async () => {
    const { calls } = setup();
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getByTitle("Edit facts"));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("likes math")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("reports a failed save", async () => {
    const t = toasts();
    setup();
    routeFetch({
      "GET /api/memory": () => jsonResponse(summary),
      "GET /api/memory/entities": () => jsonResponse(entities),
      "PUT /api/memory/entities/Ada": () => jsonResponse({ detail: "locked" }, { status: 423 }),
    });
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getByTitle("Edit facts"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(t.seen.some((x) => x.tone === "err" && x.text === "locked")).toBe(true));
    t.stop();
  });

  it("removes a single fact from the reading view", async () => {
    const t = toasts();
    const { calls } = setup();
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getAllByTitle("Remove this fact")[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")!.url.pathname).toBe("/api/memory/entities/Ada/observations/0");
    t.stop();
  });

  it("reports a fact that could not be removed", async () => {
    const t = toasts();
    routeFetch({
      "GET /api/memory": () => jsonResponse(summary),
      "GET /api/memory/entities": () => jsonResponse(entities),
      "DELETE /api/memory/entities/Ada/observations/0": () => jsonResponse({ detail: "gone" }, { status: 404 }),
    });
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getAllByTitle("Remove this fact")[0]);
    await waitFor(() => expect(t.seen.some((x) => x.text === "gone")).toBe(true));
    t.stop();
  });
});

describe("Memory: forgetting", () => {
  it("forgets a memory after confirmation", async () => {
    const t = toasts();
    const { calls } = setup();
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getByTitle("Forget"));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Forgot “Ada”")).toBe(true));
    expect(calls.some((c) => c.method === "DELETE" && c.url.pathname === "/api/memory/entities/Ada")).toBe(true);
    t.stop();
  });

  it("keeps the memory when the user declines", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const { calls } = setup();
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getByTitle("Forget"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("reports a failure to forget", async () => {
    const t = toasts();
    routeFetch({
      "GET /api/memory": () => jsonResponse(summary),
      "GET /api/memory/entities": () => jsonResponse(entities),
      "DELETE /api/memory/entities/Ada": () => jsonResponse({ detail: "protected" }, { status: 403 }),
    });
    render(<MemoryPage />);
    await openAda();
    await userEvent.click(screen.getByTitle("Forget"));
    await waitFor(() => expect(t.seen.some((x) => x.text === "protected")).toBe(true));
    t.stop();
  });

  it("clears all memory only when the exact phrase is typed", async () => {
    const t = toasts();
    const { calls } = setup();
    vi.spyOn(window, "prompt").mockReturnValueOnce("delete all memory").mockReturnValueOnce("DELETE ALL MEMORY");
    render(<MemoryPage />);
    await screen.findByText("Ada");
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(calls.some((c) => c.url.pathname === "/api/memory/clear")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Memory cleared (backup kept)")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/memory/clear")!.body).toEqual({ confirm: "DELETE ALL MEMORY" });
    t.stop();
  });

  it("reports a failed clear", async () => {
    const t = toasts();
    routeFetch({
      "GET /api/memory": () => jsonResponse(summary),
      "GET /api/memory/entities": () => jsonResponse(entities),
      "POST /api/memory/clear": () => jsonResponse({ detail: "busy" }, { status: 409 }),
    });
    vi.spyOn(window, "prompt").mockReturnValue("DELETE ALL MEMORY");
    render(<MemoryPage />);
    await screen.findByText("Ada");
    await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "busy")).toBe(true));
    t.stop();
  });

  it("disables clearing when there is nothing remembered", async () => {
    setup({ summary: () => jsonResponse({ ...summary, entities: 0, exists: false }), entities: () => jsonResponse([]) });
    render(<MemoryPage />);
    await screen.findByText(/Nothing is remembered yet/);
    expect(screen.getByRole("button", { name: "Clear all" })).toBeDisabled();
  });
});
