import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import OcsfSettings from "@/components/OcsfSettings";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const KINDS = [
  { id: "file", label: "Rotating file", note: "Saved in the data folder.", fields: [{ name: "file", label: "File name", secret: false, required: false, placeholder: "agent-studio-ocsf.ndjson" }, { name: "max_mb", label: "Rotate at (MB)", secret: false, required: false, placeholder: "10" }] },
  { id: "https", label: "HTTPS webhook or Splunk HEC", note: "Formats: ndjson.", fields: [{ name: "url", label: "URL", secret: false, required: true, placeholder: "https://siem" }, { name: "token", label: "Token", secret: true, required: false, placeholder: "" }] },
];
const SINK = { id: "disk", kind: "file", enabled: true, level: "", config: { file: "ev.ndjson" }, secrets_set: [], stats: { sent: 4, failed: 1, dropped: 0, last_error: "boom" }, where: "file ocsf/ev.ndjson" };
const CFG = { enabled: false, level: "public", classes: { agent_runs: true, model_calls: true, tool_calls: false, security: true, config_changes: true }, sinks: [] as any[], batch_size: 100, flush_seconds: 5,
  levels: { public: "Safe to share.", internal: "Adds more.", full: "Everything." }, class_info: { agent_runs: "An agent finished a run", model_calls: "A model was called", tool_calls: "A tool was used", security: "A spend cap blocked something", config_changes: "A setting changed" },
  schema_version: "1.10.0", kinds: KINDS };
const EV = { type_name: "API Activity: Read", message: "engineer used read_file", class_uid: 6003 };

