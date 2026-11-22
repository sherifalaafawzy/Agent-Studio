import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentRuntime from "@/components/AgentRuntime";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const TARGETS = [
  { id: "local", label: "This PC", enabled: true, configured: true, remote: false, cost_note: "" },
  { id: "agentcore", label: "Amazon Bedrock AgentCore Runtime", enabled: false, configured: false, remote: true, cost_note: "Bills while sessions run." },
  { id: "docker", label: "Docker container on this PC", enabled: true, configured: true, remote: false, cost_note: "Runs on this PC." },
];
const KINDS = [{ id: "docker-container", label: "Docker container", available: true, detail: "ready", experimental: false, notice: "" },
  { id: "docker-microvm", label: "Docker Sandbox (micro VM)", available: false, detail: "switched off (experimental)", experimental: true, notice: "Experimental. Proprietary Docker software." }];
const SOURCES = [{ id: "crm", label: "CRM", kind: "postgres", enabled: true }, { id: "dwh", label: "Warehouse", kind: "snowflake", enabled: false }];
const PLAN = { target: "docker", compat: [{ level: "block", feature: "external agents", text: "Coding agents cannot run there." }, { level: "warn", feature: "tools", text: "Tools do not exist in a runtime." }, { level: "info", feature: "skills", text: "Skills are not included." }],
  blocked: false, diff: [], shadow: false, image: "agent-studio-rt-bot:abc", image_ok: true, commands: ["docker build -t agent-studio-rt-bot:abc ."], bundle: { folder: "C:\\data\\bundles\\bot", files: ["Dockerfile", "agent.json"] },
  cost_note: "Runs on this PC.", side_effects: ["Starts a container."], ready: true, problem: "", info: { label: "Docker container on this PC", platform: "linux/amd64" } };
const rel = (o: Record<string, any>) => ({ id: "r1", agent_id: "bot", target: "docker", version: 1, state: "ready", live: true, shadow: false, image: "img", prev_id: null, detail: "answering /ping", health: {}, ...o });
const MIG = { runtime: "docker", live: rel({}), stale: false, changes: [], compat: [], candidate: null, message: "The live deployment matches this agent." };

function Harness({ initial, isNew = false }: { initial: any; isNew?: boolean }) {
  const [a, setA] = useState(initial);
  return <><AgentRuntime agent={a} isNew={isNew} onChange={(p) => setA({ ...a, ...p })} /><pre data-testid="state">{JSON.stringify(a)}</pre></>;
}
const state = () => JSON.parse(screen.getByTestId("state").textContent!);

