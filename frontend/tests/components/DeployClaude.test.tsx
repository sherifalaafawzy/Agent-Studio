import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DeployClaude from "@/components/DeployClaude";
import { jsonResponse, routeFetch } from "../helpers/http";

const AGENT = { id: "bot_one", name: "Bot One" };
const file = (over: Record<string, unknown> = {}) => ({ path: "H:/p/.claude/agents/bot-one.md", kind: "agent", action: "create", note: "", content: "x", diff: "+hello", ...over });
const plan = (over: Record<string, unknown> = {}) => ({
  mode: "export", scope: "project", agent: AGENT, root: "H:/p/.claude", files: [file()], commands: [], not_carried: [], warnings: [], notes: [], prerequisites: [], env_vars: [],
  claude_found: true, hash: "abc", ...over,
});

afterEach(() => vi.unstubAllGlobals());

async function openPreview(planBody: unknown, extra: Record<string, () => Response> = {}) {
  const r = routeFetch({ "POST /api/agents/bot_one/claude-code/plan": () => jsonResponse(planBody), ...extra });
  render(<DeployClaude agent={AGENT} onClose={() => undefined} />);
  await userEvent.type(screen.getByPlaceholderText("e.g. H:/my-project"), "H:/p");
  await userEvent.click(screen.getByRole("button", { name: "Preview" }));
  await screen.findByText(/Target:/);
  return r;
}

describe("Deploy to Claude Code: choosing", () => {
  it("needs a project folder before it can preview, and sends the choices", async () => {
    const r = routeFetch({ "POST /api/agents/bot_one/claude-code/plan": () => jsonResponse(plan()) });
    render(<DeployClaude agent={AGENT} onClose={() => undefined} />);
    expect(screen.getByText(/nothing is written until you confirm/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
    await userEvent.type(screen.getByPlaceholderText("e.g. H:/my-project"), "H:/p");
    await userEvent.click(screen.getByLabelText(/Also copy its sub-agents/));
    await userEvent.click(screen.getByLabelText(/Also copy the skills/));
    await userEvent.selectOptions(screen.getByLabelText("Where MCP servers go"), "agent");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText(/Target:/);
    expect(r.calls[0].body).toEqual({ mode: "export", scope: "project", project_path: "H:/p", include_sub_agents: false, include_skills: false, mcp_placement: "agent" });
  });

  it("uses the user folder without asking for a path, and hides the copy options in live mode", async () => {
    const r = routeFetch({ "POST /api/agents/bot_one/claude-code/plan": () => jsonResponse(plan({ scope: "user", mode: "live" })) });
    render(<DeployClaude agent={AGENT} onClose={() => undefined} />);
    await userEvent.click(screen.getByLabelText(/All my projects/));
    expect(screen.queryByPlaceholderText("e.g. H:/my-project")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /claude mcp add/ })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Let Claude Code call the live agent/));
    expect(screen.queryByLabelText(/Also copy its sub-agents/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Copy this agent into Claude Code/));
    expect(screen.getByLabelText(/Also copy its sub-agents/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/One project folder/));
    expect(screen.getByPlaceholderText("e.g. H:/my-project")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/All my projects/));
    await userEvent.click(screen.getByLabelText(/Let Claude Code call the live agent/));
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText(/Claude Code will call the running agent/);
    expect(r.calls[0].body).toMatchObject({ mode: "live", scope: "user", project_path: "" });
  });

  it("shows why a preview was refused", async () => {
    routeFetch({ "POST /api/agents/bot_one/claude-code/plan": () => jsonResponse({ detail: "that project folder does not exist" }, { status: 422 }) });
    render(<DeployClaude agent={AGENT} onClose={() => undefined} />);
    await userEvent.type(screen.getByPlaceholderText("e.g. H:/my-project"), "H:/nope");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("that project folder does not exist");
  });
});

