import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentWizard, { makeId } from "@/components/AgentWizard";
import { forgetModels } from "@/components/ModelSelect";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const TOOLS = [
  { name: "read_file", label: "Read files", plain: "Open and read files.", risk: "safe", risk_text: "Only reads." },
  { name: "write_file", label: "Create files", plain: "Make new files.", risk: "changes", risk_text: "Can change files in the workspace folder." },
  { name: "edit_file", label: "Change files", plain: "Edit files.", risk: "changes", risk_text: "Can change files in the workspace folder." },
  { name: "run_shell", label: "Run commands", plain: "Run commands in the sandbox.", risk: "runs", risk_text: "Runs code, but only inside the sandbox." },
  { name: "create_skill", label: "Write new skills", plain: "Save new skills.", risk: "app", risk_text: "Changes Agent Studio itself." },
];
const TEMPLATES = [
  { id: "explorer", name: "Explorer", tagline: "Reads a project. Cannot change anything.", color: "#4f8cff", risk: "safe",
    agent: { name: "Explorer", description: "Reads a project and answers questions.", tier: "flash", instruction: "You explore read-only.", tools: ["read_file"], skills: ["repo-orientation"] } },
  { id: "orchestrator", name: "Orchestrator", tagline: "Leads a team.", color: "#e0559c", risk: "runs",
    agent: { name: "Orchestrator", description: "Leads a team.", tier: "pro", instruction: "You lead.", tools: ["read_file", "write_file", "run_shell"], skills: ["plan", "ghost-skill"],
      sub_agents: ["explorer", "ghost-agent"], can_spawn: true, acp_agents: ["claude", "ghost-cli"], acp_policy: "reads_auto", rag: ["*"] } },
];
const WIZ = { templates: TEMPLATES, tools: TOOLS, tiers: [{ id: "flash", label: "Fast and cheap", text: "Quick." }, { id: "pro", label: "Careful and strong", text: "Slow." }, { id: "custom", label: "A specific model", text: "Pick it." }], risk_text: {} };
const PROVS = [
  { id: "openai", label: "OpenAI", kind: "openai", enabled: true }, { id: "anthropic", label: "Anthropic", kind: "anthropic", enabled: false },
  { id: "mock", label: "Mock", kind: "mock", enabled: true }, { id: "ollama", label: "Ollama", kind: "ollama", enabled: true },
];
const models = (pid: string, ids: string[]) => ({ provider: pid, source: "live", error: null, cached: false, models: ids.map((id) => ({ id, label: id, input_per_m: null, output_per_m: null })) });

const props = () => ({
  agents: [{ id: "explorer", name: "Explorer" }, { id: "coder", name: "Coder" }],
  mcp: [{ id: "git", label: "Git", ready: true }, { id: "fetch", label: "Web fetch", ready: false }],
  skills: [{ name: "repo-orientation", description: "Orient", group: "Engineering" }, { name: "plan", description: "Plan", group: "Engineering" }, { name: "loose", description: "No group" }],
  kbs: [{ id: "faq", label: "FAQ" }, { id: "docs", label: "Docs" }],
  ext: [{ id: "claude", label: "Claude Code", installed: true, enabled: true }, { id: "aider", label: "Aider", installed: false, enabled: true }, { id: "kiro", label: "Kiro", installed: true, enabled: false }],
  onCreated: vi.fn(), onAdvanced: vi.fn(), onCancel: vi.fn(),
});

