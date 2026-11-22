import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CustomEndpointForm, CustomProviderCard, ScopeBadge, parseHeaders } from "@/components/CustomEndpoints";
import type { Provider } from "@/lib/api";
import { jsonResponse, routeFetch } from "../helpers/http";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const P: Provider = {
  id: "custom_gpu", kind: "openai_compatible", label: "Office GPU", enabled: true, has_api_key: true, api_key_hint: "…1234", api_base: "http://192.168.1.5:8000/v1",
  vertex_project: null, vertex_location: null, vertex_credentials_path: null, default_model: "qwen3", pro_model: null, flash_model: null, cap_usd: 1, cap_period: "total",
  max_output_tokens: 4096, catalog: { label: "OpenAI-compatible endpoint", needs_key: false, note: "", default_model: "", auth_modes: ["api_key", "env", "none"], custom: true },
  auth_mode: "api_key", auth_modes: [{ id: "api_key", label: "API key" }, { id: "env", label: "Env var" }, { id: "none", label: "No key" }], api_key_env: null, key_env_default: "",
  has_service_account_json: false, service_account_email: null, extra_header_names: ["X-Team"], organization: null, auth_ready: true, auth_detail: "stored API key",
  custom: true, network_scope: "network", manual_models: ["hand"], model_prices: { "*": { input_per_m: 0, output_per_m: 0 }, qwen3: { input_per_m: 1, output_per_m: 2, context: 8192 } },
};
const MODELS = { provider: "custom_gpu", source: "live", error: null, cached: false, models: [{ id: "qwen3", label: "qwen3", input_per_m: 1, output_per_m: 2 }, { id: "hand", label: "hand", input_per_m: null, output_per_m: null }] };

describe("helpers", () => {
  it("labels where the endpoint is, defaulting to the cautious wording", () => {
    const { rerender } = render(<ScopeBadge scope="this_pc" />);
    expect(screen.getByTestId("scope-badge")).toHaveTextContent("on this PC");
    rerender(<ScopeBadge scope="network" />);
    expect(screen.getByTestId("scope-badge")).toHaveTextContent("on this network");
    rerender(<ScopeBadge scope="internet" />);
    expect(screen.getByTestId("scope-badge")).toHaveTextContent("data leaves this PC");
    rerender(<ScopeBadge />);
    expect(screen.getByTestId("scope-badge")).toHaveTextContent("data leaves this PC");
    rerender(<ScopeBadge scope="mars" />);
    expect(screen.getByTestId("scope-badge")).toHaveTextContent("data leaves this PC");
  });

  it("reads Name: value lines and ignores the rest", () => {
    expect(parseHeaders("X-A: 1\n bad line \nAuthorization: Bearer a:b\n\n")).toEqual({ "X-A": "1", Authorization: "Bearer a:b" });
  });
});