describe("Deploy to Claude Code: the preview", () => {
  it("lists files with their state, warnings, notes, variables and what is not carried over", async () => {
    await openPreview(plan({
      files: [file(), file({ path: "H:/p/.mcp.json", kind: "mcp-config", action: "update", note: "adds server entries", diff: "-a\n+b" }), file({ path: "H:/p/x.md", action: "unchanged", diff: "" }),
        file({ path: "H:/p/bad.json", kind: "mcp-config", action: "blocked", note: "it is not a valid MCP configuration file", diff: "" })],
      warnings: ["Bot One: something to know"], notes: ["A note"], env_vars: [{ name: "SNYK_TOKEN", used_by: "snyk", help: "Set it yourself." }],
      not_carried: [{ item: "Bot One: rag_search", why: "Knowledge-base search only exists inside Agent Studio." }],
      prerequisites: [{ id: "api", ok: true, text: "The API is on." }, { id: "agent", ok: false, text: "API access is off for the agent." }],
    }));
    const rows = screen.getAllByTestId("deploy-file");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByText("new")).toBeInTheDocument();
    expect(within(rows[1]).getByText("already exists")).toBeInTheDocument();
    expect(within(rows[2]).getByText("no change")).toBeInTheDocument();
    expect(within(rows[3]).getByText("blocked")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-warnings")).toHaveTextContent("something to know");
    expect(screen.getByText("A note")).toBeInTheDocument();
    expect(screen.getByTestId("deploy-env")).toHaveTextContent("SNYK_TOKEN");
    expect(screen.getByTestId("deploy-prereq")).toHaveTextContent("✓ The API is on.");
    expect(screen.getByTestId("deploy-prereq")).toHaveTextContent("✕ API access is off");
    expect(screen.getByTestId("deploy-not-carried")).toHaveTextContent("Not carried over (1)");
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Show content" }));
    expect(within(rows[0]).getByText("+hello")).toBeInTheDocument();
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Hide content" }));
    expect(within(rows[0]).queryByText("+hello")).not.toBeInTheDocument();
    await userEvent.click(within(rows[1]).getByRole("button", { name: "Show changes" }));
    expect(within(rows[1]).getByText(/\+b/)).toBeInTheDocument();
  });

  it("writes only new files until an existing one is ticked to be replaced, then confirms", async () => {
    const r = await openPreview(plan({ files: [file(), file({ path: "H:/p/.mcp.json", kind: "mcp-config", action: "update", diff: "-a\n+b" })] }), {
      "POST /api/agents/bot_one/claude-code/apply": () => jsonResponse({ ok: true, results: [{ path: "H:/p/.claude/agents/bot-one.md", status: "written" },
        { path: "H:/p/.mcp.json", status: "overwritten", backup: "D:/backups/x" }, { path: "H:/p/z", status: "skipped", detail: "left alone" }] }),
    });
    const confirm = screen.getByRole("button", { name: /Confirm: write 1 file$/ });
    await userEvent.click(screen.getByLabelText(/Replace it/));
    expect(screen.getByRole("button", { name: /Confirm: write 2 files$/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Confirm: write 2 files$/ }));
    expect(r.calls.at(-1)!.body).toMatchObject({ confirm: true, plan_hash: "abc", overwrite: ["H:/p/.mcp.json"], run_commands: false, mode: "export" });
    const done = await screen.findByTestId("deploy-results");
    expect(done).toHaveTextContent("written");
    expect(done).toHaveTextContent("old copy kept: D:/backups/x");
    expect(done).toHaveTextContent("left alone");
    expect(confirm).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Deploy again" }));
    expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
  });

  it("has nothing to confirm when every file is unchanged or blocked", async () => {
    await openPreview(plan({ files: [file({ action: "unchanged" })] }));
    expect(screen.getByRole("button", { name: /Confirm: write 0 files/ })).toBeDisabled();
  });

  it("goes back to the choices, and shows an error when the preview went stale", async () => {
    await openPreview(plan(), { "POST /api/agents/bot_one/claude-code/apply": () => jsonResponse({ detail: "The agent or the target changed since the preview. Preview again before writing." }, { status: 409 }) });
    await userEvent.click(screen.getByRole("button", { name: /Confirm: write 1 file/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("changed since the preview");
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("button", { name: "Preview" })).toBeInTheDocument();
  });

  it("shows commands, copies them, and runs them only when asked", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
    const cmds = [{ name: "fetch", action: "create", display: "claude mcp add-json fetch '{}' --scope user", diff: "" },
      { name: "memory", action: "update", display: "claude mcp remove memory --scope user\nclaude mcp add-json memory '{}' --scope user", diff: "" }, { name: "git", action: "unchanged", display: "", diff: "" }];
    const r = await openPreview(plan({ scope: "user", files: [], commands: cmds }), {
      "POST /api/agents/bot_one/claude-code/apply": () => jsonResponse({ ok: false, results: [{ command: "fetch", status: "failed", detail: "boom" }, { command: "git", status: "unchanged" }] }),
    });
    const rows = screen.getAllByTestId("deploy-command");
    expect(rows).toHaveLength(3);
    expect(screen.getByRole("button", { name: /Confirm: write 0 files$/ })).toBeDisabled();
    await userEvent.click(within(rows[0]).getByRole("button", { name: "Copy command" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(cmds[0].display);
    expect(await within(rows[0]).findByRole("button", { name: "Copied" })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Run these commands for me/));
    expect(screen.getByRole("button", { name: /Confirm: write 0 files and run 1 command$/ })).toBeEnabled();
    await userEvent.click(within(rows[1]).getByLabelText(/Replace the server/));
    await userEvent.click(screen.getByRole("button", { name: /and run 2 commands$/ }));
    expect(r.calls.at(-1)!.body).toMatchObject({ overwrite: ["memory"], run_commands: true });
    expect(await screen.findByTestId("deploy-results")).toHaveTextContent("boom");
  });

  it("cannot run commands when the claude program is missing, and survives a blocked clipboard", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => { throw new Error("denied"); }) } });
    await openPreview(plan({ scope: "user", files: [], claude_found: false, commands: [{ name: "fetch", action: "create", display: "claude mcp add-json fetch '{}'", diff: "" }] }));
    expect(screen.getByLabelText(/Run these commands for me/)).toBeDisabled();
    expect(screen.getByText(/was not found on this PC/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy command" }));
    expect(screen.getByRole("button", { name: "Copy command" })).toBeInTheDocument();
  });
});