function setup(over: Partial<ReturnType<typeof props>> = {}, routes: Record<string, any> = {}) {
  const p = { ...props(), ...over };
  const r = routeFetch({
    "GET /api/easy/wizard": () => jsonResponse(WIZ),
    "GET /api/providers": () => jsonResponse(PROVS),
    "GET /api/providers/openai/models": () => jsonResponse(models("openai", ["gpt-x", "gpt-mini"])),
    "GET /api/providers/ollama/models": () => jsonResponse(models("ollama", ["qwen3:8b"])),
    "POST /api/agents": () => jsonResponse({ ok: true }),
    ...routes,
  });
  render(<AgentWizard {...p} />);
  return { ...r, p };
}
const next = () => userEvent.click(screen.getByRole("button", { name: "Next" }));
const back = () => userEvent.click(screen.getByRole("button", { name: "Back" }));
const heading = (t: string | RegExp) => screen.findByRole("heading", { name: t });
const created = (calls: any[]) => calls.find((c) => c.method === "POST" && c.url.pathname === "/api/agents")!.body;
beforeEach(() => ["openai", "ollama", "anthropic"].forEach(forgetModels));

describe("makeId", () => {
  it("makes a valid, unique id from any name", () => {
    expect(makeId("Inbox helper!", [])).toBe("inbox_helper");
    expect(makeId("Inbox helper", ["inbox_helper", "inbox_helper_2"])).toBe("inbox_helper_3");
    expect(makeId("42 answers", [])).toBe("agent_42_answers");
    expect(makeId("***", [])).toBe("agent_");
    expect(makeId("x", [])).toBe("x_1");                                        // too short on its own
    expect(makeId("a".repeat(60), []).length).toBeLessThanOrEqual(31);
  });
});

describe("AgentWizard: starting", () => {
  it("scrolls itself into view once loaded (on a narrow screen it sits below the agent list)", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    setup();
    await heading("What should this agent do?");
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });

  it("opens with the purpose question, and cannot move on until you say something", async () => {
    setup();
    await heading("What should this agent do?");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What the agent should do"), "ab");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("What the agent should do"), "c");
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
    expect(screen.getAllByRole("listitem").filter((li) => li.closest(".wiz-steps"))).toHaveLength(7);
  });

  it("offers the advanced editor and cancel at any point", async () => {
    const { p } = setup();
    await screen.findByText("Create an agent");
    await userEvent.click(screen.getByRole("button", { name: "Advanced editor" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(p.onAdvanced).toHaveBeenCalledTimes(1);
    expect(p.onCancel).toHaveBeenCalledTimes(1);
  });

  it("shows a way out when the wizard cannot load", async () => {
    const { p } = setup({}, { "GET /api/easy/wizard": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    expect(await screen.findByText("Could not load the wizard: down")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Advanced editor" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(p.onAdvanced).toHaveBeenCalled();
    expect(p.onCancel).toHaveBeenCalled();
  });

  it("still works if the provider list cannot be read", async () => {
    setup({}, { "GET /api/providers": () => jsonResponse({ detail: "x" }, { status: 500 }) });
    await heading("What should this agent do?");
  });
});

describe("AgentWizard: a ready-made agent, start to finish", () => {
  it("builds exactly the same definition the advanced editor would", async () => {
    const { calls, p } = setup();
    await userEvent.click(await screen.findByRole("button", { name: /Orchestrator/ }));
    expect(screen.getByRole("button", { name: /Orchestrator/ })).toHaveAttribute("aria-pressed", "true");
    await next();                                                                                  // -> name
    expect(screen.getByLabelText("Name")).toHaveValue("Orchestrator");
    expect(screen.getByText("orchestrator")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Team lead");
    await next();                                                                                  // -> model
    await userEvent.click(screen.getByRole("button", { name: /Careful and strong/ }));
    await next();                                                                                  // -> abilities
    expect(screen.getByRole("checkbox", { name: /Create files/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Read files/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Change files/ })).not.toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: "Git" }));
    await next();                                                                                  // -> permissions
    expect(screen.getByText("It can change files in your workspace folder.")).toBeInTheDocument();
    expect(screen.getByText("It can run commands, inside the sandbox.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Let it build its own/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Explorer" })).toBeChecked();                      // 'ghost-agent' was dropped: it does not exist
    expect(screen.getByRole("checkbox", { name: "Claude Code" })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "Aider" })).toBeNull();                           // not installed
    expect(screen.queryByRole("checkbox", { name: "Kiro" })).toBeNull();                            // disabled
    expect(screen.getByLabelText(/When they ask permission/)).toHaveValue("reads_auto");
    await next();                                                                                  // -> instructions
    expect(screen.getByLabelText("Instructions")).toHaveValue("You lead.");
    await next();                                                                                  // -> review
    expect(screen.getByText("Team lead")).toBeInTheDocument();
    expect(screen.getByText("Read files, Create files, Run commands")).toBeInTheDocument();
    expect(screen.getByText("all knowledge bases")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText("Team lead is ready");
    expect(created(calls)).toEqual({
      id: "team_lead", name: "Team lead", description: "Leads a team.", instruction: "You lead.", builtin: false, provider: null, tier: "pro", model: null,
      tools: ["read_file", "write_file", "run_shell"], mcp: ["git"], skills: ["plan"], sub_agents: ["explorer"], can_spawn: true, acp_agents: ["claude"], acp_policy: "reads_auto", rag: ["*"], memory: {},
    });
    await userEvent.click(screen.getByRole("button", { name: "See it in Agent Studio" }));
    expect(p.onCreated).toHaveBeenCalledWith("team_lead");
  });

  it("can un-pick a template and go back to a blank start", async () => {
    setup();
    const t = await screen.findByRole("button", { name: /Explorer/ });
    await userEvent.click(t);
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
    await userEvent.click(t);
    expect(t).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText("What the agent should do")).toHaveValue("Reads a project and answers questions.");      // what it filled in stays; you can edit it
  });
});

