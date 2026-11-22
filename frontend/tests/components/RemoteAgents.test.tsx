import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { A2AConnect, AgentCreateChoice, CardInfo, RemotePanel, type CardSummary, type Remote } from "@/components/RemoteAgents";
import { jsonResponse, routeFetch } from "../helpers/http";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const CARD: CardSummary = { name: "Travel Agent", description: "Plans trips.", version: "1.2.0", provider: "Acme", streaming: true,
  skills: [{ id: "plan", name: "Plan a trip", description: "Flights and hotels" }, { id: "visa", name: "" }], auth: ["bearer token"], interfaces: ["https://x/a2a/"], input_modes: ["text/plain"], output_modes: ["text/plain"] };
const R: Remote = { id: "travel", label: "Travel", url: "https://agents.example.com/travel/", enabled: true, name: "Travel Agent", description: "Plans trips.", auth_kind: "bearer", auth_header: "",
  delegates: ["engineer"], network_scope: "internet", scope_label: "data leaves this PC", card: CARD, has_token: true };
const AGENTS = [{ id: "engineer", name: "Engineer" }, { id: "writer", name: "Writer" }];

describe("AgentCreateChoice", () => {
  it("offers the wizard or connecting an A2A agent", async () => {
    const w = vi.fn(), a = vi.fn();
    render(<AgentCreateChoice onWizard={w} onA2A={a} />);
    await userEvent.click(screen.getByRole("button", { name: /New agent \(wizard\)/ }));
    await userEvent.click(screen.getByRole("button", { name: /Connect an A2A agent/ }));
    expect(w).toHaveBeenCalledTimes(1);
    expect(a).toHaveBeenCalledTimes(1);
  });
});

describe("CardInfo", () => {
  it("shows what the agent says about itself", () => {
    render(<CardInfo card={CARD} />);
    expect(screen.getByText("Travel Agent")).toBeInTheDocument();
    expect(screen.getByText(/version 1.2.0/)).toBeInTheDocument();
    expect(screen.getByText(/by Acme/)).toBeInTheDocument();
    expect(screen.getByText("Plans trips.")).toBeInTheDocument();
    expect(screen.getByText("streams answers")).toBeInTheDocument();
    expect(screen.getByText("bearer token")).toBeInTheDocument();
    expect(screen.getByText("Plan a trip")).toBeInTheDocument();
    expect(screen.getByText("visa")).toBeInTheDocument();                                // a skill without a name falls back to its id
  });

  it("copes with a sparse card", () => {
    render(<CardInfo card={{ skills: [], auth: [], interfaces: [], input_modes: [], output_modes: [] }} />);
    expect(screen.getByText("(unnamed agent)")).toBeInTheDocument();
    expect(screen.getByText("answers in one piece")).toBeInTheDocument();
    expect(screen.getByText("no sign-in declared")).toBeInTheDocument();
    expect(screen.getByText("The agent does not list any skills.")).toBeInTheDocument();
  });
});