describe("Deploy to Claude Code: a live agent", () => {
  it("creates a key for this agent only, shows it once and never sends it anywhere else", async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
    const r = await openPreview(plan({ mode: "live", env_vars: [{ name: "AGENT_STUDIO_KEY", used_by: "agent-studio-bot-one", help: "never written to any file" }] }), {
      "POST /api/access/keys": () => jsonResponse({ id: "k1", key: "asx_demo_secretvalue" }),
    });
    expect(screen.queryByTestId("deploy-key")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create a key for this agent" }));
    const box = await screen.findByTestId("deploy-key");
    expect(box).toHaveTextContent("asx_demo_secretvalue");
    expect(box).toHaveTextContent("shown only once");
    expect(box).toHaveTextContent("AGENT_STUDIO_KEY");
    expect(r.calls.at(-1)!.body).toEqual({ name: "Claude Code: Bot One", agents: ["bot_one"] });
    await userEvent.click(within(box).getByRole("button", { name: "Copy" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("asx_demo_secretvalue");
    expect(await within(box).findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("reports a key that could not be created and drops the key when the dialog closes", async () => {
    const onClose = vi.fn();
    routeFetch({
      "POST /api/agents/bot_one/claude-code/plan": () => jsonResponse(plan({ mode: "live" })),
      "POST /api/access/keys": () => jsonResponse({ detail: "at most 50 keys: delete one you no longer use" }, { status: 422 }),
    });
    const { unmount } = render(<DeployClaude agent={AGENT} onClose={onClose} />);
    await userEvent.click(screen.getByLabelText(/Let Claude Code call the live agent/));
    await userEvent.type(screen.getByPlaceholderText("e.g. H:/my-project"), "H:/p");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await userEvent.click(await screen.findByRole("button", { name: "Create a key for this agent" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 50 keys");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    unmount();
    expect(document.body.textContent).not.toContain("asx_");
  });
});