describe("AgentWizard: building one by hand", () => {
  async function toStep(n: number) {
    for (let i = 0; i < n; i++) await next();
  }

  it("lets you switch AG-UI components off for the new agent and sends only what you changed", async () => {
    const { calls } = setup();
    await userEvent.click(await screen.findByRole("button", { name: /Orchestrator/ }));
    await next(); await next(); await next(); await next();                                       // name, model, abilities, permissions
    expect(screen.getByTestId("agent-agui")).toBeInTheDocument();
    expect(screen.getByLabelText("Rich components in answers")).toBeChecked();
    await userEvent.click(screen.getByLabelText("Rich components in answers"));
    await next(); await next();                                                                    // instructions, review
    expect(screen.getByText("no components")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText(/is ready/);
    expect((created(calls) as { agui?: unknown }).agui).toEqual({ components: false, endpoint: true });
  });

  it("walks through every step, with quick picks for tools and a pinned model", async () => {
    const { calls } = setup();
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Summarise my meeting notes");
    await next();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();                           // a name is needed
    await userEvent.type(screen.getByLabelText("Name"), "Notes helper");
    expect(screen.getByPlaceholderText("Summarise my meeting notes")).toBeInTheDocument();         // the description box suggests the goal
    await userEvent.type(screen.getByLabelText(/One-line description/), "Turns notes into summaries");
    await next();

    await userEvent.click(screen.getByRole("button", { name: /A specific model/ }));
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Provider"), "openai");
    expect(screen.queryByRole("option", { name: "Mock" })).toBeNull();                             // the offline mock is never offered
    expect(screen.queryByRole("option", { name: "Anthropic" })).toBeNull();                        // a provider that is switched off is not offered
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Model" }), "gpt-x");
    await next();

    await userEvent.click(screen.getByRole("button", { name: "Only read" }));
    expect(screen.getByRole("checkbox", { name: /Read files/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Create files/ })).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Read, change files and run commands" }));
    expect(screen.getByRole("checkbox", { name: /Run commands/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Write new skills/ })).not.toBeChecked();         // app-changing tools are never part of a quick pick
    await userEvent.click(screen.getByRole("button", { name: "None" }));
    expect(screen.getByRole("checkbox", { name: /Read files/ })).not.toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: /Read files/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Read files/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Read files/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Web fetch/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Engineering/ }));                   // a whole skill group
    expect(screen.getByRole("checkbox", { name: "repo-orientation" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "repo-orientation" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Engineering/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "loose" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "FAQ" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "All of them" }));
    expect(screen.queryByRole("checkbox", { name: "FAQ" })).toBeNull();
    await userEvent.click(screen.getByRole("checkbox", { name: "All of them" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "FAQ" }));
    await next();

    expect(screen.getByText("It cannot change any files.")).toBeInTheDocument();
    expect(screen.getByText("It cannot run commands.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Coder" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Claude Code" }));
    await userEvent.selectOptions(screen.getByLabelText(/When they ask permission/), "readonly");
    await userEvent.click(screen.getByRole("checkbox", { name: /Let it build its own/ }));
    await next();

    await userEvent.type(screen.getByLabelText("Instructions"), "You summarise notes.");
    await next();
    expect(screen.getByText("openai · gpt-x")).toBeInTheDocument();
    expect(screen.queryByText(/No instructions yet/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText("Notes helper is ready");
    expect(created(calls)).toMatchObject({
      id: "notes_helper", name: "Notes helper", description: "Turns notes into summaries", provider: "openai", tier: "custom", model: "gpt-x",
      tools: ["read_file"], mcp: ["fetch"], skills: ["loose"], sub_agents: ["coder"], can_spawn: true, acp_agents: ["claude"], acp_policy: "readonly", rag: ["faq"],
    });
  });

  it("a local model needs a downloaded model chosen, and goes through Ollama", async () => {
    const { calls } = setup();
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Answer quickly");
    await next();
    await userEvent.type(screen.getByLabelText("Name"), "Local one");
    await next();
    await userEvent.click(screen.getByRole("button", { name: /A model on this PC/ }));
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.selectOptions(await screen.findByRole("combobox"), "qwen3:8b");
    await next();
    await toStep(3);
    expect(screen.getByText("qwen3:8b on this PC")).toBeInTheDocument();
    expect(screen.getByText(/No instructions yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText("Local one is ready");
    expect(created(calls)).toMatchObject({ provider: "ollama", tier: "custom", model: "qwen3:8b" });
  });

  it("warns when no provider is switched on for a pinned model", async () => {
    setup({}, { "GET /api/providers": () => jsonResponse([PROVS[1]]) });
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Anything");
    await next();
    await userEvent.type(screen.getByLabelText("Name"), "X1");
    await next();
    await userEvent.click(screen.getByRole("button", { name: /A specific model/ }));
    expect(screen.getByText(/No provider is switched on yet/)).toBeInTheDocument();
  });

  it("says when there is nothing to add: no add-ons, skills, knowledge bases, helpers or outside agents", async () => {
    setup({ mcp: [], skills: [], kbs: [], agents: [], ext: [] });
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Anything");
    await next(); await userEvent.type(screen.getByLabelText("Name"), "Bare"); await next(); await next();
    expect(screen.getByText(/None yet\. Create one on the/)).toBeInTheDocument();
    expect(screen.queryByText("Add-ons (MCP servers)")).toBeNull();
    await next();
    expect(screen.getByText(/None installed yet/)).toBeInTheDocument();
    expect(screen.queryByText(/Helper agents it can hand work to/)).toBeNull();
  });

  it("can go back and keeps what you typed", async () => {
    setup();
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Keep me");
    await next();
    await userEvent.type(screen.getByLabelText("Name"), "Keeper");
    await back();
    expect(screen.getByLabelText("What the agent should do")).toHaveValue("Keep me");
    await next();
    expect(screen.getByLabelText("Name")).toHaveValue("Keeper");
  });
});

describe("AgentWizard: drafting the instructions", () => {
  async function toInstructions() {
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Triage my support email");
    await next(); await userEvent.type(screen.getByLabelText("Name"), "Triage"); await next(); await next(); await next(); await next();
  }

  it("asks a model to draft it and shows what it cost", async () => {
    const { calls } = setup({}, { "POST /api/easy/wizard/draft": () => jsonResponse({ instruction: "You triage email.", cost_usd: 0.0012, provider: "openai", model: "gpt-x" }) });
    await toInstructions();
    await userEvent.click(screen.getByRole("button", { name: "Draft it for me" }));
    expect(await screen.findByDisplayValue("You triage email.")).toBeInTheDocument();
    expect(screen.getByText(/Drafted with openai · gpt-x \(cost about \$0\.0012\)/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname.endsWith("/draft"))!.body).toEqual({ goal: "Triage my support email", name: "Triage", tools: [] });
  });

  it("says nothing about cost for a free draft, and explains a failure", async () => {
    let ok = true;
    setup({}, { "POST /api/easy/wizard/draft": () => (ok ? jsonResponse({ instruction: "Free.", cost_usd: 0, provider: "ollama", model: "q" }) : jsonResponse({ detail: "No model is switched on yet." }, { status: 409 })) });
    await toInstructions();
    await userEvent.click(screen.getByRole("button", { name: "Draft it for me" }));
    expect(await screen.findByText("Drafted with ollama · q. Read it and change anything you like.")).toBeInTheDocument();
    ok = false;
    await userEvent.click(screen.getByRole("button", { name: "Draft it for me" }));
    expect(await screen.findByText("No model is switched on yet.")).toBeInTheDocument();
  });

  it("drafts from the description when there is no goal (a template was used)", async () => {
    const { calls } = setup({}, { "POST /api/easy/wizard/draft": () => jsonResponse({ instruction: "x", cost_usd: 0, provider: "p", model: "m" }) });
    await userEvent.click(await screen.findByRole("button", { name: /Explorer/ }));
    await next(); await next(); await next(); await next(); await next();
    await userEvent.click(screen.getByRole("button", { name: "Draft it for me" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname.endsWith("/draft"))!.body.goal).toBe("Reads a project and answers questions."));
  });
});

describe("AgentWizard: creating and trying it", () => {
  async function toReview(extra: Record<string, any> = {}) {
    const r = setup({}, extra);
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Do things");
    await next(); await userEvent.type(screen.getByLabelText("Name"), "Doer"); await next(); await next(); await next(); await next(); await next();
    return r;
  }

  it("shows a failed create and lets you try again", async () => {
    await toReview({ "POST /api/agents": () => jsonResponse({ detail: "unknown tools: ['x']" }, { status: 422 }) });
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText("⚠ unknown tools: ['x']")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create agent" })).toBeEnabled();
  });

  it("lets you try the new agent with a short message", async () => {
    const { calls } = await toReview({ "POST /api/chat/stream": () => sseResponse([`data: ${JSON.stringify({ type: "text", delta: "Hel" })}\n\n`, `data: ${JSON.stringify({ type: "text", delta: "lo" })}\n\n`, `data: ${JSON.stringify({ type: "done", reply: "Hello there" })}\n\n`]) });
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText("Doer is ready");
    expect(screen.getByRole("button", { name: "Try it" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Try the agent"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname === "/api/chat/stream")!.body).toEqual({ provider: "openai", agent: "doer", message: "hi" });
  });

  it("shows whatever text arrived when the run ends without a reply, and says when nothing came", async () => {
    let frames: object[] = [{ type: "text", delta: "partial" }, { type: "done", reply: "" }];
    await toReview({ "POST /api/chat/stream": () => sseResponse(frames.map((f) => `data: ${JSON.stringify(f)}\n\n`)) });
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await userEvent.type(await screen.findByLabelText("Try the agent"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("partial")).toBeInTheDocument();
    frames = [{ type: "done", reply: "" }];
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("(no answer)")).toBeInTheDocument();
  });

  it("explains an error, a refusal and a network failure while trying", async () => {
    let frames: object[] = [{ type: "error", message: "rate limit" }];
    let fail = false;
    await toReview({ "POST /api/chat/stream": () => { if (fail) throw new Error("offline"); return sseResponse(frames.map((f) => `data: ${JSON.stringify(f)}\n\n`)); } });
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await userEvent.type(await screen.findByLabelText("Try the agent"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("⚠ rate limit")).toBeInTheDocument();
    frames = [{ type: "error", title: "Provider overloaded" }];
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("⚠ Provider overloaded")).toBeInTheDocument();
    frames = [{ type: "error" }];
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("⚠ something went wrong")).toBeInTheDocument();
    frames = [{ type: "refused", detail: { message: "Spend cap reached" } }];
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("⚠ Spend cap reached")).toBeInTheDocument();
    frames = [{ type: "refused" }];
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("⚠ refused")).toBeInTheDocument();
    fail = true;
    await userEvent.click(screen.getByRole("button", { name: "Try it" }));
    expect(await screen.findByText("⚠ offline")).toBeInTheDocument();
  });

  it("cannot be tried when no provider is on, and 'Create another' starts fresh", async () => {
    setup({}, { "GET /api/providers": () => jsonResponse([]) });
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Do things");
    await next(); await userEvent.type(screen.getByLabelText("Name"), "Doer"); await next(); await next(); await next(); await next(); await next();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText("Doer is ready");
    expect(screen.getByText(/Switch on a model provider first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try it" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Create another" }));
    expect(await screen.findByLabelText("What the agent should do")).toHaveValue("");
    expect(within(document.body).getByRole("button", { name: "Back" })).toBeDisabled();
  });
});

describe("AgentWizard: sparse ready-made agents and empty goals", () => {
  const SPARSE = { id: "bare", name: "Bare", tagline: "Almost nothing set.", color: "#999", risk: "safe", agent: { name: "Bare", tools: ["read_file", "ghost_tool"] } };
  const withSparse = () => setup({}, { "GET /api/easy/wizard": () => jsonResponse({ ...WIZ, templates: [...TEMPLATES, SPARSE] }) });

  it("fills in sensible defaults when a ready-made agent leaves fields out, and names tools it does not know by their id", async () => {
    withSparse();
    await userEvent.click(await screen.findByRole("button", { name: /Bare/ }));
    await next();
    expect(screen.getByLabelText("Name")).toHaveValue("Bare");
    expect(screen.getByLabelText(/One-line description/)).toHaveValue("");
    await next(); await next(); await next(); await next();
    expect(screen.getByLabelText("Instructions")).toHaveValue("");
    await next();
    expect(screen.getByText("Read files, ghost_tool")).toBeInTheDocument();
    expect(screen.getByText(/No instructions yet/)).toBeInTheDocument();
  });

  it("a ready-made agent without a goal still works: the description box and the draft button follow what you type", async () => {
    const { calls } = setup({}, { "POST /api/easy/wizard/draft": () => jsonResponse({ instruction: "Drafted.", cost_usd: 0, provider: "p", model: "m" }) });
    await userEvent.click(await screen.findByRole("button", { name: /Explorer/ }));
    await userEvent.clear(screen.getByLabelText("What the agent should do"));
    await next();
    expect(screen.getByPlaceholderText("What it is good at")).toBeInTheDocument();                  // no goal to suggest from
    await userEvent.clear(screen.getByLabelText(/One-line description/));
    await next(); await next(); await next(); await next();
    expect(screen.getByRole("button", { name: "Draft it for me" })).toBeDisabled();                // nothing to draft from yet
    await back(); await back(); await back(); await back();
    await userEvent.type(screen.getByLabelText(/One-line description/), "Reads things");
    await next(); await next(); await next(); await next();
    await userEvent.click(screen.getByRole("button", { name: "Draft it for me" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname.endsWith("/draft"))!.body.goal).toBe("Reads things"));
  });

  it("falls back to the goal text for the description when none is typed", async () => {
    const { calls } = setup();
    await userEvent.type(await screen.findByLabelText("What the agent should do"), "Count the files in my folder please");
    await next(); await userEvent.type(screen.getByLabelText("Name"), "Counter"); await next(); await next(); await next(); await next(); await next();
    await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
    await screen.findByText("Counter is ready");
    expect(created(calls).description).toBe("Count the files in my folder please");
  });
});