function setup(over: Record<string, any> = {}) {
  let rels: any[] = over.rels ?? [];
  let mig: any = over.mig ?? { ...MIG, live: null, message: "no deployment yet" };
  const net = routeFetch({
    "GET /api/runtimes": () => jsonResponse({ targets: TARGETS }),
    "GET /api/sandboxes": () => jsonResponse({ kinds: KINDS }),
    "GET /api/data/sources": () => jsonResponse({ sources: over.sources ?? SOURCES }),
    "GET /api/runtimes/agents/bot": () => jsonResponse({ releases: rels, migration: mig }),
    "POST /api/runtimes/agents/bot/plan": () => over.planError ? jsonResponse({ detail: "choose a model provider with a key for this agent first" }, { status: 422 }) : jsonResponse({ ...PLAN, ...(over.plan ?? {}) }),
    "POST /api/runtimes/agents/bot/deploy": () => over.deployError ? jsonResponse({ detail: "quota exceeded" }, { status: 422 }) : (rels = [rel({ id: "r2", state: "deploying", live: false })], jsonResponse(rels[0])),
    "POST /api/runtimes/releases/r1/refresh": () => jsonResponse(rels[0]),
    "POST /api/runtimes/releases/r1/health": () => jsonResponse({ ok: true, detail: "answered: ok" }),
    "POST /api/runtimes/releases/r2/promote": () => jsonResponse(rel({ id: "r2", live: true })),
    "DELETE /api/runtimes/releases/r2": () => jsonResponse({ ok: true }),
    "POST /api/runtimes/agents/bot/rollback": () => jsonResponse(rel({})),
  });
  return { net, setRels: (r: any[]) => { rels = r; }, setMig: (m: any) => { mig = m; } };
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const mutating = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

describe("AgentRuntime: where it runs, sandbox and data", () => {
  it("lists the runtimes, marks the ones switched off and explains the cost", async () => {
    setup();
    render(<Harness initial={{ id: "bot", runtime: "local", sandbox: "", data: [] }} />);
    await screen.findByRole("option", { name: /Amazon Bedrock AgentCore Runtime \(switched off\)/ });
    expect(screen.getByRole("option", { name: "Docker container on this PC" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Where it runs" }), "agentcore");
    expect(state().runtime).toBe("agentcore");
    expect(screen.getByText(/Bills while sessions run\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings > Runtimes/ })).toHaveAttribute("href", "/settings/#runtimes");
    expect(await screen.findByTestId("deploy-panel")).toBeInTheDocument();
  });

  it("tells a brand new agent to save before deploying", async () => {
    setup();
    render(<Harness initial={{ id: "", runtime: "docker" }} isNew />);
    expect(await screen.findByText("Save the agent first, then deploy it from here.")).toBeInTheDocument();
    expect(screen.queryByTestId("deploy-panel")).toBeNull();
  });

  it("picks a sandbox kind, warns when it is not ready and shows its notice", async () => {
    setup();
    render(<Harness initial={{ id: "bot" }} />);
    await screen.findByRole("option", { name: /micro VM\) \(experimental\)/ });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sandbox for its commands" }), "docker-microvm");
    expect(state().sandbox).toBe("docker-microvm");
    expect(screen.getByRole("alert")).toHaveTextContent("switched off (experimental)");
    expect(screen.getByText(/Proprietary Docker software/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sandbox for its commands" }), "");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ticks the data sources an agent may read, or all of them", async () => {
    setup();
    render(<Harness initial={{ id: "bot", data: [] }} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: /CRM/ }));
    expect(state().data).toEqual(["crm"]);
    expect(screen.getByText("(snowflake, off)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /CRM/ }));
    expect(state().data).toEqual([]);
    await userEvent.click(screen.getByRole("checkbox", { name: "All" }));
    expect(state().data).toEqual(["*"]);
    expect(screen.getByRole("checkbox", { name: /CRM/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "All" }));
    expect(state().data).toEqual([]);
  });

  it("points to Settings when there are no data sources yet, and survives failing lookups", async () => {
    setup({ sources: [] });
    render(<Harness initial={{ id: "bot" }} />);
    expect(await screen.findByText(/None set up yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings > Data sources/ })).toHaveAttribute("href", "/settings/#data");
    routeFetch({});                                                                              // every lookup fails
    render(<Harness initial={{ id: "bot" }} />);
    expect(await screen.findAllByRole("option", { name: "This PC" })).not.toHaveLength(0);
  });
});

describe("AgentRuntime: deploying", () => {
  it("shows the plan with what is lost, the commands and a confirmation, then deploys", async () => {
    const { net } = setup();
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    expect(await screen.findByTestId("migration-message")).toHaveTextContent("no deployment yet");
    await userEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    const plan = await screen.findByTestId("plan");
    expect(within(plan).getByText(/The first version goes live by itself/)).toBeInTheDocument();
    expect(within(plan).getByText(/Blocks deploy:/)).toBeInTheDocument();
    expect(within(plan).getByText("Tools do not exist in a runtime.")).toBeInTheDocument();
    expect(within(plan).getByText("Skills are not included.")).toBeInTheDocument();
    expect(within(plan).getByText(/docker build -t agent-studio-rt-bot:abc/)).toBeInTheDocument();
    expect(within(plan).getByText("Starts a container.")).toBeInTheDocument();
    expect((within(plan).getByLabelText("Image to deploy") as HTMLInputElement).value).toBe("agent-studio-rt-bot:abc");
    const deploy = within(plan).getByRole("button", { name: "Deploy" });
    expect(deploy).toBeDisabled();
    await userEvent.click(within(plan).getByRole("checkbox"));
    expect(deploy).toBeEnabled();
    await userEvent.click(deploy);
    await waitFor(() => expect(mutating(net.calls).some((c) => c.url.pathname.endsWith("/deploy"))).toBe(true));
    expect(mutating(net.calls).find((c) => c.url.pathname.endsWith("/deploy"))!.body).toEqual({ target: "docker", image: "agent-studio-rt-bot:abc", confirm: true });
    await waitFor(() => expect(screen.queryByTestId("plan")).toBeNull());
  });

  it("deploys the image the user typed instead of the suggestion", async () => {
    const { net } = setup();
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    await userEvent.click(await screen.findByRole("button", { name: "Review deployment" }));
    const plan = await screen.findByTestId("plan");
    await userEvent.clear(within(plan).getByLabelText("Image to deploy"));
    await userEvent.type(within(plan).getByLabelText("Image to deploy"), "my.registry/agents:bot-1");
    await userEvent.click(within(plan).getByRole("checkbox"));
    await userEvent.click(within(plan).getByRole("button", { name: "Deploy" }));
    await waitFor(() => expect(mutating(net.calls).some((c) => c.url.pathname.endsWith("/deploy"))).toBe(true));
    expect(mutating(net.calls).find((c) => c.url.pathname.endsWith("/deploy"))!.body.image).toBe("my.registry/agents:bot-1");
  });

  it("still shows the panel when the deployment state cannot be read", async () => {
    routeFetch({ "GET /api/runtimes": () => jsonResponse({ targets: TARGETS }), "GET /api/sandboxes": () => jsonResponse({ kinds: KINDS }), "GET /api/data/sources": () => jsonResponse({ sources: [] }),
      "GET /api/runtimes/agents/bot": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    expect(await screen.findByRole("button", { name: "Review deployment" })).toBeInTheDocument();
    expect(screen.queryByTestId("migration-message")).toBeNull();
  });

  it("will not deploy when the plan is blocked or the platform is not ready, and shows why", async () => {
    setup({ plan: { blocked: true, ready: false, problem: "Docker is switched off (Settings > Runtimes)", commands: [], shadow: true, diff: [{ field: "instruction", before: "a", after: "b" }] } });
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    await userEvent.click(await screen.findByRole("button", { name: "Review deployment" }));
    const plan = await screen.findByTestId("plan");
    expect(within(plan).getByText(/deployed next to it as a shadow version/)).toBeInTheDocument();
    expect(within(plan).getByText("Changed from the live version: instruction")).toBeInTheDocument();
    expect(within(plan).getByText(/Set an image address template/)).toBeInTheDocument();
    expect(within(plan).getByRole("alert")).toHaveTextContent("Docker is switched off");
    await userEvent.click(within(plan).getByRole("checkbox"));
    expect(within(plan).getByRole("button", { name: "Deploy" })).toBeDisabled();
  });

  it("shows a plan error and a deploy error as text", async () => {
    setup({ planError: true });
    const { unmount } = render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    await userEvent.click(await screen.findByRole("button", { name: "Review deployment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("choose a model provider");
    unmount();
    setup({ deployError: true });
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    await userEvent.click(await screen.findByRole("button", { name: "Review deployment" }));
    await userEvent.click(within(await screen.findByTestId("plan")).getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Deploy" }));
    expect(await screen.findByText("quota exceeded")).toBeInTheDocument();
    expect(screen.getByTestId("plan")).toBeInTheDocument();                               // the plan stays so it can be retried
  });

  it("walks a live and a shadow version: check, test message, switch, roll back, delete", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const live = rel({ id: "r1", version: 1, live: true, prev_id: "r0" });
    const shadow = rel({ id: "r2", version: 2, live: false, shadow: true, state: "ready", health: { ok: true, detail: "answered: ok", smoke: true } });
    const { net } = setup({ rels: [shadow, live, rel({ id: "r9", target: "agentcore" })], mig: { ...MIG, stale: true, changes: [{ field: "instruction", before: "", after: "Be brief." }], candidate: shadow, message: "You changed this agent after it was deployed." } });
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    const rows = await screen.findAllByTestId("release");
    expect(rows).toHaveLength(2);                                                         // only this runtime's releases
    expect(screen.getByTestId("migration-changes")).toHaveTextContent("instruction: (empty) → Be brief.");
    expect(screen.getByTestId("migration-changes")).toHaveTextContent("A shadow version (v2) is already deployed");
    expect(within(rows[0]).getByText("shadow")).toBeInTheDocument();
    expect(within(rows[1]).getByText("live")).toBeInTheDocument();
    expect(within(rows[0]).getByText("answered: ok")).toBeInTheDocument();
    await userEvent.click(within(rows[1]).getByRole("button", { name: "Check" }));
    await userEvent.click(within(rows[1]).getByRole("button", { name: "Send test message" }));
    await waitFor(() => expect(mutating(net.calls).some((c) => c.url.pathname.endsWith("/r1/health"))).toBe(true));
    expect(mutating(net.calls).find((c) => c.url.pathname.endsWith("/r1/health"))!.body).toEqual({ smoke: true });
    expect(within(rows[1]).queryByRole("button", { name: "Switch traffic" })).toBeNull();      // the live one cannot be switched to
    expect(within(rows[1]).queryByRole("button", { name: "Delete" })).toBeNull();               // ...or deleted while others exist
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Switch traffic" }));
    await waitFor(() => expect(mutating(net.calls).some((c) => c.url.pathname.endsWith("/r2/promote"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Roll back to the previous version" }));
    await waitFor(() => expect(mutating(net.calls).some((c) => c.url.pathname.endsWith("/rollback"))).toBe(true));
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mutating(net.calls).some((c) => c.method === "DELETE" && c.url.search === "?confirm=true")).toBe(true));
  });

  it("does nothing when the user declines a confirmation, and a single live version may be deleted", async () => {
    const spy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const only = rel({ id: "r2", live: true, prev_id: "r0" });
    const { net } = setup({ rels: [only, rel({ id: "r3", live: false, state: "ready" })] });
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    await screen.findAllByTestId("release");
    await userEvent.click(screen.getByRole("button", { name: "Switch traffic" }));
    await userEvent.click(screen.getByRole("button", { name: "Roll back to the previous version" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(spy).toHaveBeenCalledTimes(3);
    expect(mutating(net.calls)).toHaveLength(0);
  });

  it("shows a refused action's reason and keeps a failed release visible", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const net = setup({ rels: [rel({ id: "r1", state: "failed", live: false, detail: "image pull failed", target: "docker" })] });
    render(<Harness initial={{ id: "bot", runtime: "docker" }} />);
    expect(await screen.findByText("image pull failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send test message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Roll back to the previous version" })).toBeNull();
    net.net.fn.mockImplementation(async (input: any, init: any) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      if (init?.method === "POST") return jsonResponse({ detail: "platform said no" }, { status: 422 });
      if (url.pathname.endsWith("/bot")) return jsonResponse({ releases: [rel({ id: "r1", state: "ready", live: false, target: "docker" })], migration: { ...MIG, live: null } });
      return jsonResponse({ targets: TARGETS, kinds: KINDS, sources: [] });
    });
    await userEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("platform said no")).toBeInTheDocument();
  });
});
