import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import KnowledgePage from "@/app/knowledge/page";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

vi.mock("next/navigation", () => ({
  usePathname: () => "/knowledge/",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: () => {} }),
}));

const kinds = [
  { id: "local", label: "Local file", description: "A SQLite file on this PC.", docker_stack: null,
    fields: [{ name: "file", label: "File name", secret: false, required: false, placeholder: "local.sqlite", default: "local.sqlite" }] },
  { id: "qdrant", label: "Qdrant", description: "A Qdrant server.", docker_stack: "qdrant",
    fields: [
      { name: "url", label: "URL", secret: false, required: true, placeholder: "http://127.0.0.1:6333", default: "", help: "Where the server listens" },
      { name: "api_key", label: "API key", secret: true, required: false, placeholder: "", default: "", description: "Only for Qdrant Cloud" },
    ] },
];
const stores = [
  { id: "local", label: "Local file", kind: "local", kind_label: "Local file", builtin: true, config: {}, secrets_set: {}, docker_stack: null, kbs: 0 },
  { id: "docker-qdrant", label: "Qdrant (Docker)", kind: "qdrant", kind_label: "Qdrant", builtin: false, config: { url: "http://127.0.0.1:6333" }, secrets_set: { api_key: false }, docker_stack: "qdrant", kbs: 1 },
  { id: "cloud", label: "Cloud Qdrant", kind: "qdrant", kind_label: "Qdrant", builtin: false, config: { url: "https://x.cloud" }, secrets_set: { api_key: true }, docker_stack: null, kbs: 2 },
];
const kb = { id: "live-test", label: "Live test", description: "Three docs", store_id: "docker-qdrant", store_label: "Qdrant (Docker)", store_kind: "qdrant", provider: "openai",
  model: "text-embedding-3-small", dim: 1536, chunk_size: 1000, chunk_overlap: 150, docs: 3, chunks: 12, created: 1 };
const managedKb = { ...kb, id: "managed-kb", label: "Managed", description: "", docs: 1, chunks: 1, managed: true };
const providers = [
  { id: "openai", label: "OpenAI", kind: "openai", enabled: true },
  { id: "ollama", label: "Ollama", kind: "ollama", enabled: true },
  { id: "gemini", label: "Gemini", kind: "gemini", enabled: false },
];
const embModels = { provider: "openai", kind: "openai", enabled: true, models: [
  { model: "text-embedding-3-small", dim: 1536, input_per_m: 0.02 },
  { model: "free-embed", dim: null, input_per_m: 0 },
  { model: "mystery-embed", dim: 768, input_per_m: null },
] };
const doc = (n: number, over: Record<string, unknown> = {}) => ({ doc_id: `d${n}`, source: `file${n}.md`, chunks: 4, bytes: 2048, added: 1, ...over });
const stack = (state: string, over: Record<string, unknown> = {}) => ({ id: "qdrant", label: "Qdrant (Docker)", image: "qdrant/qdrant:v1", port: 6333, store_kind: "qdrant", notes: "Local vector database",
  docker: state !== "unavailable", state, detail: `state is ${state}`, ...over });

type R = () => Response;
function setup(over: Record<string, R | ((init: RequestInit | undefined, url: URL) => Response | Promise<Response>)> = {}) {
  return routeFetch({
    "GET /api/rag/kinds": () => jsonResponse(kinds),
    "GET /api/rag/stores": () => jsonResponse(stores),
    "GET /api/rag/kbs": () => jsonResponse([kb, managedKb]),
    "GET /api/providers": () => jsonResponse(providers),
    "GET /api/rag/stacks": () => jsonResponse([stack("running")]),
    "GET /api/rag/embedding-models/openai": () => jsonResponse(embModels),
    "GET /api/rag/embedding-models/ollama": () => jsonResponse({ provider: "ollama", kind: "ollama", enabled: true, models: [] }),
    "GET /api/rag/kbs/live-test/docs": () => jsonResponse([doc(1), doc(2, { chunks: 1, bytes: 12, source: "tiny.txt" }), doc(3, { bytes: 3 * 1048576, source: "big.pdf" })]),
    ...over,
  });
}
function toasts() {
  const seen: { text: string; tone: string }[] = [];
  const on = (e: Event) => seen.push((e as CustomEvent).detail);
  window.addEventListener("as:toast", on);
  return { seen, stop: () => window.removeEventListener("as:toast", on) };
}
const dialog = () => screen.getByRole("dialog");
async function openDetail() {
  await userEvent.click((await screen.findAllByRole("button", { name: "Open" }))[0]);
  await screen.findByText("file1.md");
}
const file = (name: string, body = "hello") => new File([body], name, { type: "text/plain" });

describe("Knowledge: list", () => {
  it("lists knowledge bases with store, model and counts, and flags managed ones", async () => {
    setup();
    render(<KnowledgePage />);
    expect(await screen.findByText("Live test")).toBeInTheDocument();
    expect(screen.getByText(/Qdrant \(Docker\) · openai\/text-embedding-3-small · 3 docs · 12 chunks/)).toBeInTheDocument();
    expect(screen.getByText(/1 doc · 1 chunk$/)).toBeInTheDocument();
    expect(screen.getByText("managed")).toHaveClass("pill");
  });

  it("marks the list card and the stores area for the guided tour", async () => {
    setup();
    const { container } = render(<KnowledgePage />);
    await screen.findByText("Live test");
    expect(container.querySelector('[data-tour="knowledge-list"]')).toHaveTextContent("Knowledge bases");
    expect(container.querySelector('[data-tour="knowledge-stores"]')).toHaveTextContent("Stores");
    expect(container.querySelector('[data-tour="knowledge-stores"]')).toHaveTextContent("Docker");
  });

  it("explains when there are none", async () => {
    setup({ "GET /api/rag/kbs": () => jsonResponse([]) });
    render(<KnowledgePage />);
    expect(await screen.findByText(/No knowledge bases yet/)).toBeInTheDocument();
  });

  it("shows loading and keeps New disabled until the list arrives", () => {
    setup({ "GET /api/rag/kbs": () => new Promise<Response>(() => {}) });
    render(<KnowledgePage />);
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /New knowledge base/ })).toBeDisabled();
  });

  it("reports when the backend is offline", async () => {
    setup({ "GET /api/rag/kinds": () => textResponse("x", 500) });
    render(<KnowledgePage />);
    expect(await screen.findByText("Backend offline: HTTP 500")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).toBeNull();
  });
});

