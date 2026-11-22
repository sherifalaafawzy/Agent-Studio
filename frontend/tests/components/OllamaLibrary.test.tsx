import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import OllamaLibrary, { compactNumber } from "@/components/OllamaLibrary";
import { forgetModels } from "@/components/ModelSelect";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const fit = (level: string, text = `fit ${level}`) => ({ level, text });
const LIB = {
  ram_gb: 16, vram_gb: null,
  models: [
    { name: "qwen3", label: "Qwen 3", best_for: "Best all-round pick for agents", caps: ["tools", "thinking"], source: "curated", new: false, downloads: null, updated: null, installed: true,
      tags: [{ tag: "4b", size_gb: 2.5, fit: fit("ok"), installed: false }, { tag: "8b", size_gb: 5.2, fit: fit("ok"), installed: true }] },
    { name: "llava", label: "LLaVA", best_for: "Light picture understanding", caps: ["vision"], source: "curated", new: false, downloads: null, updated: null, installed: false,
      tags: [{ tag: "7b", size_gb: 4.7, fit: fit("too_big"), installed: false }] },
    { name: "nomic-embed-text", label: "Nomic Embed Text", best_for: "Search helper", caps: ["embedding"], source: "curated", new: false, downloads: null, updated: null, installed: false,
      tags: [{ tag: "latest", size_gb: 0.27, fit: fit("great"), installed: false }] },
    { name: "freshmodel", label: "freshmodel", best_for: "", caps: ["tools"], source: "ollama.com", new: true, downloads: 1234567, updated: "November 8, 2026", installed: false,
      tags: [{ tag: "27b", size_gb: null, fit: fit("unknown"), installed: false }] },
    { name: "tight", label: "Tight model", best_for: "Fits barely", caps: [], source: "curated", new: false, downloads: 5400, updated: null, installed: false, tags: [{ tag: "x", size_gb: 9, fit: fit("tight"), installed: false }] },
  ],
};
const HAVE = { ok: true, message: "", models: [{ name: "qwen3:8b", size: 5225388164, parameters: "8.2B", quantization: "Q4_K_M" }, { name: "bare", size: 1_000_000_000, parameters: null, quantization: null }] };

function setup(over: Record<string, any> = {}, props: Record<string, unknown> = {}) {
  const have = over.have ?? HAVE;
  const r = routeFetch({
    "GET /api/easy/ollama/library": () => jsonResponse(over.lib ?? LIB),
    "GET /api/easy/ollama/models": () => jsonResponse(have),
    ...(over.routes ?? {}),
  });
  const onInstalledChange = vi.fn();
  render(<OllamaLibrary onInstalledChange={onInstalledChange} {...props} />);
  return { ...r, onInstalledChange };
}
const card = (name: string) => screen.getByText(name, { selector: "b" }).closest(".olib-card") as HTMLElement;

describe("compactNumber", () => {
  it("shortens big counts", () => { expect([compactNumber(999), compactNumber(5400), compactNumber(1234567)]).toEqual(["999", "5K", "1.2M"]); });
});

