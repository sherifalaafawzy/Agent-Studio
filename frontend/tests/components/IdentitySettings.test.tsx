import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import IdentitySettings from "@/components/IdentitySettings";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const PROVIDERS = [
  { id: "local", label: "Built-in issuer", summary: "Agent Studio signs tokens itself.", exchange: "rfc8693", fields: [] },
  { id: "okta", label: "Okta", summary: "Okta service app.", exchange: "rfc8693", fields: [{ name: "domain", label: "Okta domain", required: true }, { name: "client_secret", label: "Client secret", type: "secret", required: true }] },
  { id: "entra", label: "Microsoft Entra ID", summary: "Entra app.", exchange: "obo", fields: [{ name: "tenant", label: "Tenant id", required: true }] },
  { id: "spire", label: "SPIFFE / SPIRE", summary: "Ask SPIRE.", exchange: "none", fields: [{ name: "socket_path", label: "Agent socket", required: true }] },
];
const CFG = { trust_domain: "agent-studio.local", issuer: "http://127.0.0.1:8765", ttl_s: 300, enforce: false, active: "local", provider: { kind: "local", params: {}, secrets_set: [] as string[] }, providers: PROVIDERS, scopes: {} };
const AGENTS = [{ id: "engineer", name: "Engineer", spiffe_id: "spiffe://agent-studio.local/agent/engineer", scopes: ["files:read", "shell:run"], scopes_automatic: true, delegates: ["explorer"] },
  { id: "plain", name: "Plain", spiffe_id: "spiffe://agent-studio.local/agent/plain", scopes: [], scopes_automatic: false, delegates: [] }];
const EVENTS = [{ ts: 1_700_000_000, event: "delegated", subject: "s", actor: "a", audience: "agent:x", scopes: "files:read", detail: "engineer -> explorer" },
  { ts: 1_700_000_100, event: "refused", subject: "s", actor: "", audience: "aud", scopes: "", detail: "" }];