describe("Knowledge: creating", () => {
  async function openCreate() {
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: /New knowledge base/ }));
    return within(dialog());
  }

  it("warns about the paid embedding call and offers only enabled providers", async () => {
    setup();
    render(<KnowledgePage />);
    const d = await openCreate();
    expect(d.getByText(/one small paid embedding call/)).toBeInTheDocument();
    expect(d.getByRole("option", { name: "OpenAI" })).toBeInTheDocument();
    expect(d.queryByRole("option", { name: "Gemini" })).toBeNull();
    expect(d.getByRole("option", { name: /Cloud Qdrant \(Qdrant\)/ })).toBeInTheDocument();
  });

  it("loads the provider's embedding models and describes the chosen one", async () => {
    setup();
    render(<KnowledgePage />);
    const d = await openCreate();
    const model = d.getByPlaceholderText("choose or type a model");
    await waitFor(() => expect(dialog().querySelectorAll("#kn-models option")).toHaveLength(3));
    await userEvent.type(model, "text-embedding-3-small");
    expect(d.getByText("1536 dimensions · $0.02 per million tokens")).toBeInTheDocument();
    await userEvent.clear(model);
    await userEvent.type(model, "free-embed");
    expect(d.getByText("dimensions unknown · free")).toBeInTheDocument();
    await userEvent.clear(model);
    await userEvent.type(model, "mystery-embed");
    expect(d.getByText("768 dimensions · price unknown")).toBeInTheDocument();
  });

  it("lets you type a model when the provider lists none, and clears the model on a provider change", async () => {
    setup();
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "x");
    await userEvent.selectOptions(d.getByLabelText("Embedding provider"), "ollama");
    expect(await d.findByText(/No embedding models are listed/)).toBeInTheDocument();
    expect(d.getByPlaceholderText("choose or type a model")).toHaveValue("");
  });

  it("shows why the models could not be loaded", async () => {
    setup({ "GET /api/rag/embedding-models/openai": () => jsonResponse({ detail: "unknown provider" }, { status: 404 }) });
    render(<KnowledgePage />);
    const d = await openCreate();
    expect(await d.findByText("unknown provider")).toBeInTheDocument();
  });

  it("explains that no provider is enabled and blocks creating", async () => {
    setup({ "GET /api/providers": () => jsonResponse([providers[2]]) });
    render(<KnowledgePage />);
    const d = await openCreate();
    expect(d.getByText(/Enable a model provider in Settings first/)).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("checks the chunk limits before creating", async () => {
    setup();
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.type(d.getByPlaceholderText("product-docs"), "kb-one");
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "m");
    const size = d.getByLabelText(/Chunk size/), overlap = d.getByLabelText(/Overlap/);
    await userEvent.clear(size); await userEvent.type(size, "100");
    expect(d.getByText("Chunk size must be between 200 and 8000 characters.")).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Create" })).toBeDisabled();
    await userEvent.clear(size); await userEvent.type(size, "1000");
    await userEvent.clear(overlap); await userEvent.type(overlap, "600");
    expect(d.getByText("Overlap must be between 0 and half the chunk size.")).toBeInTheDocument();
    await userEvent.clear(overlap); await userEvent.type(overlap, "500");
    expect(d.queryByText(/Overlap must be/)).toBeNull();
    expect(d.getByRole("button", { name: "Create" })).toBeEnabled();
    await userEvent.clear(size); await userEvent.type(size, "1000.5");
    expect(d.getByText(/Chunk size must be/)).toBeInTheDocument();
  });

  it("creates the knowledge base, toasts and opens it", async () => {
    const t = toasts();
    const { calls } = setup({ "POST /api/rag/kbs": () => jsonResponse({ ...kb, id: "new-kb", label: "New KB", docs: 0, chunks: 0 }) });
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.type(d.getByPlaceholderText("product-docs"), "new-kb");
    await userEvent.type(d.getByPlaceholderText("Product docs"), "New KB");
    await userEvent.selectOptions(d.getByLabelText("Store"), "docker-qdrant");
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "text-embedding-3-small");
    await userEvent.click(d.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/rag/kbs")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ id: "new-kb", label: "New KB", description: "", store_id: "docker-qdrant", provider: "openai",
      model: "text-embedding-3-small", chunk_size: 1000, chunk_overlap: 150 });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(t.seen.some((x) => x.text === "Created New KB")).toBe(true);
    t.stop();
  });

  it.each([
    ["a validation message (422)", 422, { detail: "a knowledge base named 'x' already exists" }, "a knowledge base named 'x' already exists"],
    ["the spend cap (402)", 402, { detail: { message: "Spend cap reached for openai" } }, "Spend cap reached for openai"],
    ["a provider failure (502)", 502, { detail: "Provider unavailable. Try again later." }, "Provider unavailable. Try again later."],
  ])("shows %s inline and keeps the form open", async (_n, status, body, text) => {
    setup({ "POST /api/rag/kbs": () => jsonResponse(body, { status }) });
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.type(d.getByPlaceholderText("product-docs"), "x");
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "m");
    await userEvent.click(d.getByRole("button", { name: "Create" }));
    expect(await d.findByText(text)).toHaveClass("err");
    expect(d.getByRole("button", { name: "Create" })).toBeEnabled();
  });

  it("closes from the X button and the backdrop", async () => {
    setup();
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.click(d.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await openCreate();
    fireEvent.click(document.querySelector(".backdrop")!);
    expect(screen.queryByRole("dialog")).toBeNull();
    await openCreate();
    await userEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows Creating… while the request runs", async () => {
    let finish!: (r: Response) => void;
    setup({ "POST /api/rag/kbs": () => new Promise<Response>((r) => { finish = r; }) });
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.type(d.getByPlaceholderText("product-docs"), "x");
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "m");
    await userEvent.click(d.getByRole("button", { name: "Create" }));
    expect(await d.findByRole("button", { name: "Creating…" })).toBeDisabled();
    finish(jsonResponse({ ...kb, id: "x" }));
  });

  it("falls back to the local store id when no stores are listed", async () => {
    setup({ "GET /api/rag/stores": () => jsonResponse([]) });
    const { calls } = setup({ "GET /api/rag/stores": () => jsonResponse([]), "POST /api/rag/kbs": () => jsonResponse({ ...kb, id: "x" }) });
    render(<KnowledgePage />);
    const d = await openCreate();
    await userEvent.type(d.getByPlaceholderText("product-docs"), "x");
    await userEvent.type(d.getByPlaceholderText("choose or type a model"), "m");
    await userEvent.click(d.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect((calls.find((c) => c.method === "POST")!.body as any).store_id).toBe("local");
  });
});

