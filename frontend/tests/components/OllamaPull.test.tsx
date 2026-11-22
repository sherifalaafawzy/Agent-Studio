import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import OllamaPull from "@/components/OllamaPull";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const suggestions = [
  { id: "qwen2.5-coder:7b", note: "Strong at code" },
  { id: "llama3.1", note: "General purpose" },
];
const installedModels = (ids: string[]) => ({
  provider: "ollama", source: "live", error: null, cached: false,
  models: ids.map((id) => ({ id, label: id, input_per_m: null, output_per_m: null })),
});

function streamOf(frames: object[], { close = true } = {}): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const f of frames) c.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
      if (close) c.close();
    },
  });
  return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
}

function setup(installed: string[] = [], extra: Parameters<typeof routeFetch>[0] = {}) {
  const onPulled = vi.fn();
  const routes = {
    "GET /api/providers/oll/ollama/suggestions": () => jsonResponse(suggestions),
    "GET /api/providers/oll/models": () => jsonResponse(installedModels(installed)),
    ...extra,
  };
  const r = routeFetch(routes);
  render(<OllamaPull pid="oll" onPulled={onPulled} />);
  return { ...r, onPulled };
}

describe("OllamaPull: installed models", () => {
  it("shows what is installed on this PC", async () => {
    setup(["llama3.1", "phi3"]);
    expect(await screen.findByText("llama3.1, phi3")).toBeInTheDocument();
  });

  it("says when nothing is installed yet", async () => {
    setup([]);
    expect(await screen.findByText("none yet")).toBeInTheDocument();
  });

  it("shows a checking state while the list loads", () => {
    setup([], { "GET /api/providers/oll/models": () => new Promise<Response>(() => {}) });
    expect(screen.getByText("checking…")).toBeInTheDocument();
  });

  it("shows why the installed list could not be read", async () => {
    setup([], { "GET /api/providers/oll/models": () => textResponse("down", 503) });
    expect(await screen.findByText("HTTP 503")).toBeInTheDocument();
  });

  it("marks suggestions that are already installed and disables them", async () => {
    setup(["llama3.1"]);
    const installed = await screen.findByRole("button", { name: "llama3.1 ✓" });
    expect(installed).toBeDisabled();
    expect(screen.getByRole("button", { name: "qwen2.5-coder:7b" })).toBeEnabled();
  });

  it("ignores a failed suggestions request", async () => {
    setup([], { "GET /api/providers/oll/ollama/suggestions": () => textResponse("x", 500) });
    expect(await screen.findByText("none yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "llama3.1" })).toBeNull();
  });
});

describe("OllamaPull: downloading", () => {
  it("keeps the download button disabled until a name is typed", async () => {
    setup();
    const btn = screen.getByRole("button", { name: "Download a model" });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "   ");
    expect(btn).toBeDisabled();
    await userEvent.clear(screen.getByPlaceholderText(/qwen2.5-coder/));
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "phi3");
    expect(btn).toBeEnabled();
  });

  it("pulls the typed model, shows progress, then reports success", async () => {
    const { calls, onPulled } = setup([], {
      "POST /api/providers/oll/pull": () => streamOf([
        { type: "progress", status: "pulling manifest" },
        { type: "progress", status: "downloading", completed: 500_000_000, total: 1_000_000_000 },
        { type: "done", model: "phi3" },
      ]),
    });
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "  phi3 ");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("Installed phi3. Pick it in the model lists above.")).toBeInTheDocument();
    expect(onPulled).toHaveBeenCalledTimes(1);
    const pull = calls.find((c) => c.method === "POST")!;
    expect(pull.body).toEqual({ model: "phi3" });
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows live progress in gigabytes while a model downloads", async () => {
    setup([], {
      "POST /api/providers/oll/pull": () => streamOf([
        { type: "progress", status: "downloading", completed: 250_000_000, total: 1_000_000_000 },
      ], { close: false }),
    });
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "phi3");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("downloading · 25% (0.25 GB of 1.00 GB)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Downloading…" })).toBeDisabled();
  });

  it("shows a status without a percentage when the total is unknown", async () => {
    setup([], {
      "POST /api/providers/oll/pull": () => streamOf([{ type: "progress", status: "verifying sha256 digest" }], { close: false }),
    });
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "phi3");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("verifying sha256 digest")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("reports an error frame from the server", async () => {
    const { onPulled } = setup([], {
      "POST /api/providers/oll/pull": () => streamOf([{ type: "error", message: "no space left on device" }]),
    });
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "phi3");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("⚠ no space left on device")).toBeInTheDocument();
    expect(onPulled).not.toHaveBeenCalled();
  });

  it("reports a request that fails before streaming starts", async () => {
    setup([], { "POST /api/providers/oll/pull": () => jsonResponse({ detail: "Ollama is not running" }, { status: 503 }) });
    await userEvent.type(screen.getByPlaceholderText(/qwen2.5-coder/), "phi3");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("⚠ Ollama is not running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download a model" })).toBeEnabled();
  });

  it("pulls a suggested model with one click", async () => {
    const { calls } = setup([], {
      "POST /api/providers/oll/pull": () => streamOf([{ type: "done", model: "llama3.1" }]),
    });
    await userEvent.click(await screen.findByRole("button", { name: "llama3.1" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ model: "llama3.1" });
    expect(await screen.findByText("Installed llama3.1. Pick it in the model lists above.")).toBeInTheDocument();
  });

  it("disables the suggestions while a download is running", async () => {
    setup([], {
      "POST /api/providers/oll/pull": () => streamOf([{ type: "progress", status: "starting" }], { close: false }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "llama3.1" }));
    await screen.findByText("starting");
    await act(async () => {});
    expect(screen.getByRole("button", { name: "llama3.1" })).toBeDisabled();
    expect(screen.getByRole("textbox")).toBeDisabled();
  });
});
