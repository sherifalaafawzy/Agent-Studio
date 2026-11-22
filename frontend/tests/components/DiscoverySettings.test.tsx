import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DiscoverySettings, { DiscoveryCfg } from "@/components/DiscoverySettings";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings/", useSearchParams: () => new URLSearchParams(""), useRouter: () => ({ push: () => {} }) }));

const types = [{ id: "agents", label: "Agents (name, description)" }, { id: "bom_system", label: "AI-BOM: the whole system" }];
const levels = [{ id: "public", description: "Safe to share with anyone." }, { id: "internal", description: "For your own organisation." }, { id: "full", description: "Everything plus instructions." }];
const base: DiscoveryCfg = {
  enabled: false, publisher: "", base_url: "", level: "public", level_confirmed: false, sign: false, port: 8766, publish: { agents: false, bom_system: false },
  push_url: "", push_project: "Agent Studio", push_version: "", push_level: "public", types, has_token: false, has_push_key: false, effective_base_url: "",
  not_ready: ["publishing is switched off", "set the publisher domain"], publisher_problem: null,
};
const stopped = { running: false, port: null, local_url: null, error: "" };
const noKey = { exists: false, jwk: null, scheme: "x", note: "" };

function setup(cfg: Partial<DiscoveryCfg> = {}, extra: Record<string, any> = {}, server: any = stopped, keys: any = noKey) {
  const state = { cfg: { ...base, ...cfg } };
  const r = routeFetch({
    "GET /api/discovery/settings": () => jsonResponse(state.cfg),
    "GET /api/aibom/levels": () => jsonResponse(levels),
    "GET /api/discovery/server": () => jsonResponse(server),
    "GET /api/discovery/keys": () => jsonResponse(keys),
    "PUT /api/discovery/settings": (init) => { state.cfg = { ...state.cfg, ...JSON.parse(String(init?.body)) }; return jsonResponse(state.cfg); },
    ...extra,
  });
  return { ...r, state };
}

afterEach(() => { vi.restoreAllMocks(); });

