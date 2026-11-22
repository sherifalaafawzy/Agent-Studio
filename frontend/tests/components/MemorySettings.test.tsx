import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import MemorySettings from "@/components/MemorySettings";
import { dirtyIds, setDirty } from "@/lib/dirty";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const CFG = {
  enabled: true, backend: "local", backend_config: {}, embedding: { provider: "", model: "" }, review: false, redact_pii: true, auto_extract: "rules", extract_model: { provider: "", model: "" },
  types: { episodic: { enabled: true, ttl_days: 90, max_items: 1000 }, semantic: { enabled: true, ttl_days: 0, max_items: 2000 }, entity: { enabled: false, ttl_days: 0, max_items: 2000 },
    procedural: { enabled: true, ttl_days: 0, max_items: 500 } },
  recall_budget_chars: 1500, recall_k: 5, recall_min_score: 0.2, consolidation: { dedupe: true, merge_contradictions: true, similarity: 0.95, interval_hours: 24 },
  notes_file: "AGENTS.md", notes_max_chars: 4000, secrets_set: [] as string[], backend_cloud: false, backend_label: "On this PC",
  type_info: { semantic: { label: "Facts", description: "Facts about you" }, episodic: { label: "Past chats", description: "Earlier chats" }, entity: { label: "People & things", description: "Records" }, procedural: { label: "How-tos", description: "Steps" } },
};
const KINDS = [
  { id: "local", label: "On this PC", description: "Works offline.", cloud: false, notes: "", verified: "", fields: [] },
  { id: "vertex", label: "Google Vertex AI Memory Bank", description: "Google keeps a copy.", cloud: true, notes: "Uses v1beta1.", verified: "discovery document",
    fields: [{ name: "bank", label: "Memory bank", secret: false, required: true, help: "the resource name" }, { name: "service_account_json", label: "Service account JSON", secret: true, required: false, help: "" }] },
];
const PROVIDERS = [{ id: "openai", label: "OpenAI", enabled: true }, { id: "off", label: "Off one", enabled: false }];

function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  let cfg: any = { ...CFG, ...over };
  const net = routeFetch({
    "GET /api/memories/config": () => jsonResponse(cfg), "GET /api/memories/kinds": () => jsonResponse(KINDS), "GET /api/providers": () => jsonResponse(PROVIDERS),
    "GET /api/rag/embedding-models/openai": () => jsonResponse({ models: [{ model: "text-embedding-3-small" }, { model: "text-embedding-3-large" }] }),
    "PUT /api/memories/config": (init: any) => { const p = JSON.parse(init.body); cfg = { ...cfg, ...p, types: p.types ? Object.fromEntries(Object.entries(cfg.types).map(([k, v]: any) => [k, { ...v, ...(p.types[k] ?? {}) }])) : cfg.types,
      consolidation: { ...cfg.consolidation, ...(p.consolidation ?? {}) }, secrets_set: p.secrets ? Object.keys(p.secrets) : cfg.secrets_set }; return jsonResponse(cfg); },
    "GET /api/memories/health": () => jsonResponse({ ok: true, detail: "Memories are kept on this PC" }),
    ...extra,
  });
  return net;
}
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT" && c.url.pathname === "/api/memories/config").map((c) => c.body);

afterEach(() => { vi.unstubAllGlobals(); for (const id of dirtyIds()) setDirty(id, false); });