function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  let cfg: any = { ...CFG, ...over };
  return routeFetch({
    "GET /api/ocsf/config": () => jsonResponse(cfg), "GET /api/ocsf/stats": () => jsonResponse({ queued: 0, dropped_queue_full: 2, mapped: 9, invalid: 0, queue: 3 }),
    "PUT /api/ocsf/config": (init: any) => { const p = JSON.parse(init.body); cfg = { ...cfg, ...p, classes: { ...cfg.classes, ...(p.classes ?? {}) },
      sinks: p.sinks ? p.sinks.map((s: any) => ({ ...s, secrets_set: Object.keys(p.secrets?.[s.id] ?? {}), stats: {}, where: "somewhere" })) : cfg.sinks }; return jsonResponse(cfg); },
    "POST /api/ocsf/sinks/disk/test": () => jsonResponse({ ok: true, detail: "A test event was delivered to file ocsf/ev.ndjson" }),
    "GET /api/ocsf/preview": () => jsonResponse({ level: "public", sample: true, schema_version: "1.10.0", events: [{ event: EV, problems: [] }, { event: { type_name: "Detection Finding: Create" }, problems: [] }] }),
    ...extra,
  });
}
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT").map((c) => c.body);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("OcsfSettings", () => {
  it("explains what is released OCSF and what is convention, and shows the switch, level and event kinds", async () => {
    const net = setup();
    render(<OcsfSettings />);
    expect(await screen.findByText(/follow the released OCSF schema/)).toBeInTheDocument();
    expect(screen.getByText(/OWASP Agent Observability Standard/)).toBeInTheDocument();
    expect(screen.getByText("off")).toBeInTheDocument();
    expect(screen.getByText("Safe to share.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Send security events" }));
    await userEvent.selectOptions(screen.getByLabelText(/How much detail/), "internal");
    await userEvent.click(screen.getByRole("checkbox", { name: "A tool was used" }));
    const batch = screen.getByLabelText("Send in batches of (events)");
    fireEvent.change(batch, { target: { value: "50" } }); fireEvent.blur(batch);
    const every = screen.getByLabelText("At least every (seconds)");
    fireEvent.change(every, { target: { value: "9" } }); fireEvent.blur(every);
    await waitFor(() => expect(puts(net.calls)).toEqual([{ enabled: true }, { level: "internal" }, { classes: { tool_calls: true } }, { batch_size: 50 }, { flush_seconds: 9 }]));
  });

  it("adds a destination with a write-only credential, then switches it, tests it, edits it and removes it", async () => {
    const net = setup({ enabled: true });
    render(<OcsfSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a destination" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Add a destination" }));
    expect(screen.getByRole("button", { name: "Add destination" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Name"), "SIEM");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "https");
    expect(screen.getByText("Formats: ndjson.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^URL/), "https://siem.example.com/x");
    await userEvent.type(screen.getByLabelText(/Token/), "super-secret");
    await userEvent.selectOptions(screen.getByLabelText(/Detail level for this destination/), "full");
    await userEvent.click(screen.getByRole("button", { name: "Add destination" }));
    await waitFor(() => expect(puts(net.calls)[0]).toEqual({ sinks: [{ id: "siem", kind: "https", enabled: true, level: "full", config: { url: "https://siem.example.com/x" } }], secrets: { siem: { token: "super-secret" } } }));
    expect(await screen.findByText("somewhere")).toBeInTheDocument();
    expect(screen.queryByTestId("sink-form")).toBeNull();
  });

  it("shows each destination's counters, sends a test event, toggles it, edits it and asks before removing it", async () => {
    const net = setup({ enabled: true, sinks: [SINK, { ...SINK, id: "second", kind: "https", config: { url: "https://x" }, secrets_set: ["token"], level: "internal", stats: {} }] });
    render(<OcsfSettings />);
    const [disk, second] = await screen.findAllByTestId("sink");
    expect(disk).toHaveTextContent("sent 4 · failed attempts 1 · dropped 0 · last problem: boom");
    expect(second).toHaveTextContent("internal detail");
    expect(second).toHaveTextContent("sent 0");
    await userEvent.click(within(disk).getByRole("button", { name: "Send a test event" }));
    expect(await within(disk).findByRole("status")).toHaveTextContent("A test event was delivered");
    await userEvent.click(within(second).getByRole("button", { name: "Edit" }));
    const form = screen.getByTestId("sink-form");
    expect(within(form).getByLabelText("Name")).toBeDisabled();
    expect(within(form).getByLabelText(/Token/)).toHaveAttribute("placeholder", "(stored: leave empty to keep)");
    await userEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("sink-form")).toBeNull();
    await userEvent.click(within(disk).getByRole("checkbox", { name: "disk on" }));
    await waitFor(() => expect(puts(net.calls)[0].sinks[0]).toEqual({ id: "disk", kind: "file", enabled: false, level: "", config: { file: "ev.ndjson" } }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    await userEvent.click(within(screen.getAllByTestId("sink")[1]).getByRole("button", { name: "Remove" }));
    expect(puts(net.calls)).toHaveLength(1);
    await userEvent.click(within(screen.getAllByTestId("sink")[1]).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(puts(net.calls)[1].sinks.map((s: any) => s.id)).toEqual(["disk"]));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("saves an edited destination, turning numeric fields into numbers", async () => {
    const net = setup({ enabled: true, sinks: [SINK] });
    render(<OcsfSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText(/Rotate at/), "25");
    await userEvent.click(screen.getByRole("button", { name: "Save destination" }));
    await waitFor(() => expect(puts(net.calls)[0]).toEqual({ sinks: [{ id: "disk", kind: "file", enabled: true, level: "", config: { file: "ev.ndjson", max_mb: 25 } }] }));
  });

  it("reports a test that cannot be run and a destination the server refuses", async () => {
    setup({ enabled: true, sinks: [SINK] }, { "POST /api/ocsf/sinks/disk/test": () => jsonResponse({ detail: "no sink" }, { status: 404 }), "PUT /api/ocsf/config": () => jsonResponse({ detail: "sink 'disk': bad file name" }, { status: 422 }) });
    render(<OcsfSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Send a test event" }));
    expect(await screen.findByRole("status")).toHaveClass("bad");
    await userEvent.click(screen.getByRole("checkbox", { name: "disk on" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("bad file name");
  });

  it("previews example events and says whether they are valid OCSF", async () => {
    setup({ enabled: true });
    render(<OcsfSettings />);
    expect(await screen.findByText(/dropped when busy 2/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show example events" }));
    expect(await screen.findByText(/Nothing has happened yet, so these are examples/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("All 2 events are valid OCSF 1.10.0");
    await userEvent.click(screen.getByText(/API Activity: Read — engineer used read_file/));
    expect(screen.getByText(/"class_uid": 6003/)).toBeInTheDocument();
  });

  it("lists the problems when an event does not match the schema, and shows the latest events when there are some", async () => {
    setup({}, { "GET /api/ocsf/preview": () => jsonResponse({ level: "internal", sample: false, schema_version: "1.10.0", events: [{ event: EV, problems: ["api: required"] }, { event: EV, problems: ["a", "b"] }] }) });
    render(<OcsfSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Show example events" }));
    expect(await screen.findByText(/The latest events/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Some events do not match the schema");
    expect(screen.getByText(/⚠ 1 problem$/)).toBeInTheDocument();
    expect(screen.getByText(/⚠ 2 problems$/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByText(/API Activity: Read/)[0]);
    expect(screen.getByText("api: required")).toBeInTheDocument();
  });

  it("shows when the preview or the settings cannot be loaded", async () => {
    setup({}, { "GET /api/ocsf/preview": () => jsonResponse({ detail: "no" }, { status: 500 }) });
    const first = render(<OcsfSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Show example events" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    first.unmount();
    routeFetch({ "GET /api/ocsf/config": () => jsonResponse({ detail: "down" }, { status: 500 }), "GET /api/ocsf/stats": () => jsonResponse({}) });
    render(<OcsfSettings />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("copes with a destination of a kind it does not know", async () => {
    setup({ enabled: true, sinks: [{ ...SINK, kind: "mystery" }] });
    render(<OcsfSettings />);
    expect(await screen.findByText("mystery")).toBeInTheDocument();
  });
});