function setup(over: Record<string, any> = {}, extra: Record<string, any> = {}) {
  let cfg: any = { ...CFG, ...over };
  return routeFetch({
    "GET /api/identity/config": () => jsonResponse(cfg), "GET /api/identity/agents": () => jsonResponse({ agents: AGENTS }), "GET /api/identity/events": () => jsonResponse({ events: EVENTS }),
    "PUT /api/identity/config": (init: any) => {
      const p = JSON.parse(init.body);
      cfg = { ...cfg, ...p, ...(p.provider ? { active: p.provider.kind, provider: { kind: p.provider.kind, params: p.provider.params, secrets_set: Object.keys(p.provider.secrets ?? {}) } } : {}) };
      return jsonResponse(cfg);
    },
    "POST /api/identity/rotate-key": () => jsonResponse({ ok: true }),
    ...extra,
  });
}
const puts = (calls: Call[]) => calls.filter((c) => c.method === "PUT").map((c) => c.body);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("IdentitySettings", () => {
  it("shows the built-in issuer, every agent's identity and the recent hand-overs", async () => {
    setup();
    render(<IdentitySettings />);
    expect(await screen.findByText("built-in issuer")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "spiffe://agent-studio.local/agent/engineer" })).toBeInTheDocument();
    expect(screen.getByText(/files:read, shell:run \(from its tools\)/)).toBeInTheDocument();
    expect(screen.getByText("nothing")).toBeInTheDocument();
    expect(screen.getByText(/engineer -> explorer · files:read/)).toBeInTheDocument();
    expect(screen.getByText("Public keys (JWKS)")).toHaveAttribute("href", "http://127.0.0.1:8765/api/identity/jwks.json");
    expect(screen.getByRole("button", { name: "Save provider" })).toBeDisabled();                  // nothing to save for the built-in issuer
  });

  it("saves the trust domain, issuer, lifetime and enforcement, and ignores unchanged values", async () => {
    const net = setup();
    render(<IdentitySettings />);
    const td = await screen.findByLabelText("Trust domain");
    fireEvent.blur(td);                                                                          // unchanged: nothing is sent
    const loaded = (n: number) => waitFor(() => expect(net.calls.filter((c) => c.url.pathname === "/api/identity/events").length).toBeGreaterThanOrEqual(n));
    await userEvent.clear(td); await userEvent.type(td, "example.org");
    fireEvent.blur(td);
    await loaded(2);                                                                              // the page reloads after a save
    const iss = screen.getByLabelText("Issuer address");
    fireEvent.blur(iss);
    await userEvent.clear(iss); await userEvent.type(iss, "https://studio.example.org");
    fireEvent.blur(iss);
    await loaded(3);
    const ttl = screen.getByLabelText("Pass lifetime (seconds)");
    fireEvent.change(ttl, { target: { value: "600" } }); fireEvent.blur(ttl);
    await userEvent.click(screen.getByRole("checkbox", { name: "Enforce hand-overs" }));
    await waitFor(() => expect(new Set(puts(net.calls).map((x) => JSON.stringify(x)))).toEqual(new Set([{ trust_domain: "example.org" }, { issuer: "https://studio.example.org" }, { ttl_s: 600 }, { enforce: true }].map((x) => JSON.stringify(x)))));
  });

  it("switches provider, explains what delegation does there, and saves with a write-only secret", async () => {
    const net = setup();
    render(<IdentitySettings />);
    await userEvent.selectOptions(await screen.findByLabelText("Identity provider"), "okta");
    expect(screen.getByText(/Hand-overs use RFC 8693 token exchange/)).toBeInTheDocument();
    expect(screen.getByText(/It identifies Agent Studio to that system/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^Okta domain/), "dev-1.okta.com");
    await userEvent.type(screen.getByLabelText(/^Client secret/), "SECRET-VALUE");
    await userEvent.click(screen.getByRole("button", { name: "Save provider" }));
    await waitFor(() => expect(puts(net.calls)).toEqual([{ provider: { kind: "okta", params: { domain: "dev-1.okta.com" }, secrets: { client_secret: "SECRET-VALUE" } } }]));
    expect(await screen.findByText("Okta", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^Client secret/)).toHaveValue("");                              // never shown again
    expect(screen.getByLabelText(/^Client secret/)).toHaveAttribute("placeholder", "(stored: leave empty to keep)");
    await userEvent.selectOptions(screen.getByLabelText("Identity provider"), "entra");
    expect(screen.getByText(/on-behalf-of grant/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Identity provider"), "spire");
    expect(screen.getByText(/Tokens cannot be exchanged/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Identity provider"), "okta");              // back to the saved one: its stored values return
    expect(screen.getByLabelText(/^Okta domain/)).toHaveValue("dev-1.okta.com");
    await userEvent.selectOptions(screen.getByLabelText("Identity provider"), "local");
    expect(screen.queryByLabelText(/^Okta domain/)).toBeNull();
  });

  it("rotates the signing key only after you confirm", async () => {
    const net = setup();
    render(<IdentitySettings />);
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await userEvent.click(await screen.findByRole("button", { name: "New signing key" }));
    expect(net.calls.some((c) => c.method === "POST")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "New signing key" }));
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname === "/api/identity/rotate-key")).toBe(true));
  });

  it("shows the server's reason when a setting is refused, and when the page cannot load", async () => {
    setup({}, { "PUT /api/identity/config": () => jsonResponse({ detail: "the trust domain must look like example.org" }, { status: 422 }),
      "POST /api/identity/rotate-key": () => jsonResponse({ detail: "no key" }, { status: 500 }) });
    render(<IdentitySettings />);
    const td = await screen.findByLabelText("Trust domain");
    await userEvent.clear(td); await userEvent.type(td, "Bad Domain");
    fireEvent.blur(td);
    expect(await screen.findByText("the trust domain must look like example.org")).toBeInTheDocument();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "New signing key" }));
    expect(await screen.findByText("no key")).toBeInTheDocument();
    vi.unstubAllGlobals();
    routeFetch({ "GET /api/identity/config": () => jsonResponse({ detail: "offline" }, { status: 500 }), "GET /api/identity/agents": () => jsonResponse({ agents: [] }), "GET /api/identity/events": () => jsonResponse({ events: [] }) });
    render(<IdentitySettings />);
    expect(await screen.findByText("offline")).toBeInTheDocument();
  });

  it("says so when nothing has happened yet", async () => {
    setup({}, { "GET /api/identity/events": () => jsonResponse({ events: [] }) });
    render(<IdentitySettings />);
    expect(await screen.findByText("Nothing yet.")).toBeInTheDocument();
  });
});