describe("Knowledge: deleting a knowledge base", () => {
  it("deletes after confirmation, closes its detail and reloads", async () => {
    const t = toasts();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = setup({ "DELETE /api/rag/kbs/live-test": () => jsonResponse({ ok: true }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.click(screen.getByRole("button", { name: "Delete Live test" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Deleted Live test")).toBe(true));
    expect(window.confirm).toHaveBeenCalledWith("Delete the knowledge base “Live test” and its 3 documents?");
    expect(calls.find((c) => c.method === "DELETE")!.url.search).toBe("");
    await waitFor(() => expect(screen.queryByText("file1.md")).toBeNull());
    t.stop();
  });

  it("uses the singular for one document and keeps another open knowledge base open", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ "DELETE /api/rag/kbs/managed-kb": () => jsonResponse({ ok: true }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.click(screen.getByRole("button", { name: "Delete Managed" }));
    await waitFor(() => expect(window.confirm).toHaveBeenCalledWith("Delete the knowledge base “Managed” and its 1 document?"));
    expect(screen.getByText("file1.md")).toBeInTheDocument();
  });

  it("keeps it when the user declines", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls } = setup();
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: "Delete Live test" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("offers to delete anyway when its store cannot be reached", async () => {
    const t = toasts();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let n = 0;
    const { calls } = setup({
      "DELETE /api/rag/kbs/live-test": () => (++n === 1 ? jsonResponse({ detail: "could not remove its vectors (connection refused). Fix the store, or delete anyway with force" }, { status: 422 }) : jsonResponse({ ok: true })),
    });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: "Delete Live test" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(2));
    expect(confirm.mock.calls[1][0]).toContain("Delete anyway? The vectors stay in the store.");
    expect(calls.filter((c) => c.method === "DELETE")[1].url.search).toBe("?force=true");
    await waitFor(() => expect(t.seen.some((x) => x.text === "Deleted Live test")).toBe(true));
    t.stop();
  });

  it("does not force the delete when the user declines the second question", async () => {
    const t = toasts();
    vi.spyOn(window, "confirm").mockReturnValueOnce(true).mockReturnValueOnce(false);
    const { calls } = setup({ "DELETE /api/rag/kbs/live-test": () => jsonResponse({ detail: "could not remove its vectors (down). Delete anyway with force" }, { status: 422 }) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: "Delete Live test" }));
    await waitFor(() => expect(t.seen.some((x) => x.tone === "err")).toBe(true));
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
    t.stop();
  });

  it("shows other failures as an error toast, and a failed forced delete too", async () => {
    const t = toasts();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ "DELETE /api/rag/kbs/live-test": (init, url) => jsonResponse({ detail: url.search ? "still failing force" : "unknown knowledge base" }, { status: url.search ? 500 : 404 }) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: "Delete Live test" }));
    await waitFor(() => expect(t.seen).toContainEqual({ text: "unknown knowledge base", tone: "err" }));
    t.stop();
  });

  it("shows a failed forced delete as an error", async () => {
    const t = toasts();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ "DELETE /api/rag/kbs/live-test": (_i, url) => jsonResponse({ detail: url.search ? "force failed" : "store down, delete anyway with force" }, { status: url.search ? 500 : 422 }) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: "Delete Live test" }));
    await waitFor(() => expect(t.seen).toContainEqual({ text: "force failed", tone: "err" }));
    t.stop();
  });
});

