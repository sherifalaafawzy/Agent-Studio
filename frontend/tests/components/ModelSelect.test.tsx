import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ModelSelect, { forgetModels, loadModels, type ModelInfo } from "@/components/ModelSelect";
import { jsonResponse, routeFetch, textResponse } from "../helpers/http";

const models: ModelInfo[] = [
  { id: "gpt-4o", label: "GPT-4o", input_per_m: 2.5, output_per_m: 10 },
  { id: "tiny", label: "tiny", input_per_m: 0, output_per_m: 0 },
  { id: "mini", label: "mini", input_per_m: 0.5, output_per_m: 0.123456 },
  { id: "unpriced", label: "Unpriced", input_per_m: null, output_per_m: null },
];
const resp = (over: Record<string, unknown> = {}) => ({ provider: "p", source: "live", error: null, models, cached: false, ...over });

describe("loadModels cache", () => {
  it("fetches once per provider and reuses the result", async () => {
    const { calls } = routeFetch({ "GET /api/providers/cache-a/models": () => jsonResponse(resp()) });
    const first = await loadModels("cache-a");
    const second = await loadModels("cache-a");
    expect(second).toBe(first);
    expect(calls).toHaveLength(1);
  });

  it("bypasses the cache when asked to refresh", async () => {
    const { calls } = routeFetch({ "GET /api/providers/cache-b/models": () => jsonResponse(resp()) });
    await loadModels("cache-b");
    await loadModels("cache-b", true);
    expect(calls.map((c) => c.url.search)).toEqual(["", "?refresh=true"]);
  });

  it("forgets a provider so the next load goes to the network", async () => {
    const { calls } = routeFetch({ "GET /api/providers/cache-c/models": () => jsonResponse(resp()) });
    await loadModels("cache-c");
    forgetModels("cache-c");
    await loadModels("cache-c");
    expect(calls).toHaveLength(2);
  });

  it("drops a failed lookup from the cache and reports the error", async () => {
    routeFetch({ "GET /api/providers/cache-d/models": () => jsonResponse({ detail: "no key" }, { status: 400 }) });
    await expect(loadModels("cache-d")).rejects.toThrow("no key");
    const { calls } = routeFetch({ "GET /api/providers/cache-d/models": () => jsonResponse(resp()) });
    await expect(loadModels("cache-d")).resolves.toMatchObject({ source: "live" });
    expect(calls).toHaveLength(1);
  });
});

describe("ModelSelect", () => {
  // loadModels caches per provider for the module lifetime, so each setup gets its own provider id
  let seq = 0;
  function setup(props: Partial<React.ComponentProps<typeof ModelSelect>> = {}, body = resp()) {
    const onChange = vi.fn();
    const pid = `sel-${++seq}`;
    const { calls } = routeFetch({ [`GET /api/providers/${pid}/models`]: () => jsonResponse(body) });
    const utils = render(<ModelSelect provider={pid} value="" onChange={onChange} {...props} />);
    return { ...utils, onChange, calls };
  }

  it("shows a loading placeholder until the list arrives", async () => {
    routeFetch({ "GET /api/providers/sel-loading-only/models": () => new Promise<Response>(() => {}) });
    render(<ModelSelect provider="sel-loading-only" value="" onChange={() => {}} />);
    expect(screen.getByRole("combobox")).toBeDisabled();
    expect(screen.getByRole("option", { name: "Loading models…" })).toBeInTheDocument();
  });

  it("lists models with their price, and labels by id when there is no separate name", async () => {
    setup();
    expect(await screen.findByRole("option", { name: "GPT-4o (gpt-4o) · $2.5/$10 per M" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "tiny · free" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "mini · $0.5/$0.123 per M" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Unpriced (unpriced)" })).toBeInTheDocument();
  });

  it("drops the prices in compact mode", async () => {
    setup({ compact: true });
    expect(await screen.findByRole("option", { name: "GPT-4o (gpt-4o)" })).toBeInTheDocument();
  });

  it("uses the empty label for the blank choice", async () => {
    setup({ emptyLabel: "Use the chat's model" });
    expect(await screen.findByRole("option", { name: "Use the chat's model" })).toBeInTheDocument();
  });

  it("falls back to Default for the blank choice", async () => {
    setup();
    expect(await screen.findByRole("option", { name: "Default" })).toBeInTheDocument();
  });

  it("reports a picked model", async () => {
    const { onChange } = setup();
    await screen.findByRole("option", { name: "tiny · free" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "gpt-4o");
    expect(onChange).toHaveBeenCalledWith("gpt-4o");
  });

  it("keeps a value that is not in the list visible", async () => {
    setup({ value: "old-model" });
    expect(await screen.findByRole("option", { name: "old-model (not in list)" })).toBeInTheDocument();
  });

  it("reloads the list from the provider when asked", async () => {
    const { calls, onChange } = setup();
    await screen.findByRole("option", { name: "tiny · free" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "__refresh");
    await waitFor(() => expect(calls.some((c) => c.url.search === "?refresh=true")).toBe(true));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("switches to a typed model id and back to the list", async () => {
    const { onChange } = setup();
    await screen.findByRole("option", { name: "tiny · free" });
    await userEvent.selectOptions(screen.getByRole("combobox"), "__custom");
    const input = screen.getByPlaceholderText("exact model id");
    expect(input).toHaveFocus();
    await userEvent.type(input, "x");
    expect(onChange).toHaveBeenCalledWith("x");
    await userEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("applies the class name to the picker", async () => {
    setup({ className: "wide" });
    expect(await screen.findByRole("combobox")).toHaveClass("wide");
  });

  it("states that the list came from the provider", async () => {
    setup({ showStatus: true });
    expect(await screen.findByText("4 models from the provider")).toBeInTheDocument();
  });

  it("explains a fallback to the public catalogue", async () => {
    setup({ showStatus: true }, resp({ source: "catalog", error: "403 from provider" }));
    expect(await screen.findByText("Showing LiteLLM's catalog (4) — couldn't list live: 403 from provider")).toBeInTheDocument();
  });

  it("shows why the list could not be loaded", async () => {
    routeFetch({ "GET /api/providers/sel-err/models": () => textResponse("x", 500) });
    render(<ModelSelect provider="sel-err" value="" onChange={() => {}} showStatus />);
    expect(await screen.findByText("HTTP 500")).toHaveClass("err");
  });

  it("does nothing without a provider", () => {
    const { fn } = routeFetch({});
    render(<ModelSelect provider="" value="" onChange={() => {}} />);
    expect(fn).not.toHaveBeenCalled();
  });

  it("reloads when the provider changes", async () => {
    routeFetch({
      "GET /api/providers/sel-x/models": () => jsonResponse(resp({ models: [{ id: "x-model", label: "x-model", input_per_m: null, output_per_m: null }] })),
      "GET /api/providers/sel-y/models": () => jsonResponse(resp({ models: [{ id: "y-model", label: "y-model", input_per_m: null, output_per_m: null }] })),
    });
    const { rerender } = render(<ModelSelect provider="sel-x" value="" onChange={() => {}} />);
    expect(await screen.findByRole("option", { name: "x-model" })).toBeInTheDocument();
    rerender(<ModelSelect provider="sel-y" value="" onChange={() => {}} />);
    expect(await screen.findByRole("option", { name: "y-model" })).toBeInTheDocument();
  });
});
