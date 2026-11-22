import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import GuardrailSettings from "@/components/GuardrailSettings";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const KINDS = [
  { id: "rules", label: "Built-in rules", summary: "Pattern checks.", where: "stays on this PC", leaves_pc: false, cost: "free", fields: [{ name: "blocklist", label: "Blocked words", type: "textarea", default: "" }] },
  { id: "azure_safety", label: "Azure AI Content Safety", summary: "Microsoft classifier.", where: "leaves this PC (sent to Microsoft Azure)", leaves_pc: true, cost: "cloud",
    fields: [{ name: "endpoint", label: "Resource endpoint", required: true, default: "" }, { name: "api_key", label: "Key", type: "secret", required: true }] },
];
const BASIC = { id: "basic", name: "Basic rules", kind: "rules", params: {}, stages: { input: true, output: true, tool: false }, mode: "monitor", fail: "open", default: true, secrets_set: [], leaves_pc: false, stored: false };
const AZ = { id: "az", name: "Azure", kind: "azure_safety", params: { endpoint: "https://r.example" }, stages: { input: true, output: false, tool: false }, mode: "block", fail: "closed", default: false, secrets_set: ["api_key"], leaves_pc: true, stored: true };
const EVENT = { ts: 1_700_000_000, stage: "input", agent: "engineer", profile: "basic", action: "blocked", category: "pii", reason: "contains an email address" };

