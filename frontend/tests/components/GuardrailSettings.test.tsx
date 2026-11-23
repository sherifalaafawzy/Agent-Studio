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

const PREP_PULL = { what: "downloading", notice: "Guard model llama-guard3:8b is downloading", key: "k1", model: "llama-guard3:8b", base: "http://127.0.0.1:11434", where: "on this PC", status: "downloading",
  completed: 0, total: 0, percent: null, speed_bps: null, eta_s: null, size_hint_gb: 4.9, licence: "Llama community licence", message: "", error_kind: "" };
const PULL = { ...PREP_PULL, percent: 40, total: 4_900_000_000, completed: 1_960_000_000 };

async function openTry(text = "check this") {
  const [basic] = await screen.findAllByTestId("profile");
  await userEvent.click(within(basic).getByRole("button", { name: "Try it" }));
  await userEvent.type(within(basic).getByLabelText("Text to check"), text);
  return basic;
}

describe("Try it waits for a guard that is not ready yet", () => {
  it("downloads a missing guard model, shows the progress and runs the check when it has arrived", async () => {
    let tests = 0, started = 0;
    setup({}, {
      "POST /api/guardrails/test": () => { if (!started) started = Date.now(); return jsonResponse(++tests === 1 ? { ok: false, error: "Guard model llama-guard3:8b is downloading", preparing: PREP_PULL } : { ok: true, flagged: false, ms: 9 }); },
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: !started ? [] : [Date.now() - started < 120 ? PULL : { ...PULL, status: "done", percent: 100 }] }),
    });
    render(<GuardrailSettings />);
    const basic = await openTry();
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText("Downloading llama-guard3:8b")).toBeInTheDocument();
    expect(within(basic).getByText(/Licence: Llama community licence/)).toBeInTheDocument();
    expect(await within(basic).findByText(/Nothing found - it would be let through · 9 ms/)).toBeInTheDocument();
    expect(tests).toBe(2);
  });

  it("can cancel the download", async () => {
    const net = setup({}, {
      "POST /api/guardrails/test": () => jsonResponse({ ok: false, error: "downloading", preparing: PREP_PULL }),
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [PULL] }),
      "POST /api/guardrails/pulls/cancel": () => jsonResponse({ cancelled: true }),
    });
    render(<GuardrailSettings />);
    const basic = await openTry();
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    await userEvent.click(await within(basic).findByRole("button", { name: "Cancel download" }));
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/guardrails/pulls/cancel")).toBe(true));
    await waitFor(() => expect(within(basic).getByRole("button", { name: "Check it" })).toBeEnabled());
  });

  it("stops and says why when the download failed, without trying again in a loop", async () => {
    let tests = 0;
    setup({}, {
      "POST /api/guardrails/test": () => { tests++; return jsonResponse({ ok: false, error: "downloading", preparing: PREP_PULL }); },
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [{ ...PULL, status: "error", error_kind: "disk", message: "Not enough free disk space." }] }),
    });
    render(<GuardrailSettings />);
    const card = await openTry("x");
    await userEvent.click(within(card).getByRole("button", { name: "Check it" }));
    await waitFor(() => expect(within(card).getAllByRole("alert").some((a) => /Not enough free disk space/.test(a.textContent ?? ""))).toBe(true));
    expect(tests).toBe(1);
  });

  it("treats a download that has disappeared from the list as finished and asks again", async () => {
    let tests = 0;
    setup({}, {
      "POST /api/guardrails/test": () => jsonResponse(++tests === 1 ? { ok: false, error: "d", preparing: PREP_PULL } : { ok: true, flagged: true, category: "S1", reason: "violent crimes", would: "block", ms: 4 }),
      "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [] }),
    });
    render(<GuardrailSettings />);
    const basic = await openTry();
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText(/Flagged \(S1\): violent crimes - would block/)).toBeInTheDocument();
  });

  it("waits for the decision model to load, shows how long the first load takes, and carries on when it is ready", async () => {
    let tests = 0;
    setup({}, {
      "POST /api/guardrails/test": async () => {
        await new Promise((r) => setTimeout(r, 20));
        return jsonResponse(++tests < 3 ? { ok: false, error: "Decision model is loading", preparing: { what: "loading", notice: "Decision model is loading", load_seconds: 41.2, loading_for_s: 25.4 } } : { ok: true, flagged: false, ms: 11 });
      },
    });
    render(<GuardrailSettings />);
    const basic = await openTry();
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    const msg = await within(basic).findByTestId("d1-loading");
    expect(msg).toHaveTextContent("Loading the decision model…");
    expect(msg).toHaveTextContent("The first load takes about 41 s on this PC · 25 s so far");
    expect(await within(basic).findByText(/Nothing found - it would be let through · 11 ms/)).toBeInTheDocument();
    expect(tests).toBe(3);
  });

  it("says a first load can take minutes when it has never been timed, and lets you stop waiting", async () => {
    setup({}, { "POST /api/guardrails/test": async () => { await new Promise((r) => setTimeout(r, 30)); return jsonResponse({ ok: false, error: "loading", preparing: { what: "loading", notice: "Decision model is loading" } }); } });
    render(<GuardrailSettings />);
    const basic = await openTry();
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    const msg = await within(basic).findByTestId("d1-loading");
    expect(msg).toHaveTextContent("The first load can take from several seconds to a few minutes");
    await userEvent.click(within(basic).getByRole("button", { name: "Stop waiting" }));
    expect(await within(basic).findByText(/Stopped waiting. The decision model keeps loading in the background./)).toBeInTheDocument();
  });

  it("points to the Decision model page when it is not installed", async () => {
    setup({}, { "POST /api/guardrails/test": () => jsonResponse({ ok: false, error: "The decision model is not installed yet. Install it under MCP servers > Decision model, then try again.", install: "d1" }) });
    render(<GuardrailSettings />);
    const basic = await openTry();
    await userEvent.click(within(basic).getByRole("button", { name: "Check it" }));
    expect(await within(basic).findByText(/not installed yet/)).toBeInTheDocument();
    expect(within(basic).getByRole("link", { name: "Open the Decision model page" })).toHaveAttribute("href", "/mcp/?server=d1");
  });

  it("shows guard downloads at the top of the card, including ones a chat started", async () => {
    setup({}, { "GET /api/guardrails/pulls": () => jsonResponse({ pulls: [PULL] }) });
    render(<GuardrailSettings />);
    const strip = await screen.findByTestId("guard-downloads");
    expect(within(strip).getByText("Downloading llama-guard3:8b")).toBeInTheDocument();
  });
});