describe("OllamaLibrary: browsing", () => {
  it("shows what is on this PC, the PC's size, and every model with a plain verdict on whether it fits", async () => {
    const { onInstalledChange } = setup();
    expect(await screen.findByText("qwen3:8b")).toBeInTheDocument();
    expect(screen.getByText(/5.2 GB · 8.2B/)).toBeInTheDocument();
    expect(onInstalledChange).toHaveBeenCalledWith(["qwen3:8b", "bare"]);
    expect(screen.getByText(/16 GB memory, no graphics card found/)).toBeInTheDocument();
    expect(within(card("Qwen 3")).getByText("installed", { selector: ".pill" })).toBeInTheDocument();
    expect(within(card("Qwen 3")).getAllByText("Slower (uses processor)")).toHaveLength(2);
    expect(within(card("LLaVA")).getByText("Too big")).toHaveAttribute("title", "fit too_big");
    expect(within(card("Nomic Embed Text")).getByText("Fast on your PC")).toBeInTheDocument();
    expect(within(card("Tight model")).getByText("Tight fit")).toBeInTheDocument();
    expect(within(card("freshmodel")).getByText("new")).toBeInTheDocument();
    expect(within(card("freshmodel")).getByText("1.2M downloads")).toBeInTheDocument();
    expect(within(card("freshmodel")).getByText(/No description\. \(from ollama\.com\)/)).toBeInTheDocument();
    expect(within(card("Qwen 3")).getByText("Installed ✓")).toBeInTheDocument();
    expect(screen.getByText("Size unknown")).toBeInTheDocument();
  });

  it("says when graphics memory exists", async () => {
    setup({ lib: { ...LIB, vram_gb: 12, ram_gb: null } });
    expect(await screen.findByText(/memory unknown, 12 GB graphics memory/)).toBeInTheDocument();
  });

  it("searches by name, description or badge, and says when nothing matches", async () => {
    setup();
    await screen.findByText("Qwen 3");
    await userEvent.type(screen.getByLabelText("Search models"), "picture");
    expect(screen.queryByText("Qwen 3")).toBeNull();
    expect(screen.getByText("LLaVA")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Search models"));
    await userEvent.type(screen.getByLabelText("Search models"), "zzzz");
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument();
  });

  it("filters by what a model can do, what fits, and what is installed", async () => {
    setup();
    await screen.findByText("Qwen 3");
    const names = () => [...document.querySelectorAll(".olib-card > .row b")].map((b) => b.textContent);
    await userEvent.click(screen.getByRole("button", { name: "Reads pictures" }));
    expect(names()).toEqual(["LLaVA"]);
    await userEvent.click(screen.getByRole("button", { name: "Can use tools" }));
    expect(names()).toEqual(["Qwen 3", "freshmodel"]);
    await userEvent.click(screen.getByRole("button", { name: "Thinks step by step" }));
    expect(names()).toEqual(["Qwen 3"]);
    await userEvent.click(screen.getByRole("button", { name: "Search helpers" }));
    expect(names()).toEqual(["Nomic Embed Text"]);
    await userEvent.click(screen.getByRole("button", { name: "Fits my PC" }));
    expect(names()).toEqual(["Qwen 3", "Nomic Embed Text"]);
    await userEvent.click(screen.getByRole("button", { name: "Installed" }));
    expect(names()).toEqual(["Qwen 3"]);
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(names()).toHaveLength(5);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains an empty PC and an unreachable Ollama", async () => {
    setup({ have: { ok: true, message: "", models: [] } });
    expect(await screen.findByText("No models yet. Pick one below.")).toBeInTheDocument();
  });

  it("shows why the installed list is unavailable", async () => {
    setup({ have: { ok: false, message: "Ollama isn't reachable (ConnectError). Start it first.", models: [] } });
    expect(await screen.findByText(/Ollama isn't reachable/)).toBeInTheDocument();
    expect(screen.queryByText("No models yet. Pick one below.")).toBeNull();
  });

  it("shows a load error, and hides the Use buttons when no provider is ready", async () => {
    routeFetch({ "GET /api/easy/ollama/library": () => jsonResponse({ detail: "boom" }, { status: 500 }), "GET /api/easy/ollama/models": () => jsonResponse(HAVE) });
    render(<OllamaLibrary />);
    expect(await screen.findByText("⚠ boom")).toBeInTheDocument();
    setup({}, { providerReady: false });
    await screen.findAllByText("qwen3:8b");
    expect(screen.queryByRole("button", { name: "Use" })).toBeNull();
  });
});

describe("OllamaLibrary: downloading and managing models", () => {
  const events = (...e: object[]) => sseResponse(e.map((x) => `data: ${JSON.stringify(x)}\n\n`));

  it("downloads a model with one overall percentage, then switches it on", async () => {
    forgetModels("ollama");
    const { calls } = setup({ routes: {
      "POST /api/easy/ollama/pull": () => events({ type: "progress", status: "pulling abc", percent: 42.5, completed: 5, total: 10 }, { type: "done", model: "qwen3:4b" }),
      "POST /api/easy/ollama/use": () => jsonResponse({ ok: true }),
    } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Download qwen3:4b" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname === "/api/easy/ollama/use")?.body).toEqual({ model: "qwen3:4b" }));
    expect(calls.find((c) => c.url.pathname === "/api/easy/ollama/pull")!.body).toEqual({ model: "qwen3:4b" });
    expect(await screen.findByText("qwen3:4b is now available in the model lists.")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.pathname === "/api/easy/ollama/library").length).toBeGreaterThanOrEqual(2);       // the list reloads afterwards
  });

  it("shows progress while it downloads, and the other download buttons wait", async () => {
    let finish: () => void = () => {};
    setup({ routes: {
      "POST /api/easy/ollama/pull": () => new Promise<Response>((resolve) => { finish = () => resolve(events({ type: "error", message: "file does not exist" })); }),
    } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Download qwen3:4b" }));
    expect(await screen.findByText(/Downloading/)).toHaveTextContent("Downloading qwen3:4b: starting");
    expect(screen.getByRole("button", { name: "Download llava:7b" })).toBeDisabled();
    finish();
    expect(await screen.findByText("⚠ file does not exist")).toBeInTheDocument();
    expect(screen.queryByText(/Downloading/)).toBeNull();
  });

  it("draws a progress bar from the percentage", async () => {
    let finish: () => void = () => {};
    setup({ routes: {
      "POST /api/easy/ollama/pull": () => new Promise<Response>((resolve) => { finish = () => resolve(events({ type: "progress", status: "pulling x", percent: 60 }, { type: "error", message: "stopped" })); }),
    } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Download llava:7b" }));
    finish();
    await screen.findByText("⚠ stopped");
  });

  it("reports a network failure while downloading", async () => {
    setup({ routes: { "POST /api/easy/ollama/pull": () => { throw new Error("network down"); } } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Download qwen3:4b" }));
    expect(await screen.findByText("⚠ network down")).toBeInTheDocument();
  });

  it("'Stop' really aborts the request, and says the download resumes where it left off", async () => {
    let signal: AbortSignal | undefined;
    setup({ routes: { "POST /api/easy/ollama/pull": (init: RequestInit | undefined) => new Promise<Response>((_res, rej) => {
      signal = init?.signal ?? undefined;
      signal?.addEventListener("abort", () => rej(Object.assign(new Error("aborted"), { name: "AbortError" })));
    }) } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Download llava:7b" }));
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(signal?.aborted).toBe(true);
    expect(await screen.findByText(/Download stopped\. Start it again to continue where it left off\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("uses an installed model", async () => {
    const { calls } = setup({ routes: { "POST /api/easy/ollama/use": () => jsonResponse({ ok: true }) } });
    const row = (await screen.findByText("qwen3:8b")).closest(".olib-row") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Use" }));
    expect(await screen.findByText("qwen3:8b is now available in the model lists.")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/easy/ollama/use")!.body).toEqual({ model: "qwen3:8b" });
  });

  it("explains when a model cannot be switched on", async () => {
    setup({ routes: { "POST /api/easy/ollama/use": () => jsonResponse({ detail: "there is no Ollama provider" }, { status: 404 }) } });
    const row = (await screen.findByText("qwen3:8b")).closest(".olib-row") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Use" }));
    expect(await screen.findByText("⚠ there is no Ollama provider")).toBeInTheDocument();
  });

  it("removes a model only after you confirm", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { calls } = setup({ routes: { "DELETE /api/easy/ollama/models": () => jsonResponse({ ok: true }) } });
    await screen.findByText("qwen3:8b");
    await userEvent.click(screen.getByRole("button", { name: "Remove qwen3:8b" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Remove qwen3:8b" }));
    await waitFor(() => expect(calls.find((c) => c.method === "DELETE")!.url.search).toBe("?name=qwen3%3A8b"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("You can download it again later"));
  });

  it("explains a failed removal", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    setup({ routes: { "DELETE /api/easy/ollama/models": () => jsonResponse({ detail: "model 'bare' not found" }, { status: 422 }) } });
    await userEvent.click(await screen.findByRole("button", { name: "Remove bare" }));
    expect(await screen.findByText("⚠ model 'bare' not found")).toBeInTheDocument();
  });

  it("finds new models on ollama.com and says what it found", async () => {
    const { calls } = setup({ routes: { "POST /api/easy/ollama/library/refresh": () => jsonResponse({ ok: true, message: "Found 20 models on ollama.com." }) } });
    await screen.findByText("Qwen 3");
    await userEvent.type(screen.getByLabelText("Search models"), "  coder ");
    await userEvent.click(screen.getByRole("button", { name: "Find new models" }));
    expect(await screen.findByText("Found 20 models on ollama.com.")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname.endsWith("/refresh"))!.body).toEqual({ query: "coder", force: true });
    expect(screen.getByRole("button", { name: "Find new models" })).toBeEnabled();
  });

  it("keeps working when the website cannot be read", async () => {
    setup({ routes: { "POST /api/easy/ollama/library/refresh": () => jsonResponse({ detail: "offline" }, { status: 500 }) } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Find new models" }));
    expect(await screen.findByText("⚠ offline")).toBeInTheDocument();
    expect(screen.getByText("Qwen 3")).toBeInTheDocument();
  });

  it("loads exact download sizes for a discovered model on request, and reports a failure", async () => {
    let ok = true;
    const { calls } = setup({ routes: { "GET /api/easy/ollama/library/tags": () => (ok ? jsonResponse({ ok: true, tags: [], message: "" }) : jsonResponse({ ok: false, tags: [], message: "Could not read the size list (ConnectError)." })) } });
    await screen.findByText("freshmodel");
    await userEvent.click(screen.getByRole("button", { name: "Show download sizes" }));
    await waitFor(() => expect(calls.some((c) => c.url.pathname === "/api/easy/ollama/library/tags" && c.url.search === "?model=freshmodel")).toBe(true));
    ok = false;
    await userEvent.click(screen.getByRole("button", { name: "Show download sizes" }));
    expect(await screen.findByText("Could not read the size list (ConnectError).")).toBeInTheDocument();
  });

  it("a thrown request while loading sizes is shown too", async () => {
    setup({ routes: { "GET /api/easy/ollama/library/tags": () => jsonResponse({ detail: "tag page broke" }, { status: 500 }) } });
    await screen.findByText("freshmodel");
    await userEvent.click(screen.getByRole("button", { name: "Show download sizes" }));
    expect(await screen.findByText("tag page broke")).toBeInTheDocument();
  });

  it("shows the percentage and a bar while a download is running, and ignores events it does not know", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const enc = new TextEncoder();
    const send = (e: object) => controller.enqueue(enc.encode(`data: ${JSON.stringify(e)}

`));
    setup({ routes: { "POST /api/easy/ollama/pull": () => new Response(new ReadableStream<Uint8Array>({ start(c) { controller = c; } }), { headers: { "Content-Type": "text/event-stream" } }),
      "POST /api/easy/ollama/use": () => jsonResponse({ ok: true }) } });
    await screen.findByText("Qwen 3");
    await userEvent.click(screen.getByRole("button", { name: "Download qwen3:4b" }));
    await waitFor(() => expect(controller).toBeDefined());
    send({ type: "progress", status: "pulling abc", percent: 42.5 });
    expect(await screen.findByText(/pulling abc · 42.5%/)).toBeInTheDocument();
    expect(document.querySelector(".olib-prog .bar i")).toHaveStyle({ width: "42.5%" });
    send({ type: "noise" });
    send({ type: "done", model: "qwen3:4b" });
    controller.close();
    expect(await screen.findByText("qwen3:4b is now available in the model lists.")).toBeInTheDocument();
  });
});