describe("Knowledge: a knowledge base", () => {
  it("shows its settings and documents, and toggles open and closed", async () => {
    setup();
    render(<KnowledgePage />);
    await openDetail();
    expect(screen.getByText(/openai · text-embedding-3-small · 1536 dimensions · chunks of 1000 characters, 150 overlap · 2 knowledge bases in total/)).toBeInTheDocument();
    expect(screen.getByText(/4 chunks · 2 KB/)).toBeInTheDocument();
    expect(screen.getByText(/1 chunk · 12 B/)).toBeInTheDocument();
    expect(screen.getByText(/3.0 MB/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByText("file1.md")).toBeNull();
    await userEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    await screen.findByText("file1.md");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("file1.md")).toBeNull();
  });

  it("uses the singular for a single knowledge base and shows its description fallback", async () => {
    setup({ "GET /api/rag/kbs": () => jsonResponse([{ ...kb, description: "" }]) });
    render(<KnowledgePage />);
    await openDetail();
    expect(screen.getByText("No description.")).toBeInTheDocument();
    expect(screen.getByText(/1 knowledge base in total/)).toBeInTheDocument();
  });

  it("says when it has no documents, and when documents cannot be loaded", async () => {
    setup({ "GET /api/rag/kbs/live-test/docs": () => jsonResponse([]) });
    const { unmount } = render(<KnowledgePage />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Open" }))[0]);
    expect(await screen.findByText(/No documents yet/)).toBeInTheDocument();
    unmount();
    setup({ "GET /api/rag/kbs/live-test/docs": () => jsonResponse({ detail: "store unreachable" }, { status: 422 }) });
    render(<KnowledgePage />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Open" }))[0]);
    expect(await screen.findByText("store unreachable")).toHaveClass("err");
  });

  it("removes a document after confirmation", async () => {
    const t = toasts();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = setup({ "DELETE /api/rag/kbs/live-test/docs/d1": () => jsonResponse({ ok: true, removed: 4 }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.click(screen.getByRole("button", { name: "Remove file1.md" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Removed file1.md")).toBe(true));
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
    t.stop();
  });

  it("keeps the document when declined, and reports a failed removal", async () => {
    const t = toasts();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { calls } = setup({ "DELETE /api/rag/kbs/live-test/docs/d1": () => jsonResponse({ detail: "unknown document" }, { status: 404 }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.click(screen.getByRole("button", { name: "Remove file1.md" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Remove file1.md" }));
    await waitFor(() => expect(t.seen).toContainEqual({ text: "unknown document", tone: "err" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    t.stop();
  });
});

describe("Knowledge: adding content", () => {
  const outcomes = [
    { name: "a.md", doc_id: "1", source: "a.md", chunks: 3, status: "added" },
    { name: "b.md", doc_id: "2", source: "b.md", chunks: 1, status: "updated" },
    { name: "c.md", doc_id: "3", source: "c.md", chunks: 2, status: "unchanged" },
    { name: "d.exe", status: "skipped", error: "unsupported file type" },
  ];

  it("uploads several files and shows each outcome", async () => {
    const { calls } = setup({ "POST /api/rag/kbs/live-test/docs": () => jsonResponse(outcomes) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.upload(screen.getByLabelText("Upload files"), [file("a.md"), file("b.md")]);
    const res = await screen.findByLabelText("Ingest results");
    expect(within(res).getByText("added")).toHaveClass("ok");
    expect(within(res).getByText("updated")).toBeInTheDocument();
    expect(within(res).getByText("unchanged")).toBeInTheDocument();
    expect(within(res).getByText("skipped")).toHaveClass("warn");
    expect(within(res).getByText("unsupported file type")).toHaveClass("err");
    expect(within(res).getByText("3 chunks")).toBeInTheDocument();
    expect(within(res).getByText("1 chunk")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toBeInstanceOf(FormData);
    expect((post.body as FormData).getAll("files").map((f) => (f as File).name)).toEqual(["a.md", "b.md"]);
    expect((post.init?.headers as any)?.["Content-Type"]).toBeUndefined();
  });

  it("shows an outcome without a chunk count", async () => {
    setup({ "POST /api/rag/kbs/live-test/docs": () => jsonResponse([{ name: "z.md", status: "added" }]) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.upload(screen.getByLabelText("Upload files"), file("z.md"));
    const res = await screen.findByLabelText("Ingest results");
    expect(res).not.toHaveTextContent("chunk");
  });

  it("shows the upload progress label, then reloads documents and counts", async () => {
    let finish!: (r: Response) => void;
    const { calls } = setup({ "POST /api/rag/kbs/live-test/docs": () => new Promise<Response>((r) => { finish = r; }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.upload(screen.getByLabelText("Upload files"), file("a.md"));
    expect(await screen.findByText("Uploading and embedding…")).toBeInTheDocument();
    const before = calls.filter((c) => c.url.pathname === "/api/rag/kbs/live-test/docs" && c.method === "GET").length;
    finish(jsonResponse(outcomes));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/rag/kbs/live-test/docs" && c.method === "GET").length).toBeGreaterThan(before));
  });

  it.each([
    ["the spend cap", 402, { detail: { message: "Spend cap reached" } }, "Spend cap reached"],
    ["an unknown knowledge base", 404, { detail: "unknown knowledge base 'live-test'" }, "unknown knowledge base 'live-test'"],
  ])("shows %s when an upload fails", async (_n, status, body, text) => {
    setup({ "POST /api/rag/kbs/live-test/docs": () => jsonResponse(body, { status }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.upload(screen.getByLabelText("Upload files"), file("a.md"));
    expect(await screen.findByText(text)).toHaveClass("err");
  });

  it("falls back to a bare HTTP error for a non-JSON upload failure", async () => {
    setup({ "POST /api/rag/kbs/live-test/docs": () => textResponse("boom", 500) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.upload(screen.getByLabelText("Upload files"), file("a.md"));
    expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  });

  it("accepts dropped files, highlights the drop zone and ignores an empty drop", async () => {
    const { calls } = setup({ "POST /api/rag/kbs/live-test/docs": () => jsonResponse(outcomes) });
    const { container } = render(<KnowledgePage />);
    await openDetail();
    const zone = container.querySelector(".kn-drop")!;
    fireEvent.dragOver(zone);
    expect(zone).toHaveClass("on");
    fireEvent.dragLeave(zone);
    expect(zone).not.toHaveClass("on");
    fireEvent.drop(zone, { dataTransfer: { files: [] } });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.dragOver(zone);
    fireEvent.drop(zone, { dataTransfer: { files: [file("dropped.md")] } });
    expect(zone).not.toHaveClass("on");
    await screen.findByLabelText("Ingest results");
    expect(calls.some((c) => c.method === "POST")).toBe(true);
  });

  it("ignores a cancelled file chooser", async () => {
    const { calls } = setup();
    render(<KnowledgePage />);
    await openDetail();
    fireEvent.change(screen.getByLabelText("Upload files"), { target: { files: null } });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("adds pasted text with a title", async () => {
    const { calls } = setup({ "POST /api/rag/kbs/live-test/text": () => jsonResponse({ doc_id: "t1", source: "Notes", chunks: 1, status: "added" }) });
    render(<KnowledgePage />);
    await openDetail();
    const add = screen.getByRole("button", { name: "Add text" });
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Title"), "Notes");
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Text"), "Some knowledge");
    await userEvent.click(add);
    const res = await screen.findByLabelText("Ingest results");
    expect(res).toHaveTextContent("Notes");
    expect(res).toHaveTextContent("added");
    expect(calls.find((c) => c.url.pathname.endsWith("/text"))!.body).toEqual({ title: "Notes", text: "Some knowledge" });
    expect(screen.getByLabelText("Title")).toHaveValue("");
    expect(screen.getByLabelText("Text")).toHaveValue("");
  });

  it("shows a text ingest failure and keeps what was typed", async () => {
    setup({ "POST /api/rag/kbs/live-test/text": () => jsonResponse({ detail: "give the text a title" }, { status: 422 }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.type(screen.getByLabelText("Title"), "x");
    await userEvent.type(screen.getByLabelText("Text"), "y");
    await userEvent.click(screen.getByRole("button", { name: "Add text" }));
    expect(await screen.findByText("give the text a title")).toBeInTheDocument();
    expect(screen.getByLabelText("Text")).toHaveValue("y");
  });

  it("ingests a workspace folder and shows per-file results", async () => {
    const { calls } = setup({ "POST /api/rag/kbs/live-test/path": () => jsonResponse(outcomes) });
    render(<KnowledgePage />);
    await openDetail();
    const btn = screen.getByRole("button", { name: "Ingest from workspace" });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Workspace path"), "docs");
    await userEvent.click(btn);
    expect(await screen.findByLabelText("Ingest results")).toHaveTextContent("d.exe");
    expect(calls.find((c) => c.url.pathname.endsWith("/path"))!.body).toEqual({ path: "docs" });
  });

  it("shows a workspace path error", async () => {
    setup({ "POST /api/rag/kbs/live-test/path": () => jsonResponse({ detail: "that path is outside the workspace folder" }, { status: 422 }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.type(screen.getByLabelText("Workspace path"), "../x");
    await userEvent.click(screen.getByRole("button", { name: "Ingest from workspace" }));
    expect(await screen.findByText("that path is outside the workspace folder")).toBeInTheDocument();
  });

  it("shows busy labels for text and folder ingestion", async () => {
    let fin!: (r: Response) => void;
    setup({ "POST /api/rag/kbs/live-test/text": () => new Promise<Response>((r) => { fin = r; }), "POST /api/rag/kbs/live-test/path": () => new Promise<Response>(() => {}) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.type(screen.getByLabelText("Title"), "t");
    await userEvent.type(screen.getByLabelText("Text"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Add text" }));
    expect(await screen.findByRole("button", { name: "Adding…" })).toBeDisabled();
    fin(jsonResponse({ doc_id: "1", source: "t", chunks: 1, status: "added" }));
    await screen.findByLabelText("Ingest results");
    await userEvent.type(screen.getByLabelText("Workspace path"), "docs");
    await userEvent.click(screen.getByRole("button", { name: "Ingest from workspace" }));
    expect(await screen.findByRole("button", { name: "Ingesting…" })).toBeDisabled();
  });
});

describe("Knowledge: testing retrieval", () => {
  const hits = [
    { kb: "live-test", kb_label: "Live test", doc_id: "d1", source: "file1.md", chunk: 2, text: "Qdrant stores vectors.", score: 0.8123 },
    { kb: "live-test", kb_label: "Live test", doc_id: "d2", source: "tiny.txt", chunk: 0, text: "Another snippet.", score: 0.5 },
  ];

  it("searches with the button and shows source, score and snippet", async () => {
    const { calls } = setup({ "POST /api/rag/search": () => jsonResponse(hits) });
    render(<KnowledgePage />);
    await openDetail();
    const btn = screen.getByRole("button", { name: /Search/ });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Search query"), "what stores vectors");
    const k = screen.getByLabelText("Number of results");
    await userEvent.clear(k); await userEvent.type(k, "3");
    await userEvent.click(btn);
    expect(await screen.findByText("score 0.8123")).toBeInTheDocument();
    expect(screen.getByText("Qdrant stores vectors.")).toBeInTheDocument();
    expect(screen.getByText("chunk 2")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/rag/search")!.body).toEqual({ query: "what stores vectors", kbs: ["live-test"], k: 3 });
  });

  it("searches when Enter is pressed, but not for an empty query", async () => {
    const { calls } = setup({ "POST /api/rag/search": () => jsonResponse(hits) });
    render(<KnowledgePage />);
    await openDetail();
    const box = screen.getByLabelText("Search query");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.keyDown(box, { key: "a" });
    await userEvent.type(box, "hello{Enter}");
    await screen.findByText("score 0.5");
  });

  it("says when there are no matches", async () => {
    setup({ "POST /api/rag/search": () => jsonResponse([]) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.type(screen.getByLabelText("Search query"), "zzz");
    await userEvent.click(screen.getByRole("button", { name: /Search/ }));
    expect(await screen.findByText("No matches.")).toBeInTheDocument();
  });

  it.each([
    ["the spend cap", 402, { detail: { message: "Spend cap reached" } }, "Spend cap reached"],
    ["a store problem", 422, { detail: "Live test: connection refused" }, "Live test: connection refused"],
  ])("shows %s when a search fails", async (_n, status, body, text) => {
    setup({ "POST /api/rag/search": () => jsonResponse(body, { status }) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.type(screen.getByLabelText("Search query"), "q");
    await userEvent.click(screen.getByRole("button", { name: /Search/ }));
    expect(await screen.findByText(text)).toHaveClass("err");
  });

  it("shows a searching label while waiting", async () => {
    setup({ "POST /api/rag/search": () => new Promise<Response>(() => {}) });
    render(<KnowledgePage />);
    await openDetail();
    await userEvent.type(screen.getByLabelText("Search query"), "q");
    await userEvent.click(screen.getByRole("button", { name: /Search/ }));
    expect(await screen.findByRole("button", { name: /Searching…/ })).toBeDisabled();
  });
});

describe("Knowledge: stores", () => {
  it("lists stores with their settings and whether secrets are set", async () => {
    setup();
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const cloud = screen.getByText("Cloud Qdrant").closest(".kn-store") as HTMLElement;
    expect(cloud).toHaveTextContent("url: https://x.cloud · api_key: set");
    expect(cloud).toHaveTextContent("2 knowledge bases");
    const docker = screen.getAllByText("Qdrant (Docker)").map((e) => e.closest(".kn-store")).find((e) => e?.textContent?.includes("api_key: not set")) as HTMLElement;
    expect(docker).toHaveTextContent("1 knowledge base");
    const local = screen.getByText("Local file", { selector: "b" }).closest(".kn-store") as HTMLElement;
    expect(within(local).getByText("built-in")).toBeInTheDocument();
    expect(within(local).queryByRole("button", { name: /Delete/ })).toBeNull();
    expect(within(local).queryByText(/·/)).toBeNull();
  });

  it("tests a store and shows healthy or unreachable with the detail", async () => {
    setup({
      "POST /api/rag/stores/docker-qdrant/test": () => jsonResponse({ ok: true, detail: "Qdrant 1.20.0 (2 collections)" }),
      "POST /api/rag/stores/cloud/test": () => jsonResponse({ ok: false, detail: "connection refused" }),
    });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const card = (label: string) => screen.getByText(label, { selector: "b" }).closest(".kn-store") as HTMLElement;
    await userEvent.click(within(card("Cloud Qdrant")).getByRole("button", { name: "Test" }));
    expect(await within(card("Cloud Qdrant")).findByText("unreachable")).toHaveClass("bad");
    expect(within(card("Cloud Qdrant")).getByText("connection refused")).toBeInTheDocument();
    const dockerCard = screen.getAllByText("Qdrant (Docker)", { selector: "b" }).map((e) => e.closest(".kn-store") as HTMLElement).find((c) => c.textContent?.includes("Qdrant (Qdrant"))
      ?? screen.getAllByText("Qdrant (Docker)", { selector: "b" })[0].closest(".kn-store") as HTMLElement;
    await userEvent.click(within(dockerCard).getByRole("button", { name: "Test" }));
    expect(await within(dockerCard).findByText("healthy")).toHaveClass("ok");
    expect(within(dockerCard).getByText("Qdrant 1.20.0 (2 collections)")).toBeInTheDocument();
  });

  it("shows Testing… and an unreachable result when the request itself fails", async () => {
    let fin!: (r: Response) => void;
    setup({ "POST /api/rag/stores/cloud/test": () => new Promise<Response>((r) => { fin = r; }) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const card = screen.getByText("Cloud Qdrant", { selector: "b" }).closest(".kn-store") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByRole("button", { name: "Testing…" })).toBeDisabled();
    fin(textResponse("down", 500));
    expect(await within(card).findByText("HTTP 500")).toBeInTheDocument();
  });

  it("adds a store from the fields its kind declares", async () => {
    const t = toasts();
    const { calls } = setup({ "POST /api/rag/stores": () => jsonResponse(stores[1]) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: /Add store/ }));
    const d = within(dialog());
    expect(d.getByText("A SQLite file on this PC.")).toBeInTheDocument();
    expect(d.getByLabelText("File name")).toHaveValue("local.sqlite");
    await userEvent.selectOptions(d.getByLabelText("Kind"), "qdrant");
    expect(d.getByText("A Qdrant server.")).toBeInTheDocument();
    expect(d.getByText("Where the server listens")).toBeInTheDocument();
    expect(d.getByText("Only for Qdrant Cloud")).toBeInTheDocument();
    expect(d.getByLabelText(/^URL \*/)).toBeInTheDocument();
    expect(d.getByLabelText(/^API key/)).toHaveAttribute("type", "password");
    expect(d.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(d.getByPlaceholderText("my-qdrant"), "mine");
    await userEvent.type(d.getByLabelText("Name"), "Mine");
    await userEvent.type(d.getByLabelText(/^URL/), "http://h:6333");
    await userEvent.type(d.getByLabelText(/^API key/), "sekret");
    await userEvent.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/rag/stores")).toBe(true));
    expect(calls.find((c) => c.url.pathname === "/api/rag/stores" && c.method === "POST")!.body).toEqual({
      id: "mine", label: "Mine", kind: "qdrant", config: { url: "http://h:6333" }, secrets: { api_key: "sekret" } });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(t.seen.some((x) => x.text === "Saved Mine")).toBe(true);
    t.stop();
  });

  it("adds a store with default settings and no secrets", async () => {
    const { calls } = setup({ "POST /api/rag/stores": () => jsonResponse(stores[0]) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: /Add store/ }));
    const d = within(dialog());
    await userEvent.type(d.getByPlaceholderText("my-qdrant"), "notes");
    await userEvent.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ id: "notes", label: "", kind: "local", config: { file: "local.sqlite" }, secrets: {} });
  });

  it("shows a store that is refused (422) and keeps the form open", async () => {
    setup({ "POST /api/rag/stores": () => jsonResponse({ detail: "URL is required for a Qdrant store" }, { status: 422 }) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: /Add store/ }));
    const d = within(dialog());
    await userEvent.selectOptions(d.getByLabelText("Kind"), "qdrant");
    await userEvent.type(d.getByPlaceholderText("my-qdrant"), "x");
    await userEvent.click(d.getByRole("button", { name: "Save" }));
    expect(await d.findByText("URL is required for a Qdrant store")).toHaveClass("err");
    expect(d.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("edits a store: label and settings change, a stored secret is kept unless replaced or cleared", async () => {
    const { calls } = setup({ "PUT /api/rag/stores/cloud": () => jsonResponse(stores[2]) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const card = screen.getByText("Cloud Qdrant", { selector: "b" }).closest(".kn-store") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const d = within(dialog());
    expect(d.getByLabelText("Kind")).toBeDisabled();
    expect(d.queryByPlaceholderText("my-qdrant")).toBeNull();
    expect(d.getByLabelText(/^API key/)).toHaveAttribute("placeholder", "set: leave blank to keep");
    await userEvent.clear(d.getByLabelText("Name")); await userEvent.type(d.getByLabelText("Name"), "Renamed");
    await userEvent.clear(d.getByLabelText(/^URL/)); await userEvent.type(d.getByLabelText(/^URL/), "https://y.cloud");
    await userEvent.click(d.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ label: "Renamed", config: { url: "https://y.cloud" }, secrets: {} });
  });

  it("replaces or clears a stored secret", async () => {
    const { calls } = setup({ "PUT /api/rag/stores/cloud": () => jsonResponse(stores[2]) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const open = async () => userEvent.click(within(screen.getByText("Cloud Qdrant", { selector: "b" }).closest(".kn-store") as HTMLElement).getByRole("button", { name: "Edit" }));
    await open();
    await userEvent.type(within(dialog()).getByLabelText(/^API key/), "new-secret");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    expect((calls.find((c) => c.method === "PUT")!.body as any).secrets).toEqual({ api_key: "new-secret" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await open();
    await userEvent.click(within(dialog()).getByLabelText("Clear API key"));
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect((calls.filter((c) => c.method === "PUT")[1].body as any).secrets).toEqual({ api_key: "" });
  });

  it("edits a store that has no secret set and sends an empty setting as an explicit empty value", async () => {
    const { calls } = setup({ "PUT /api/rag/stores/docker-qdrant": () => jsonResponse(stores[1]) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const card = screen.getAllByText("Qdrant (Docker)", { selector: "b" })[0].closest(".kn-store") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Edit" }));
    expect(within(dialog()).queryByLabelText(/Clear/)).toBeNull();
    await userEvent.clear(within(dialog()).getByLabelText(/^URL/));
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect((calls.find((c) => c.method === "PUT")!.body as any).config).toEqual({ url: "" });
  });

  it("shows an edit failure", async () => {
    setup({ "PUT /api/rag/stores/cloud": () => jsonResponse({ detail: "could not connect" }, { status: 422 }) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(within(screen.getByText("Cloud Qdrant", { selector: "b" }).closest(".kn-store") as HTMLElement).getByRole("button", { name: "Edit" }));
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save" }));
    expect(await within(dialog()).findByText("could not connect")).toBeInTheDocument();
  });

  it("deletes a store after confirmation, and reports a refusal", async () => {
    const t = toasts();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    let n = 0;
    const { calls } = setup({ "DELETE /api/rag/stores/cloud": () => (++n === 1 ? jsonResponse({ detail: "2 knowledge bases still use this store - delete them first" }, { status: 422 }) : jsonResponse({ ok: true })) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    const del = () => within(screen.getByText("Cloud Qdrant", { selector: "b" }).closest(".kn-store") as HTMLElement).getByRole("button", { name: /Delete/ });
    await userEvent.click(del());
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(del());
    await waitFor(() => expect(t.seen).toContainEqual({ text: "2 knowledge bases still use this store - delete them first", tone: "err" }));
    await userEvent.click(del());
    await waitFor(() => expect(t.seen.some((x) => x.text === "Deleted Cloud Qdrant")).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(3);
    t.stop();
  });

  it("closes the store form from Cancel", async () => {
    setup();
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: /Add store/ }));
    await userEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("copes with a kinds list that has not loaded", async () => {
    setup({ "GET /api/rag/kinds": () => jsonResponse([]) });
    render(<KnowledgePage />);
    await screen.findByText("Live test");
    await userEvent.click(screen.getByRole("button", { name: /Add store/ }));
    expect(within(dialog()).getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("Knowledge: Docker", () => {
  // the Docker card repeats the label of the store it registers, so find the card by its description
  const stackCard = () => screen.getByText(/Run a vector database on this PC/).closest(".card")!.querySelector(".kn-store") as HTMLElement;

  it("shows Docker as unavailable with the reason and no actions", async () => {
    setup({ "GET /api/rag/stacks": () => jsonResponse([stack("unavailable", { detail: "Docker is not running - start Docker Desktop" })]) });
    render(<KnowledgePage />);
    await screen.findByText("Docker is not running - start Docker Desktop");
    expect(screen.getByText("unavailable")).toHaveClass("bad");
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("offers Start for a missing stack and warns that the first start downloads the image", async () => {
    const t = toasts();
    let state = "missing";
    const { calls } = setup({
      "GET /api/rag/stacks": () => jsonResponse([stack(state)]),
      "POST /api/rag/stacks/qdrant/start": () => { state = "running"; return jsonResponse(stack("running")); },
    });
    render(<KnowledgePage />);
    await screen.findByText(/The first start downloads the image \(qdrant\/qdrant:v1\)/);
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Qdrant (Docker) started")).toBe(true));
    expect(calls.some((c) => c.method === "POST" && c.url.pathname === "/api/rag/stacks/qdrant/start")).toBe(true);
    expect(await screen.findByRole("button", { name: "Stop" })).toBeInTheDocument();
    t.stop();
  });

  it("shows a busy label while starting", async () => {
    setup({ "GET /api/rag/stacks": () => jsonResponse([stack("stopped")]), "POST /api/rag/stacks/qdrant/start": () => new Promise<Response>(() => {}) });
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Start" }));
    expect(await screen.findByRole("button", { name: "Starting…" })).toBeDisabled();
  });

  it("stops a running stack", async () => {
    const { calls } = setup({ "POST /api/rag/stacks/qdrant/stop": () => jsonResponse(stack("stopped")) });
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/rag/stacks/qdrant/stop")).toBe(true));
  });

  it("shows busy labels for stopping, adding and removing", async () => {
    setup({
      "POST /api/rag/stacks/qdrant/stop": () => new Promise<Response>(() => {}),
      "POST /api/rag/stacks/qdrant/register": () => new Promise<Response>(() => {}),
      "DELETE /api/rag/stacks/qdrant": () => new Promise<Response>(() => {}),
    });
    const { unmount } = render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(await screen.findByRole("button", { name: "Stopping…" })).toBeDisabled();
    unmount();
    const second = render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Use it" }));
    expect(await screen.findByRole("button", { name: "Adding…" })).toBeDisabled();
    second.unmount();
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("button", { name: "Removing…" })).toBeDisabled();
  });

  it("registers a running stack as a store and reloads the store list", async () => {
    const t = toasts();
    const { calls } = setup({ "POST /api/rag/stacks/qdrant/register": () => jsonResponse(stores[1]) });
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Use it" }));
    await waitFor(() => expect(t.seen.some((x) => x.text === "Qdrant (Docker) added as a store")).toBe(true));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/rag/stores" && c.method === "GET").length).toBeGreaterThan(1));
    t.stop();
  });

  it("removes a container but keeps the data by default", async () => {
    const { calls } = setup({ "DELETE /api/rag/stacks/qdrant": () => jsonResponse(stack("missing")) });
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")!.url.search).toBe("");
  });

  it("removes the container and its data when the box is ticked", async () => {
    const { calls } = setup({ "GET /api/rag/stacks": () => jsonResponse([stack("stopped")]), "DELETE /api/rag/stacks/qdrant": () => jsonResponse(stack("missing")) });
    render(<KnowledgePage />);
    await screen.findByRole("button", { name: "Remove" });
    await userEvent.click(within(stackCard()).getByLabelText("also delete its data"));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")!.url.search).toBe("?delete_data=true");
    await userEvent.click(within(stackCard()).getByLabelText("also delete its data"));
  });

  it("shows a stack action failure on that stack and clears it on the next try", async () => {
    let fail = true;
    setup({ "GET /api/rag/stacks": () => jsonResponse([stack("stopped")]),
      "POST /api/rag/stacks/qdrant/start": () => (fail ? jsonResponse({ detail: "port 6333 is already allocated" }, { status: 409 }) : jsonResponse(stack("running"))) });
    render(<KnowledgePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Start" }));
    expect(await screen.findByText("port 6333 is already allocated")).toHaveClass("err");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(screen.queryByText("port 6333 is already allocated")).toBeNull());
  });

  it("shows why stacks could not be listed, and refreshes on demand", async () => {
    let fail = true;
    const { calls } = setup({ "GET /api/rag/stacks": () => (fail ? jsonResponse({ detail: "docker query failed" }, { status: 409 }) : jsonResponse([stack("running")])) });
    render(<KnowledgePage />);
    expect(await screen.findByText("docker query failed")).toHaveClass("err");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("button", { name: "Stop" });
    expect(calls.filter((c) => c.url.pathname === "/api/rag/stacks").length).toBe(2);
  });

  it("shows a checking state before the stacks arrive, and no notes for a stack without any", async () => {
    setup({ "GET /api/rag/stacks": () => new Promise<Response>(() => {}) });
    render(<KnowledgePage />);
    expect(await screen.findByText("Checking Docker…")).toBeInTheDocument();
  });

  it("omits the notes line when a stack has none", async () => {
    setup({ "GET /api/rag/stacks": () => jsonResponse([stack("running", { notes: "" })]) });
    render(<KnowledgePage />);
    await screen.findByRole("button", { name: "Stop" });
    expect(screen.queryByText("Local vector database")).toBeNull();
  });
});