describe("A2AConnect", () => {
  it("reads the card, then connects with the name you chose", async () => {
    const net = routeFetch({
      "POST /api/access/remotes/preview": () => jsonResponse({ ok: true, base_url: "https://agents.example.com/travel/", card: CARD, scope: "internet" }),
      "POST /api/access/remotes/connect": () => jsonResponse(R),
    });
    const done = vi.fn();
    render(<A2AConnect onConnected={done} onCancel={() => {}} />);
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Agent address/), "https://agents.example.com/travel/");
    await userEvent.type(screen.getByLabelText(/^Token/), "tok-1");
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    const pv = await screen.findByTestId("card-preview");
    expect(within(pv).getByText("Remote A2A")).toBeInTheDocument();
    expect(within(pv).getByTestId("scope-badge")).toHaveTextContent("data leaves this PC");
    expect(net.calls[0].body).toEqual({ url: "https://agents.example.com/travel/", auth_kind: "bearer", token: "tok-1", auth_header: "" });
    const name = screen.getByLabelText(/^Name in your list/);
    expect(name).toHaveValue("Travel Agent");                                          // prefilled from the card
    await userEvent.clear(name);
    await userEvent.type(name, "My travel");
    expect(screen.getByRole("button", { name: "Connect" })).toBeEnabled();                // renaming does not throw the preview away
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("travel"));
    expect(net.calls[1].body).toMatchObject({ label: "My travel", url: "https://agents.example.com/travel/" });
  });

  it("keeps a name you typed before checking and asks again when the address changes", async () => {
    routeFetch({ "POST /api/access/remotes/preview": () => jsonResponse({ ok: true, card: { ...CARD, name: undefined }, scope: "network" }) });
    render(<A2AConnect onConnected={() => {}} onCancel={() => {}} />);
    await userEvent.type(screen.getByLabelText(/^Agent address/), "http://10.0.0.4/a");
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    const name = await screen.findByLabelText(/^Name in your list/);
    expect(name).toHaveValue("");                                                            // a card without a name leaves it blank
    await userEvent.type(screen.getByLabelText(/^Agent address/), "x");
    expect(screen.queryByTestId("card-preview")).toBeNull();
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  });

  it("uses a header name for API-key sign-in", async () => {
    const net = routeFetch({ "POST /api/access/remotes/preview": () => jsonResponse({ ok: true, card: CARD, scope: "internet" }) });
    render(<A2AConnect onConnected={() => {}} onCancel={() => {}} />);
    await userEvent.selectOptions(screen.getByLabelText(/^Sign-in/), "header");
    await userEvent.type(screen.getByLabelText(/^Header name/), "X-API-Key");
    await userEvent.type(screen.getByLabelText(/^API key/), "k1");
    await userEvent.type(screen.getByLabelText(/^Agent address/), "https://a.example.com");
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    await screen.findByTestId("card-preview");
    expect(net.calls[0].body).toMatchObject({ auth_kind: "header", auth_header: "X-API-Key", token: "k1" });
  });

  it("says why the card could not be read, and why connecting failed", async () => {
    let n = 0;
    routeFetch({
      "POST /api/access/remotes/preview": () => (++n === 1 ? jsonResponse({ ok: false, kind: "auth", title: "The agent refused the credentials", detail: "HTTP 401" })
        : n === 2 ? jsonResponse({ detail: "the address must start with http://" }, { status: 422 }) : jsonResponse({ ok: true, card: CARD, scope: "internet" })),
      "POST /api/access/remotes/connect": () => jsonResponse({ detail: "a remote agent 'travel_agent' already exists" }, { status: 422 }),
    });
    const cancel = vi.fn();
    render(<A2AConnect onConnected={() => {}} onCancel={cancel} />);
    await userEvent.type(screen.getByLabelText(/^Agent address/), "https://a.example.com");
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    expect(await screen.findByText(/The agent refused the credentials: HTTP 401/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    expect(await screen.findByText(/must start with http/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Check the agent" }));
    await userEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(cancel).toHaveBeenCalled();
  });
});

describe("RemotePanel", () => {
  const setup = (over: Record<string, any> = {}) => routeFetch({
    "PUT /api/access/remotes/travel": (init: any) => (over.putFail ? jsonResponse({ detail: "the address must start with http://" }, { status: 422 }) : jsonResponse({ ...R, ...JSON.parse(init.body) })),
    "POST /api/access/remotes/travel/refresh": () => jsonResponse(over.refreshFail ? { ok: false, title: "Cannot reach it", detail: "Nothing answered." } : { ok: true, card: CARD }),
    "POST /api/access/remotes/travel/ping": () => jsonResponse(over.pingFail ? { ok: false, title: "Refused", detail: "HTTP 401" } : { ok: true, title: "Answered", detail: "in 120 ms", reply: "Hello!", ms: 120 }),
    "PUT /api/access/remotes/travel/delegates": () => jsonResponse(R),
    "DELETE /api/access/remotes/travel": () => jsonResponse({ ok: true }),
  });
  const mount = (r: Remote = R, handlers: Partial<{ changed: () => void; deleted: () => void }> = {}) =>
    render(<RemotePanel r={r} agents={AGENTS} onChanged={handlers.changed ?? (() => {})} onDeleted={handlers.deleted ?? (() => {})} />);

  it("shows it as a remote agent that runs elsewhere, with its card", () => {
    setup();
    mount();
    const p = screen.getByTestId("remote-panel");
    expect(within(p).getByText("Remote A2A")).toBeInTheDocument();
    expect(within(p).getByTestId("scope-badge")).toHaveTextContent("data leaves this PC");
    expect(within(p).getByText(/outside Agent Studio's spend caps/)).toBeInTheDocument();
    expect(within(p).getByText("Plan a trip")).toBeInTheDocument();
  });

  it("asks you to read the card when none is stored", () => {
    setup();
    mount({ ...R, card: null });
    expect(screen.getByText(/No agent card stored yet/)).toBeInTheDocument();
  });

  it("turns it off and on", async () => {
    const net = setup();
    const changed = vi.fn();
    mount(R, { changed });
    await userEvent.click(screen.getByLabelText("enabled"));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(net.calls[0].body).toEqual({ enabled: false });
  });

  it("turns an agent on again", async () => {
    const net = setup();
    mount({ ...R, enabled: false });
    expect(screen.getByRole("button", { name: "Send test message" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("enabled"));
    await waitFor(() => expect(net.calls).toHaveLength(1));
    expect(net.calls[0].body).toEqual({ enabled: true });
  });

  it("refreshes the card, and says when it cannot", async () => {
    setup();
    const changed = vi.fn();
    const { unmount } = mount(R, { changed });
    await userEvent.click(screen.getByRole("button", { name: "Refresh card" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    unmount();
    setup({ refreshFail: true });
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Refresh card" }));
    expect(await screen.findByText(/Cannot reach it: Nothing answered/)).toBeInTheDocument();
  });

  it("sends a test message and shows the answer or the failure", async () => {
    const net = setup();
    const { unmount } = mount();
    await userEvent.clear(screen.getByLabelText("Test message"));
    await userEvent.type(screen.getByLabelText("Test message"), "Ping");
    await userEvent.click(screen.getByRole("button", { name: "Send test message" }));
    const res = await screen.findByTestId("ping-result");
    expect(res).toHaveTextContent("answered");
    expect(res).toHaveTextContent("“Hello!”");
    expect(net.calls[0].body).toEqual({ message: "Ping" });
    unmount();
    setup({ pingFail: true });
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Send test message" }));
    const bad = await screen.findByTestId("ping-result");
    expect(bad).toHaveTextContent("failed");
    expect(bad).not.toHaveTextContent("“");
  });

  it("chooses which of your agents may call it", async () => {
    const net = setup();
    mount();
    const save = screen.getAllByRole("button", { name: "Save" })[0];
    expect(save).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Engineer" })).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Writer" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Engineer" }));
    await userEvent.click(save);
    await waitFor(() => expect(net.calls.some((c) => c.url.pathname.endsWith("/delegates"))).toBe(true));
    expect(net.calls.find((c) => c.url.pathname.endsWith("/delegates"))!.body).toEqual({ agents: ["writer"] });
  });

  it("edits the connection and can clear the stored token", async () => {
    const net = setup();
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("button", { name: "Save connection" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText(/^Name in your list/));
    await userEvent.type(screen.getByLabelText(/^Name in your list/), "Trips");
    await userEvent.type(screen.getByLabelText(/^Agent address/), "x");
    await userEvent.selectOptions(screen.getByLabelText(/^Sign-in/), "header");
    await userEvent.type(screen.getByLabelText(/^Header name/), "X-Key");
    await userEvent.type(screen.getByLabelText(/^API key/), "new-key");
    await userEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(net.calls).toHaveLength(1));
    expect(net.calls[0].body).toEqual({ label: "Trips", url: "https://agents.example.com/travel/x", auth_kind: "header", auth_header: "X-Key", token: "new-key" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save connection" })).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear stored token" }));
    await waitFor(() => expect(net.calls).toHaveLength(2));
    expect(net.calls[1].body).toEqual({ token: "" });
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));              // reopen, then close again without saving
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("button", { name: "Save connection" })).toBeNull();
  });

  it("shows no clear-token button when no token is stored, and reports a refused save", async () => {
    setup({ putFail: true });
    mount({ ...R, has_token: false });
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.queryByRole("button", { name: "Clear stored token" })).toBeNull();
    await userEvent.type(screen.getByLabelText(/^Agent address/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save connection" }));
    expect(await screen.findByText(/must start with http/)).toBeInTheDocument();
  });

  it("removes it only after confirming", async () => {
    const net = setup();
    const deleted = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    mount(R, { deleted });
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(net.calls).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(deleted).toHaveBeenCalled());
    expect(net.calls[0].method).toBe("DELETE");
  });
});
