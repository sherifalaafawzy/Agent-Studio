import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import OllamaPull from "@/components/OllamaPull";
import { jsonResponse, routeFetch } from "../helpers/http";

function stream(frames: object[], close: boolean): Response {
  const enc = new TextEncoder();
  return new Response(new ReadableStream<Uint8Array>({
    start(c) {
      for (const f of frames) c.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
      if (close) c.close();
    },
  }), { headers: { "Content-Type": "text/event-stream" } });
}

function setup(pull: () => Response) {
  routeFetch({
    "GET /api/providers/ob/ollama/suggestions": () => jsonResponse([]),
    "GET /api/providers/ob/models": () => jsonResponse({ provider: "ollama", source: "live", error: null, cached: false, models: [] }),
    "POST /api/providers/ob/pull": pull,
  });
  render(<OllamaPull pid="ob" onPulled={() => {}} />);
}

describe("OllamaPull stream edge cases", () => {
  it("ignores stream events of an unknown type and ends cleanly", async () => {
    setup(() => stream([{ type: "mystery", message: "??" }], true));
    await screen.findByText("none yet");
    await userEvent.type(screen.getByPlaceholderText(/model name/), "m1");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Download a model" })).toBeEnabled());
    expect(screen.queryByText(/Installed/)).toBeNull();
    expect(screen.queryByText(/⚠/)).toBeNull();
  });

  it("shows a percentage of 0 and no sizes when progress has a total but no completed count", async () => {
    setup(() => stream([{ type: "progress", status: "pulling", total: 2e9 }], false));
    await screen.findByText("none yet");
    await userEvent.type(screen.getByPlaceholderText(/model name/), "m1");
    await userEvent.click(screen.getByRole("button", { name: "Download a model" }));
    expect(await screen.findByText("pulling · 0% ( of 2.00 GB)")).toBeInTheDocument();
  });
});