describe("Settings > Discovery & AI-BOM", () => {
  it("starts switched off, explains what it does, and lists the choices", async () => {
    setup();
    render(<DiscoverySettings />);
    expect(await screen.findByText("off")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is published unless you switch it on/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Discovery page" }).getAttribute("href")).toMatch(/^\/discovery\/?$/);
    expect(screen.getByLabelText("Agents (name, description)")).not.toBeChecked();
    expect(screen.getByText("Safe to share with anyone.")).toBeInTheDocument();
    expect(screen.getByText(/no API keys, tokens, passwords, environment values/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Not ready because" })).toHaveTextContent("set the publisher domain");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start server" })).toBeDisabled();
    expect(screen.getByText("Port 8766")).toBeInTheDocument();
  });

  it("shows a load failure instead of crashing", async () => {
    routeFetch({ "GET /api/discovery/settings": () => jsonResponse({ detail: "boom" }, { status: 500 }), "GET /api/aibom/levels": () => jsonResponse([]),
      "GET /api/discovery/server": () => jsonResponse(stopped), "GET /api/discovery/keys": () => jsonResponse(noKey) });
    render(<DiscoverySettings />);
    expect(await screen.findByText(/⚠ boom/)).toBeInTheDocument();
  });

  it("saves edits (and a write-only push key) in one request, then reloads", async () => {
    const { calls } = setup({}, {}, stopped);
    render(<DiscoverySettings />);
    await userEvent.type(await screen.findByLabelText("Publisher domain"), "agents.acme-corp.dev");
    await userEvent.type(screen.getByLabelText("Public address"), "https://agents.acme-corp.dev");
    await userEvent.click(screen.getByLabelText("Publishing on"));
    await userEvent.click(screen.getByLabelText("AI-BOM: the whole system"));
    await userEvent.selectOptions(screen.getByLabelText("Push redaction level"), "internal");
    await userEvent.type(screen.getByLabelText("Dependency-Track address"), "https://dt.acme.dev");
    await userEvent.type(screen.getByLabelText("Dependency-Track API key"), "odt_secret");
    await userEvent.clear(screen.getByLabelText("Project name"));
    await userEvent.type(screen.getByLabelText("Project name"), "Proj");
    await userEvent.type(screen.getByLabelText("Project version"), "2.0");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toMatchObject({ publisher: "agents.acme-corp.dev", base_url: "https://agents.acme-corp.dev", enabled: true, publish: { agents: false, bom_system: true },
      push_level: "internal", push_url: "https://dt.acme.dev", push_api_key: "odt_secret", push_project: "Proj", push_version: "2.0" });
    expect(screen.getByLabelText("Dependency-Track API key")).toHaveValue("");              // the key is never kept in the form
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("changing the level asks for confirmation; signing can be switched on", async () => {
    const { calls } = setup();
    render(<DiscoverySettings />);
    await userEvent.selectOptions(await screen.findByLabelText("Redaction level"), "full");
    expect(screen.getByText("Everything plus instructions.")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/I understand the 'full' level/));
    await userEvent.click(screen.getByLabelText("Sign each catalog entry (detached JWS, EdDSA)"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ level: "full", level_confirmed: true, sign: true });
  });

  it("reports a rejected save", async () => {
    setup({}, { "PUT /api/discovery/settings": () => jsonResponse({ detail: "that domain is reserved" }, { status: 422 }) });
    render(<DiscoverySettings />);
    await userEvent.type(await screen.findByLabelText("Publisher domain"), "example.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/⚠ Save: that domain is reserved/)).toBeInTheDocument();
  });

  it("shows the publisher problem, the ready state and the stored push key hint", async () => {
    setup({ enabled: true, publisher: "bad_domain", publisher_problem: "must be a fully qualified domain name", not_ready: [], has_push_key: true, push_url: "https://dt.acme.dev" });
    render(<DiscoverySettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("fully qualified");
    expect(screen.getByText("on")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("set (not shown)")).toBeInTheDocument();
    expect(screen.getByText("configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start server" })).toBeEnabled();
  });

  it("says when publishing is on but not ready", async () => {
    setup({ enabled: true, not_ready: ["choose at least one kind of resource to publish"] });
    render(<DiscoverySettings />);
    expect(await screen.findByText("on, not ready")).toBeInTheDocument();
  });

  it("non-public levels need confirmation and an access token that is shown once", async () => {
    const writeText = vi.fn();
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const s = setup({ level: "internal" }, {
      "POST /api/discovery/token": () => { s.state.cfg = { ...s.state.cfg, has_token: true }; return jsonResponse({ token: "asd_TOKEN123" }); },
      "DELETE /api/discovery/token": () => { s.state.cfg = { ...s.state.cfg, has_token: false }; return jsonResponse({ ok: true }); },
    });
    render(<DiscoverySettings />);
    expect(await screen.findByText("For your own organisation.")).toBeInTheDocument();
    expect(screen.getByText("No token yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/I understand the 'internal' level/));
    await userEvent.click(screen.getByRole("button", { name: "Generate access token" }));
    expect(await screen.findByText("asd_TOKEN123")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replace access token" })).toBeInTheDocument();
    expect(screen.getByText(/A token is set \(only its hash is kept\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("asd_TOKEN123");
    await userEvent.click(screen.getByRole("button", { name: "Remove token" }));
    await waitFor(() => expect(screen.queryByText("asd_TOKEN123")).not.toBeInTheDocument());
    expect(screen.getByText("No token yet.")).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("hides the confirmation and token controls at the public level", async () => {
    setup({ level: "public" });
    render(<DiscoverySettings />);
    await screen.findByLabelText("Redaction level");
    expect(screen.queryByText(/I understand the/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /access token/ })).not.toBeInTheDocument();
  });

  it("manages the signing key and says what a signature means", async () => {
    let exists = false;
    setup({ sign: true }, {
      "GET /api/discovery/keys": () => jsonResponse(exists ? { exists: true, jwk: { kid: "KID-1" }, scheme: "x", note: "" } : noKey),
      "POST /api/discovery/keys": () => { exists = true; return jsonResponse({ exists: true }); },
      "DELETE /api/discovery/keys": () => { exists = false; return jsonResponse({ exists: false }); },
    });
    render(<DiscoverySettings />);
    expect(await screen.findByText(/ARD defines no signing or verification procedure/)).toBeInTheDocument();
    expect(screen.getByText("No signing key yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Generate key" }));
    expect(await screen.findByText(/Key id KID-1/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rotate key" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete key" }));
    expect(await screen.findByText("No signing key yet.")).toBeInTheDocument();
  });

  it("starts and stops the discovery server and shows its loopback address", async () => {
    let running = false;
    setup({ enabled: true, not_ready: [] }, {
      "GET /api/discovery/server": () => jsonResponse(running ? { running: true, port: 8766, local_url: "http://127.0.0.1:8766/", error: "" } : stopped),
      "POST /api/discovery/server/start": () => { running = true; return jsonResponse({}); },
      "POST /api/discovery/server/stop": () => { running = false; return jsonResponse({}); },
    });
    const writeText = vi.fn();
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<DiscoverySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Start server" }));
    expect(await screen.findByText("http://127.0.0.1:8766/")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("your own domain or tunnel")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Copy" })[0]);
    expect(writeText).toHaveBeenCalledWith("http://127.0.0.1:8766/");
    await userEvent.click(screen.getByRole("button", { name: "Stop server" }));
    expect(await screen.findByRole("button", { name: "Start server" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("shows a server error and tolerates a missing clipboard", async () => {
    setup({ enabled: true, not_ready: [] }, {}, { running: true, port: 1, local_url: "http://127.0.0.1:1/", error: "earlier failure" });
    vi.stubGlobal("navigator", {});
    render(<DiscoverySettings />);
    expect(await screen.findByText("earlier failure")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    vi.unstubAllGlobals();
  });
});

describe("start failures", () => {
  it("are shown next to the button", async () => {
    setup({ enabled: true, not_ready: [] }, { "POST /api/discovery/server/start": () => jsonResponse({ detail: "could not start: is the port already in use?" }, { status: 409 }) });
    render(<DiscoverySettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Start server" }));
    expect(await screen.findByText(/⚠ Server: could not start/)).toBeInTheDocument();
  });
});