function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  let cfg: any = { enabled: false, profiles: [BASIC, AZ], kinds: KINDS, ...over };
  return routeFetch({
    "GET /api/guardrails": () => jsonResponse(cfg),
    "GET /api/guardrails/events": () => jsonResponse({ events: [EVENT] }),
    "GET /api/guardrails/stats": () => jsonResponse({ days: 7, flagged: 3, blocked: 2, errors: 1, top_categories: [{ category: "pii", count: 4 }] }),
    "PUT /api/guardrails/enabled": (init: any) => { cfg = { ...cfg, enabled: JSON.parse(init.body).enabled }; return jsonResponse(cfg); },
    "PUT /api/guardrails/profiles": (init: any) => { const p = JSON.parse(init.body); cfg = { ...cfg, profiles: [...cfg.profiles.filter((x: any) => x.id !== p.id), { ...BASIC, ...p, secrets_set: [], leaves_pc: false, stored: true }] }; return jsonResponse(cfg); },
    "DELETE /api/guardrails/profiles/az": () => { cfg = { ...cfg, profiles: cfg.profiles.filter((x: any) => x.id !== "az") }; return jsonResponse(cfg); },
    "POST /api/guardrails/test": (init: any) => { const b = JSON.parse(init.body); return jsonResponse(b.text.includes("flag") ? { ok: true, flagged: true, category: "pii", reason: "contains an email address", would: "block", ms: 3 }
      : b.text.includes("boom") ? { ok: false, error: "cannot reach the model" } : { ok: true, flagged: false, ms: 2 }); },
    ...extra,
  });
}
const bodies = (calls: Call[], m: string, path: string) => calls.filter((c) => c.method === m && c.url.pathname === path).map((c) => c.body);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("GuardrailSettings", () => {
  it("shows the switch, the numbers and the latest catches; the switch calls the server", async () => {
    const net = setup();
    render(<GuardrailSettings />);
    expect(await screen.findByText("off")).toBeInTheDocument();
    expect(screen.getByText(/3 flagged · 2 blocked · 1 checks that could not answer · most common: pii/)).toBeInTheDocument();
    expect(screen.getByText(/contains an email address/)).toBeInTheDocument();
    expect(screen.getByText(/Text streamed to the screen before the end/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Guardrails on" }));
    await waitFor(() => expect(bodies(net.calls, "PUT", "/api/guardrails/enabled")).toEqual([{ enabled: true }]));
    expect(await screen.findByText("on")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Guardrails on" }));
    await waitFor(() => expect(screen.getByText("off")).toBeInTheDocument());
  });

  it("describes each profile, and warns when text leaves the PC", async () => {
    setup();
    render(<GuardrailSettings />);
    const [basic, az] = await screen.findAllByTestId("profile");
    expect(within(basic).getByText("Record only")).toBeInTheDocument();
    expect(within(basic).getByText("default")).toBeInTheDocument();
    expect(within(basic).getByText(/messages and tool results going in, answers coming out/)).toBeInTheDocument();
    expect(within(basic).queryByRole("button", { name: "Remove" })).toBeNull();                    // the built-in profile cannot be removed
    expect(within(az).getByText("Block")).toBeInTheDocument();
    expect(within(az).getByText("sends text to the cloud")).toBeInTheDocument();
    expect(within(az).getByText(/if it cannot answer: stops/)).toBeInTheDocument();
  });

  it("tries a saved profile on some text and reports what it found", async () => {
    setup();
    render(<GuardrailSettings />);
    const [basic] = await screen.findAllByTestId("profile");
    await userEvent.click(within(basic).getByRole("button", { name: "Try it" }));
    const box = within(basic).getByLabelText("Text to check");
    expect(within(basic).getByRole("button", { name: "Check it" })).toBeDisabled();
    await userEvent.type(box, "please flag this");
    await userEvent.selectOptions(within(basic).getByLabelText("Where to check it"), "output");
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText(/Flagged \(pii\): contains an email address - would block · 3 ms/)).toBeInTheDocument();
    await userEvent.clear(box); await userEvent.type(box, "fine");
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText(/Nothing found - it would be let through · 2 ms/)).toBeInTheDocument();
    await userEvent.clear(box); await userEvent.type(box, "boom");
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText("cannot reach the model")).toBeInTheDocument();
    await userEvent.click(within(basic).getByRole("button", { name: "Try it" }));
    expect(within(basic).queryByLabelText("Text to check")).toBeNull();
    await userEvent.click(within(basic).getByRole("button", { name: "Try it" }));
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("network down"); }));
    await userEvent.type(within(basic).getByLabelText("Text to check"), "anything");
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText("network down")).toBeInTheDocument();
  });

  it("adds a local profile: the id follows the name, and it saves with the chosen mode", async () => {
    const net = setup();
    render(<GuardrailSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a profile" }));
    const form = screen.getByTestId("profile-form");
    expect(within(form).getByRole("button", { name: "Add profile" })).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Name"), "My Rules!");
    expect(within(form).getByLabelText("Id")).toHaveValue("my-rules-");
    await userEvent.type(within(form).getByLabelText("Id"), "x");                                    // the id can also be typed by hand
    expect(within(form).getByLabelText("Id")).toHaveValue("my-rules-x");
    await userEvent.type(within(form).getByLabelText("Name"), "!");                                  // for a new profile it follows the name again
    expect(within(form).getByLabelText("Id")).toHaveValue("my-rules-");
    await userEvent.type(within(form).getByLabelText(/Blocked words/), "falcon");
    await userEvent.selectOptions(within(form).getByLabelText(/When it finds something/), "block");
    await userEvent.selectOptions(within(form).getByLabelText(/If the check cannot answer/), "closed");
    await userEvent.click(within(form).getByLabelText(/Answers coming out/));
    await userEvent.click(within(form).getByLabelText(/Use for every agent/));
    await userEvent.click(within(form).getByRole("button", { name: "Add profile" }));
    await waitFor(() => expect(bodies(net.calls, "PUT", "/api/guardrails/profiles")).toEqual([{ id: "my-rules-", name: "My Rules!!", kind: "rules", params: { blocklist: "falcon" },
      stages: { input: true, output: false, tool: true }, mode: "block", fail: "closed", default: true, secrets: {} }]));
    expect(screen.queryByTestId("profile-form")).toBeNull();
  });

  it("makes you acknowledge that a cloud guard receives the text, keeps secrets write-only, and can try the draft", async () => {
    const net = setup();
    render(<GuardrailSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a profile" }));
    const form = screen.getByTestId("profile-form");
    await userEvent.selectOptions(within(form).getByLabelText("Kind of check"), "azure_safety");
    expect(within(form).getByRole("note")).toHaveTextContent(/sent to a cloud service/);
    await userEvent.type(within(form).getByLabelText("Name"), "Az2");
    await userEvent.type(within(form).getByLabelText(/Resource endpoint/), "https://x.example");
    await userEvent.type(within(form).getByLabelText(/^Key/), "TOPSECRET");
    expect(within(form).getByRole("button", { name: "Add profile" })).toBeDisabled();
    await userEvent.type(within(form).getByLabelText("Text to check"), "boom text");
    await userEvent.click(within(form).getByRole("button", { name: "Check it" }));
    expect(await within(form).findByText("cannot reach the model")).toBeInTheDocument();
    expect(JSON.stringify(bodies(net.calls, "POST", "/api/guardrails/test"))).toContain("TOPSECRET");                   // sent to the server for the test only
    await userEvent.click(within(form).getByLabelText(/I understand the checked text leaves this PC/));
    await userEvent.click(within(form).getByRole("button", { name: "Add profile" }));
    await waitFor(() => expect(bodies(net.calls, "PUT", "/api/guardrails/profiles")[0]).toMatchObject({ id: "az2", kind: "azure_safety", secrets: { api_key: "TOPSECRET" } }));
  });

  it("edits a stored profile (kind and id fixed, stored secret kept) and cancels cleanly", async () => {
    const net = setup();
    render(<GuardrailSettings />);
    const az = (await screen.findAllByTestId("profile"))[1];
    await userEvent.click(within(az).getByRole("button", { name: "Edit" }));
    const form = screen.getByTestId("profile-form");
    expect(within(form).getByLabelText("Id")).toBeDisabled();
    expect(within(form).getByLabelText("Kind of check")).toBeDisabled();
    expect(within(form).getByLabelText(/^Key/)).toHaveAttribute("placeholder", "(stored: leave empty to keep)");
    await userEvent.type(within(form).getByLabelText("Name"), " v2");
    expect(within(form).getByLabelText("Id")).toHaveValue("az");                                  // renaming does not change the id
    await userEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("profile-form")).toBeNull();
    await userEvent.click(within(screen.getAllByTestId("profile")[1]).getByRole("button", { name: "Edit" }));
    await userEvent.click(within(screen.getByTestId("profile-form")).getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(bodies(net.calls, "PUT", "/api/guardrails/profiles")[0]).toMatchObject({ id: "az", secrets: {}, mode: "block" }));
  });

  it("removes a stored profile only after you confirm", async () => {
    const net = setup();
    render(<GuardrailSettings />);
    const az = (await screen.findAllByTestId("profile"))[1];
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(within(az).getByRole("button", { name: "Remove" }));
    expect(net.calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(az).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.getAllByTestId("profile")).toHaveLength(1));
  });

  it("lets you back out of adding a profile", async () => {
    setup();
    render(<GuardrailSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a profile" }));
    expect(screen.getByTestId("profile-form")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("profile-form")).toBeNull();
  });

  it("shows the server's reason when saving fails, and when the page cannot load", async () => {
    setup({}, { "PUT /api/guardrails/profiles": () => jsonResponse({ detail: "the id must be lowercase" }, { status: 422 }) });
    render(<GuardrailSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a profile" }));
    await userEvent.type(screen.getByLabelText("Name"), "Zed");
    await userEvent.click(screen.getByRole("button", { name: "Add profile" }));
    expect(await screen.findByText("the id must be lowercase")).toBeInTheDocument();
    expect(screen.getByTestId("profile-form")).toBeInTheDocument();                              // the form stays open
    vi.unstubAllGlobals();
    routeFetch({ "GET /api/guardrails": () => jsonResponse({ detail: "offline" }, { status: 500 }), "GET /api/guardrails/events": () => jsonResponse({ events: [] }), "GET /api/guardrails/stats": () => jsonResponse({}) });
    render(<GuardrailSettings />);
    expect(await screen.findByText("offline")).toBeInTheDocument();
  });

  it("copes with no catches and no top category", async () => {
    setup({}, { "GET /api/guardrails/events": () => jsonResponse({ events: [{ ...EVENT, agent: "", action: "flagged" }] }),
      "GET /api/guardrails/stats": () => jsonResponse({ days: 7, flagged: 0, blocked: 0, errors: 0, top_categories: [] }) });
    render(<GuardrailSettings />);
    expect(await screen.findByText(/Last 7 days: 0 flagged · 0 blocked · 0 checks that could not answer$/)).toBeInTheDocument();
    vi.unstubAllGlobals();
    setup({}, { "GET /api/guardrails/events": () => jsonResponse({ events: [] }) });
    render(<GuardrailSettings />);
    expect(await screen.findByText("Nothing yet.")).toBeInTheDocument();
  });
});
