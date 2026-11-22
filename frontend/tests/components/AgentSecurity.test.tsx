import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentSecurity, { type Identity } from "@/components/AgentSecurity";
import { jsonResponse, routeFetch } from "../helpers/http";

const PROFILES = [{ id: "basic", name: "Basic rules", mode: "monitor" }, { id: "strict", name: "Strict", mode: "block" }];
const IDCFG = { trust_domain: "agent-studio.local", scopes: { "files:read": "read files", "shell:run": "run commands" } };

function Host({ isNew = false, start = { guardrails: [] as string[], identity: {} as Identity } }) {
  const [g, setG] = useState(start.guardrails);
  const [i, setI] = useState(start.identity);
  return <div><AgentSecurity agentId={isNew ? "" : "engineer"} isNew={isNew} guardrails={g} identity={i} others={[{ id: "explorer", name: "Explorer" }]} onChange={(a, b) => { setG(a); setI(b); }} />
    <pre data-testid="out">{JSON.stringify({ g, i })}</pre></div>;
}
const out = () => JSON.parse(screen.getByTestId("out").textContent!);
function setup(extra: Record<string, any> = {}) {
  return routeFetch({ "GET /api/guardrails": () => jsonResponse({ profiles: PROFILES }), "GET /api/identity/config": () => jsonResponse(IDCFG),
    "POST /api/identity/agents/engineer/token": () => jsonResponse({ source: "local", claims: { scope: "files:read" }, fingerprint: "abc123", expires_in: 300 }), ...extra });
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("AgentSecurity", () => {
  it("defaults to the default guardrail profiles and shows the agent's identity", async () => {
    setup();
    render(<Host />);
    expect(await screen.findByText("spiffe://agent-studio.local/agent/engineer")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Default profiles" })).toBeChecked();
    expect(screen.queryByText("Strict")).toBeNull();
    expect(screen.getByRole("checkbox", { name: /work it out from its tools/ })).toBeChecked();
  });

  it("switches an agent between default, no guardrails and chosen profiles", async () => {
    setup();
    render(<Host />);
    await screen.findByText("spiffe://agent-studio.local/agent/engineer");
    await userEvent.click(screen.getByRole("radio", { name: /None/ }));
    expect(out().g).toEqual(["none"]);
    await userEvent.click(screen.getByRole("radio", { name: "Choose profiles" }));
    expect(out().g).toEqual(["basic"]);                                                          // starts with one profile ticked
    await userEvent.click(screen.getByRole("checkbox", { name: /Strict/ }));
    expect(out().g).toEqual(["basic", "strict"]);
    await userEvent.click(screen.getByRole("checkbox", { name: /Basic rules/ }));
    expect(out().g).toEqual(["strict"]);
    await userEvent.click(screen.getByRole("radio", { name: "Default profiles" }));
    expect(out().g).toEqual([]);
  });

  it("lets you choose scopes and who the agent may hand work to, or go back to automatic", async () => {
    setup();
    render(<Host start={{ guardrails: ["basic"], identity: {} }} />);
    await screen.findByText("spiffe://agent-studio.local/agent/engineer");
    await userEvent.click(screen.getByRole("checkbox", { name: /work it out from its tools/ }));
    expect(out().i).toEqual({ scopes: [] });
    await userEvent.click(screen.getByRole("checkbox", { name: "files:read" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "shell:run" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "files:read" }));
    expect(out().i).toEqual({ scopes: ["shell:run"] });
    await userEvent.click(screen.getByRole("checkbox", { name: /its sub-agents/ }));
    expect(out().i).toEqual({ scopes: ["shell:run"], delegates: [] });
    await userEvent.click(screen.getByRole("checkbox", { name: "Explorer" }));
    expect(out().i.delegates).toEqual(["explorer"]);
    await userEvent.click(screen.getByRole("checkbox", { name: "Explorer" }));
    expect(out().i.delegates).toEqual([]);
    await userEvent.click(screen.getByRole("checkbox", { name: /its sub-agents/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /work it out from its tools/ }));
    expect(out()).toEqual({ g: ["basic"], i: {} });
  });

  it("tries a pass for a saved agent and shows what it says (never the token)", async () => {
    const net = setup();
    render(<Host />);
    const aud = await screen.findByLabelText("Audience");
    await userEvent.clear(aud); await userEvent.type(aud, "https://mcp.example.com");
    await userEvent.click(screen.getByRole("button", { name: "Try a pass" }));
    expect(await screen.findByText("pass abc123 · files:read · 300s")).toBeInTheDocument();
    expect(net.calls.find((c) => c.url.pathname.endsWith("/token"))?.body).toEqual({ audience: "https://mcp.example.com" });
  });

  it("shows the reason a pass was refused, a pass with no scopes, and hides the button for a new agent", async () => {
    setup({ "POST /api/identity/agents/engineer/token": () => jsonResponse({ detail: "Engineer may not hold: shell:run" }, { status: 422 }) });
    const { unmount } = render(<Host />);
    await userEvent.click(await screen.findByRole("button", { name: "Try a pass" }));
    expect(await screen.findByText("Engineer may not hold: shell:run")).toBeInTheDocument();
    unmount();
    vi.unstubAllGlobals();
    setup({ "POST /api/identity/agents/engineer/token": () => jsonResponse({ source: "local", claims: null, fingerprint: "zzz", expires_in: 60 }) });
    const second = render(<Host />);
    await userEvent.click(await screen.findByRole("button", { name: "Try a pass" }));
    expect(await screen.findByText("pass zzz · no scopes · 60s")).toBeInTheDocument();
    second.unmount();
    render(<Host isNew />);
    expect(await screen.findByText("spiffe://agent-studio.local/agent/(new agent)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try a pass" })).toBeNull();
  });

  it("still renders when the lists cannot be loaded", async () => {
    routeFetch({ "GET /api/guardrails": () => jsonResponse({}, { status: 500 }), "GET /api/identity/config": () => jsonResponse({}, { status: 500 }) });
    render(<Host start={{ guardrails: [], identity: { scopes: [] } }} />);
    await waitFor(() => expect(screen.getByText("…")).toBeInTheDocument());
    expect(screen.getByRole("radio", { name: "Default profiles" })).toBeInTheDocument();
  });
});
