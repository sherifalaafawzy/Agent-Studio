import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import KnowledgePage from "@/app/knowledge/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/knowledge/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

// a field with no `default` key at all: the API may omit it
const kinds = [{ id: "local", label: "Local file", description: "SQLite", docker_stack: null,
  fields: [{ name: "file", label: "File name", secret: false, required: false, placeholder: "local.sqlite", default: "local.sqlite" },
           { name: "note", label: "Note", secret: false, required: false, placeholder: "" }] }];
const stores = [{ id: "local", label: "Local file", kind: "local", kind_label: "Local file", builtin: false, config: {}, secrets_set: {}, docker_stack: null, kbs: 0 }];
const kb = { id: "m", label: "Managed KB", description: "d", store_id: "local", store_label: "Local file", store_kind: "local", provider: "openai", model: "e", dim: 8,
  chunk_size: 1000, chunk_overlap: 100, docs: 0, chunks: 0, managed: true };
const providers = [{ id: "openai", label: "OpenAI", kind: "openai", enabled: true }, { id: "ollama", label: "Ollama", kind: "ollama", enabled: true }];

function setup(extra: Record<string, () => Response | Promise<Response>> = {}) {
  return routeFetch({
    "GET /api/rag/kinds": () => jsonResponse(kinds),
    "GET /api/rag/stores": () => jsonResponse(stores),
    "GET /api/rag/kbs": () => jsonResponse([kb]),
    "GET /api/providers": () => jsonResponse(providers),
    "GET /api/rag/stacks": () => jsonResponse([]),
    "GET /api/rag/kbs/m/docs": () => jsonResponse([]),
    ...extra,
  });
}
const dialog = () => screen.getByRole("dialog");
const toasts = () => {
  const seen: { text: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
};

describe("Knowledge: remaining behaviours", () => {
  it("flags a managed knowledge base in its detail view", async () => {
    setup();
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Open" }));
    await screen.findByText(/No documents yet/);
    expect(document.querySelector(".kn-detail")).toHaveTextContent("managed");
  });

  it("sends the description typed in the create form", async () => {
    const { calls } = setup({
      "GET /api/rag/embedding-models/openai": () => jsonResponse({ models: [] }),
      "POST /api/rag/kbs": () => jsonResponse({ ...kb, id: "x" }),
    });
    render(<KnowledgePage />);
    await screen.findByText("Managed KB");
    await userEvent.click(screen.getByRole("button", { name: /New knowledge base/ }));
    const d = within(dialog());
    await userEvent.type(d.getByPlaceholderText("product-docs"), "x");
    await userEvent.type(d.getByLabelText(/What it holds/), "Manuals for the product");
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "m");
    await userEvent.click(d.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect((calls.find((c) => c.method === "POST")!.body as any).description).toBe("Manuals for the product");
  });

  it("ignores a model list that arrives after the provider was changed", async () => {
    let late!: (r: Response) => void;
    setup({
      "GET /api/rag/embedding-models/openai": () => new Promise<Response>((r) => { late = r; }),
      "GET /api/rag/embedding-models/ollama": () => jsonResponse({ models: [{ model: "nomic-embed-text", dim: 768, input_per_m: 0 }] }),
    });
    render(<KnowledgePage />);
    await screen.findByText("Managed KB");
    await userEvent.click(screen.getByRole("button", { name: /New knowledge base/ }));
    const d = within(dialog());
    await userEvent.selectOptions(d.getByLabelText("Embedding provider"), "ollama");
    await waitFor(() => expect(dialog().querySelectorAll("#kn-models option")).toHaveLength(1));
    late(jsonResponse({ models: [{ model: "stale-a", dim: 1, input_per_m: 1 }, { model: "stale-b", dim: 1, input_per_m: 1 }] }));
    await new Promise((r) => setTimeout(r, 20));
    expect(dialog().querySelectorAll("#kn-models option")).toHaveLength(1);
  });

  it("ignores a model-list failure that arrives after the provider was changed", async () => {
    let late!: (r: Response) => void;
    setup({
      "GET /api/rag/embedding-models/openai": () => new Promise<Response>((r) => { late = r; }),
      "GET /api/rag/embedding-models/ollama": () => jsonResponse({ models: [{ model: "nomic-embed-text", dim: 768, input_per_m: 0 }] }),
    });
    render(<KnowledgePage />);
    await screen.findByText("Managed KB");
    await userEvent.click(screen.getByRole("button", { name: /New knowledge base/ }));
    await userEvent.selectOptions(within(dialog()).getByLabelText("Embedding provider"), "ollama");
    await waitFor(() => expect(dialog().querySelectorAll("#kn-models option")).toHaveLength(1));
    late(textResponse("down", 500));
    await new Promise((r) => setTimeout(r, 20));
    expect(within(dialog()).queryByText("HTTP 500")).toBeNull();
  });

  it("fills a store field from its default when the store has no value, and tolerates a field with no default", async () => {
    setup();
    render(<KnowledgePage />);
    await screen.findByText("Managed KB");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(within(dialog()).getByLabelText(/File name/)).toHaveValue("local.sqlite");
    expect(within(dialog()).getByLabelText(/Note/)).toHaveValue("");
  });

  it("names the store in the saved toast even when its label was cleared", async () => {
    const t = toasts();
    setup({ "PUT /api/rag/stores/local": () => jsonResponse(stores[0]) });
    render(<KnowledgePage />);
    await screen.findByText("Managed KB");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(within(dialog()).getByLabelText("Name"));
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Saved local")).toBe(true));
    t.stop();
  });
});