describe("MemorySettings", () => {
  it("shows every setting and saves a switch as soon as it is changed", async () => {
    const net = setup();
    render(<MemorySettings />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Memory is on" }));
    await waitFor(() => expect(puts(net.calls)).toEqual([{ enabled: false }]));
    await userEvent.click(screen.getByRole("checkbox", { name: "Remove personal data before saving" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Always ask me before a new memory is used" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Merge memories that say the same thing" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "A newer fact replaces an older one with the same key" }));
    await waitFor(() => expect(puts(net.calls).slice(1)).toEqual([{ redact_pii: false }, { review: true }, { consolidation: { dedupe: false } }, { consolidation: { merge_contradictions: false } }]));
  });

  it("edits how long each kind is kept and how many are kept, and switches kinds on and off", async () => {
    const net = setup();
    render(<MemorySettings />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "People & things on" }));
    await waitFor(() => expect(puts(net.calls)).toEqual([{ types: { entity: { enabled: true } } }]));
    const row = document.querySelector('[data-setting="types-episodic"]') as HTMLElement;
    const days = within(row).getByLabelText("Keep (days, 0 = forever)");
    fireEvent.change(days, { target: { value: "30" } }); fireEvent.blur(days);
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ types: { episodic: { ttl_days: 30 } } }));
    const most = within(row).getByLabelText("Most to keep");
    fireEvent.change(most, { target: { value: "5" } }); fireEvent.blur(most);                       // under the limit of 10: refused on screen, never sent
    expect(await within(row).findByRole("alert")).toHaveTextContent("between 10 and 100000");
    expect(puts(net.calls)).toHaveLength(2);
    fireEvent.change(most, { target: { value: "50" } }); fireEvent.blur(most);
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ types: { episodic: { max_items: 50 } } }));
  });

  it("switches to a cloud backend, warns about it, saves its details with the key write-only, and tests the connection", async () => {
    const net = setup({ secrets_set: ["service_account_json"] });
    render(<MemorySettings />);
    await userEvent.selectOptions(await screen.findByLabelText("Backend"), "vertex");
    expect(screen.getByText(/Memory text is sent to this cloud service/)).toBeInTheDocument();
    expect(screen.getByText(/Uses v1beta1/)).toBeInTheDocument();
    expect(screen.getByText(/API checked against: discovery document/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save backend" })).toBeEnabled();
    await userEvent.type(screen.getByLabelText(/Memory bank/), "projects/p/locations/us-central1/reasoningEngines/1");
    expect((screen.getByLabelText(/Service account JSON/) as HTMLInputElement).placeholder).toBe("(stored: leave empty to keep)");
    await userEvent.type(screen.getByLabelText(/Service account JSON/), "{{}");
    await userEvent.click(screen.getByRole("button", { name: "Save backend" }));
    await waitFor(() => expect(puts(net.calls)[0]).toEqual({ backend: "vertex", backend_config: { bank: "projects/p/locations/us-central1/reasoningEngines/1" }, secrets: { service_account_json: "{}" } }));
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Memories are kept on this PC");
  });

  it("saves a backend without secrets, and shows a refused backend and a failed connection test", async () => {
    const net = setup({}, { "GET /api/memories/health": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<MemorySettings />);
    await userEvent.selectOptions(await screen.findByLabelText("Backend"), "vertex");
    await userEvent.type(screen.getByLabelText(/Memory bank/), "x");
    net.fn.mockClear();
    routeFetch({ "PUT /api/memories/config": () => jsonResponse({ detail: "Memory bank must look like projects/..." }, { status: 422 }), "GET /api/memories/health": () => jsonResponse({ ok: false, detail: "unreachable" }) });
    await userEvent.click(screen.getByRole("button", { name: "Save backend" }));
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent(/must look like/);
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("status")).toHaveTextContent("unreachable");
    expect(screen.getByRole("status")).toHaveClass("bad");
  });

  it("reports a connection test that cannot even be made", async () => {
    setup({}, { "GET /api/memories/health": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<MemorySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("status")).toHaveClass("bad");
  });

  it("chooses an embedding model for finding memories by meaning, and word matching otherwise", async () => {
    const net = setup();
    render(<MemorySettings />);
    const prov = await screen.findByLabelText("Embedding provider");
    expect(screen.queryByLabelText("Embedding model")).toBeNull();
    expect(within(prov).queryByText("Off one")).toBeNull();                                          // only enabled providers are offered
    await userEvent.selectOptions(prov, "openai");
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ embedding: { provider: "openai", model: "" } }));
    const model = await screen.findByLabelText("Embedding model");
    await waitFor(() => expect(document.querySelectorAll("#mem-models option")).toHaveLength(2));
    await userEvent.type(model, "text-embedding-3-small");
    fireEvent.blur(model);
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ embedding: { provider: "openai", model: "text-embedding-3-small" } }));
    fireEvent.blur(model);                                                                             // unchanged: nothing more is sent
    expect(puts(net.calls)).toHaveLength(2);
  });

  it("lets the embedding model list fail quietly", async () => {
    setup({ embedding: { provider: "openai", model: "m" } }, { "GET /api/rag/embedding-models/openai": () => jsonResponse({}, { status: 500 }) });
    render(<MemorySettings />);
    expect(await screen.findByLabelText("Embedding model")).toHaveValue("m");
    expect(document.querySelectorAll("#mem-models option")).toHaveLength(0);
  });

  it("edits recall, consolidation, notes and the way memories are picked up", async () => {
    const net = setup();
    render(<MemorySettings />);
    await screen.findByLabelText("Memories to bring up");
    for (const [label, value, patch] of [["Memories to bring up", "7", { recall_k: 7 }], ["Most characters added to a prompt", "900", { recall_budget_chars: 900 }],
      ["Weakest match to use (0 to 1)", "0.4", { recall_min_score: 0.4 }], ["Treat as the same when this similar (0.5 to 1)", "0.9", { consolidation: { similarity: 0.9 } }],
      ["Tidy up every (hours)", "12", { consolidation: { interval_hours: 12 } }], ["Most characters read per chat", "2000", { notes_max_chars: 2000 }]] as [string, string, object][]) {
      const el = screen.getByLabelText(label);
      fireEvent.change(el, { target: { value } }); fireEvent.blur(el);
      await waitFor(() => expect(puts(net.calls).at(-1)).toEqual(patch));
    }
    const file = screen.getByLabelText(/Notes file name/);
    fireEvent.change(file, { target: { value: "NOTES.md" } }); fireEvent.blur(file);
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ notes_file: "NOTES.md" }));
    fireEvent.blur(file);
    await userEvent.selectOptions(screen.getByLabelText("How new memories are picked up"), "model");
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ auto_extract: "model" }));
    const extractProv = await screen.findByLabelText("Model provider");
    await userEvent.selectOptions(extractProv, "openai");
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ extract_model: { provider: "openai", model: "" } }));
    const extractModel = screen.getByLabelText("Model");
    await userEvent.type(extractModel, "gpt-5-mini"); fireEvent.blur(extractModel);
    await waitFor(() => expect(puts(net.calls).at(-1)).toEqual({ extract_model: { provider: "openai", model: "gpt-5-mini" } }));
    fireEvent.blur(extractModel);
  });

  it("shows when the settings cannot be loaded or saved", async () => {
    routeFetch({ "GET /api/memories/config": () => jsonResponse({ detail: "boom" }, { status: 500 }), "GET /api/memories/kinds": () => jsonResponse([]), "GET /api/providers": () => jsonResponse([]) });
    const { unmount } = render(<MemorySettings />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    unmount();
    setup({}, { "PUT /api/memories/config": () => jsonResponse({ detail: "recall_k must be 1 to 20" }, { status: 422 }) });
    render(<MemorySettings />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Memory is on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("recall_k must be");
  });

  it("guards the unsaved backend form, and renders a local backend that is not in the list", async () => {
    setup({ backend: "ghost" });
    render(<MemorySettings />);
    await screen.findByLabelText("Backend");
    expect(dirtyIds()).not.toContain("memory-backend");
    await userEvent.selectOptions(screen.getByLabelText("Backend"), "vertex");
    expect(dirtyIds()).toContain("memory-backend");
  });
});