describe("CustomEndpointForm", () => {
  const fill = async (label = "Office GPU", base = "http://10.0.0.2:8000", model = "qwen3") => {
    await userEvent.type(screen.getByLabelText(/^Name/), label);
    await userEvent.type(screen.getByLabelText(/^Address/), base);
    if (model) await userEvent.type(screen.getByLabelText(/^Default model/), model);
  };

  it("finds the models first, then adds the endpoint with its prices, cap and key", async () => {
    const net = routeFetch({
      "POST /api/providers/custom/discover": () => jsonResponse({ ok: true, models: ["qwen3", "llama"], count: 2, detail: "2 model(s) found" }),
      "POST /api/providers/custom": () => jsonResponse({ ...P }),
    });
    const created = vi.fn();
    render(<CustomEndpointForm onCreated={created} onCancel={() => {}} />);
    expect(screen.getByRole("button", { name: "Add endpoint" })).toBeDisabled();
    await fill();
    await userEvent.type(screen.getByLabelText("API key"), "sk-1");
    await userEvent.type(screen.getByLabelText(/^Extra headers/), "X-Team: ml");
    await userEvent.type(screen.getByLabelText(/^Organization/), "org1");
    await userEvent.click(screen.getByRole("button", { name: "Find models" }));
    expect(await screen.findByText("2 model(s) found")).toBeInTheDocument();
    expect(net.calls[0].body).toMatchObject({ api_base: "http://10.0.0.2:8000", auth_mode: "api_key", api_key: "sk-1", headers: { "X-Team": "ml" }, organization: "org1" });
    await userEvent.type(screen.getByLabelText(/^Input price/), "0.5");
    await userEvent.type(screen.getByLabelText(/^Output price/), "1.5");
    await userEvent.type(screen.getByLabelText(/^Context size/), "32768");
    await userEvent.clear(screen.getByLabelText(/^Spend cap/));
    await userEvent.type(screen.getByLabelText(/^Spend cap/), "5");
    await userEvent.selectOptions(screen.getByLabelText(/^Cap window/), "monthly");
    await userEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(created).toHaveBeenCalledWith("custom_gpu"));
    const body = net.calls[1].body;
    expect(body).toMatchObject({ label: "Office GPU", default_model: "qwen3", cap_usd: 5, cap_period: "monthly", model_prices: { "*": { input_per_m: 0.5, output_per_m: 1.5, context: 32768 } }, manual_models: [] });
  });

  it("keeps a model that the endpoint did not list as a typed-in model, and sends no price when none is given", async () => {
    const net = routeFetch({ "POST /api/providers/custom": () => jsonResponse(P) });
    render(<CustomEndpointForm onCreated={() => {}} onCancel={() => {}} />);
    await userEvent.selectOptions(screen.getByLabelText(/^Sign-in/), "none");
    await fill("Mine", "http://localhost:1234", "my-model");
    await userEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(net.calls).toHaveLength(1));
    expect(net.calls[0].body).toMatchObject({ auth_mode: "none", api_key: null, manual_models: ["my-model"], model_prices: {} });
  });

  it("sends only a context size when no prices are typed", async () => {
    const net = routeFetch({ "POST /api/providers/custom": () => jsonResponse(P) });
    render(<CustomEndpointForm onCreated={() => {}} onCancel={() => {}} />);
    await fill();
    await userEvent.type(screen.getByLabelText(/^Context size/), "4096");
    await userEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(net.calls).toHaveLength(1));
    expect(net.calls[0].body.model_prices).toEqual({ "*": { context: 4096 } });
  });

  it("asks for both prices, or neither", async () => {
    const net = routeFetch({});
    render(<CustomEndpointForm onCreated={() => {}} onCancel={() => {}} />);
    await fill();
    await userEvent.type(screen.getByLabelText(/^Input price/), "1");
    await userEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    expect(await screen.findByText(/Give both the input and the output price/)).toBeInTheDocument();
    expect(net.calls).toHaveLength(0);
  });

  it("explains why the models could not be listed, and why adding failed", async () => {
    let n = 0;
    routeFetch({
      "POST /api/providers/custom/discover": () => (++n === 1 ? jsonResponse({ ok: false, kind: "unreachable", title: "Cannot reach it", detail: "Nothing answered at 10.0.0.2:8000." })
        : n === 2 ? jsonResponse({ detail: "the endpoint address must start with http://" }, { status: 422 }) : jsonResponse({ ok: true, count: 0, detail: "no models" })),
      "POST /api/providers/custom": () => jsonResponse({ detail: "choose a default model" }, { status: 422 }),
    });
    render(<CustomEndpointForm onCreated={() => {}} onCancel={() => {}} />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Find models" }));
    expect(await screen.findByText(/Cannot reach it: Nothing answered/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Find models" }));
    expect(await screen.findByText(/must start with http/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Find models" }));
    expect(await screen.findByText("no models")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Default model/)).toHaveAttribute("placeholder", "type the model name");
    await userEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    expect(await screen.findByText(/choose a default model/)).toBeInTheDocument();
  });

  it("offers an environment variable instead of a key, and can be cancelled", async () => {
    routeFetch({});
    const cancel = vi.fn();
    render(<CustomEndpointForm onCreated={() => {}} onCancel={cancel} />);
    await userEvent.selectOptions(screen.getByLabelText(/^Sign-in/), "env");
    expect(screen.queryByLabelText("API key")).toBeNull();
    await userEvent.type(screen.getByLabelText(/^Environment variable/), "MY_KEY");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(cancel).toHaveBeenCalled();
  });
});

describe("CustomProviderCard", () => {
  const setup = (over: Record<string, any> = {}) => routeFetch({
    "GET /api/providers/custom_gpu/models": () => jsonResponse(MODELS),
    "PUT /api/providers/custom_gpu": (init: any) => (over.putFail ? jsonResponse({ detail: "the name must be 1 to 60 characters" }, { status: 422 }) : jsonResponse({ ...P, ...JSON.parse(init.body) })),
    "POST /api/providers/custom_gpu/test": () => (over.testThrow ? jsonResponse({ detail: "boom" }, { status: 500 })
      : jsonResponse(over.testFail ? { ok: false, kind: "auth", title: "The key was refused", detail: "HTTP 401", scope: "network", scope_label: "on this network", model: "qwen3" }
        : { ok: true, kind: "ok", title: "Works", detail: "qwen3 answered in 40 ms.", reply: "ok", scope: "network", scope_label: "on this network", model: "qwen3", latency_ms: 40 })),
    "DELETE /api/providers/custom_gpu": () => (over.delFail ? jsonResponse({ detail: "still used by agent 'Alpha'" }, { status: 422 }) : jsonResponse({ ok: true })),
  });

  it("shows where the endpoint is, tests it and reports the answer", async () => {
    const net = setup();
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={() => {}} />);
    expect(screen.getByTestId("scope-badge")).toHaveTextContent("on this network");
    expect(screen.getByText("ready")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    const res = await screen.findByTestId("test-result");
    expect(res).toHaveTextContent("works");
    expect(res).toHaveTextContent("qwen3 answered in 40 ms.");
    expect(res).toHaveTextContent("Reply: “ok”");
    expect(net.calls.find((c) => c.url.pathname.endsWith("/test"))!.body).toEqual({ model: "qwen3" });
  });

  it("names the reason when the test fails or the request itself breaks", async () => {
    setup({ testFail: true });
    const { unmount } = render(<CustomProviderCard p={{ ...P, default_model: "" }} onSaved={() => {}} onDeleted={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByTestId("test-result")).toHaveTextContent("The key was refused");
    expect(screen.getByTestId("test-result")).not.toHaveTextContent("Reply:");
    unmount();
    setup({ testThrow: true });
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it("saves only what changed: name, address, key, headers, limits, typed-in models and prices", async () => {
    const net = setup();
    const saved = vi.fn();
    render(<CustomProviderCard p={P} onSaved={saved} onDeleted={() => {}} />);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(/^Name/));
    await userEvent.type(screen.getByLabelText(/^Name/), "Renamed");
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();                         // unsaved changes: save first
    await userEvent.type(screen.getByLabelText(/^Address/), "x");
    await userEvent.type(screen.getByLabelText(/^API key/), "sk-new");
    await userEvent.type(screen.getByLabelText(/^Extra headers/), "X-New: 1");
    await userEvent.type(screen.getByLabelText(/^Organization/), "org");
    await userEvent.clear(screen.getByLabelText(/^Spend cap/));
    await userEvent.type(screen.getByLabelText(/^Spend cap/), "3");
    await userEvent.clear(screen.getByLabelText(/^Max output tokens/));
    await userEvent.type(screen.getByLabelText(/^Max output tokens/), "2048");
    await userEvent.selectOptions(screen.getByLabelText(/^Cap window/), "daily");
    fireEvent.change(screen.getByLabelText(/^Models typed in by hand/), { target: { value: "hand\n second \n\n" } });
    await userEvent.click(screen.getByLabelText("enabled"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    const body = net.calls.find((c) => c.method === "PUT")!.body;
    expect(body).toMatchObject({ label: "Renamed", api_key: "sk-new", extra_headers: { "X-New": "1" }, organization: "org", cap_usd: 3, max_output_tokens: 2048, cap_period: "daily", manual_models: ["hand", "second"], enabled: false });
    expect(body.api_base).toBe("http://192.168.1.5:8000/v1x");
    expect(body._headers).toBeUndefined();
  });

  it("edits the price table: a default row, a row per model, clearing a field and adding another model", async () => {
    const net = setup();
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={() => {}} />);
    const table = screen.getByRole("table", { name: "Model prices" });
    expect(within(table).getByText("All other models")).toBeInTheDocument();
    expect(within(table).getByText("hand")).toBeInTheDocument();
    fireEvent.change(within(table).getByLabelText("hand input"), { target: { value: "0.3" } });
    fireEvent.change(within(table).getByLabelText("hand output"), { target: { value: "0.9" } });
    fireEvent.change(within(table).getByLabelText("qwen3 context"), { target: { value: "" } });
    fireEvent.change(within(table).getByLabelText("default input"), { target: { value: "" } });
    fireEvent.change(within(table).getByLabelText("default output"), { target: { value: "" } });         // the last field of a row clears the whole row
    await userEvent.type(screen.getByLabelText("Another model name"), "newbie");
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    expect(within(table).getByText("newbie")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "PUT")).toBe(true));
    const prices = net.calls.find((c) => c.method === "PUT")!.body.model_prices;
    expect(prices).toEqual({ qwen3: { input_per_m: 1, output_per_m: 2 }, hand: { input_per_m: 0.3, output_per_m: 0.9 }, newbie: { input_per_m: 0, output_per_m: 0 } });
    expect(screen.getByRole("button", { name: "Add row" })).toBeDisabled();
  });

  it("keeps the typed price of a row that is added twice", async () => {
    setup();
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={() => {}} />);
    await userEvent.type(screen.getByLabelText("Another model name"), "qwen3");
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    expect(screen.getByLabelText("qwen3 input")).toHaveValue(1);
  });

  it("picks the default, pro and flash models from the endpoint's list", async () => {
    const net = setup();
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("option", { name: /^hand/ }).length).toBeGreaterThan(0));
    const [def, pro, flash] = screen.getAllByRole("combobox").filter((el) => within(el).queryByText("Choose a model") || within(el).queryByText("Same as default"));
    await userEvent.selectOptions(def, "hand");
    await userEvent.selectOptions(pro, "qwen3");
    await userEvent.selectOptions(flash, "hand");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "PUT")).toBe(true));
    expect(net.calls.find((c) => c.method === "PUT")!.body).toMatchObject({ default_model: "hand", pro_model: "qwen3", flash_model: "hand" });
  });

  it("shows the environment-variable field and the stored key state", async () => {
    setup();
    const { rerender } = render(<CustomProviderCard p={{ ...P, auth_mode: "env", api_key_env: "MY_KEY", has_api_key: false }} onSaved={() => {}} onDeleted={() => {}} />);
    expect(screen.getByLabelText(/^Environment variable/)).toHaveValue("MY_KEY");
    await userEvent.type(screen.getByLabelText(/^Environment variable/), "2");
    expect(screen.getByLabelText(/^Environment variable/)).toHaveValue("MY_KEY2");
    await userEvent.selectOptions(screen.getByLabelText(/^Sign-in/), "api_key");
    expect(screen.getByLabelText(/^API key/)).toHaveAttribute("placeholder", "paste key");
    rerender(<CustomProviderCard p={{ ...P, extra_header_names: [], auth_ready: false, auth_detail: "set the endpoint address", model_prices: undefined, manual_models: undefined }} onSaved={() => {}} onDeleted={() => {}} />);
    expect(screen.getByText("incomplete")).toBeInTheDocument();
  });

  it("reports a refused save", async () => {
    setup({ putFail: true });
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={() => {}} />);
    await userEvent.type(screen.getByLabelText(/^Name/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/1 to 60 characters/)).toBeInTheDocument();
  });

  it("deletes only after confirming, and says why it cannot", async () => {
    const net = setup();
    const gone = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    const { unmount } = render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={gone} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(net.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(gone).toHaveBeenCalled());
    unmount();
    setup({ delFail: true });
    render(<CustomProviderCard p={P} onSaved={() => {}} onDeleted={gone} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(/still used by agent 'Alpha'/)).toBeInTheDocument();
  });
});
