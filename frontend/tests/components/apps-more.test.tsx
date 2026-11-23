import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch } from "../helpers/http";

import SettingsPanel from "@/components/apps/SettingsPanel";
import GitPanel from "@/components/apps/GitPanel";
import SimpleCreate, { BUILD_KEY } from "@/components/apps/SimpleCreate";
import AppsSettings from "@/components/AppsSettings";
import type { AppRow } from "@/lib/apps";

const APP: AppRow = { id: "a1", name: "Demo", slug: "demo", description: "", template: "static-chat", type: "static", mode: "advanced", session_id: "", version: 1, git: {}, agents: [], pending: 0, files: 3, size: 100, created: 1, updated: 1 };
const MAN = { schema: 1, name: "Demo", description: "d", version: "0.1.0", type: "static", entry: "public/index.html", start: "", port: 8080, agents: [{ id: "coder", purpose: "answers" }],
  permissions: { network: "none", allow: [] }, env: [{ name: "TOKEN", description: "the token", required: false }], runtime: "local" };
const AGENTS = [{ id: "coder", name: "Coder", api_access: false }, { id: "reviewer", name: "Reviewer", api_access: true }, { id: "app_builder", name: "App Builder", api_access: false }];

function settingsRoutes(over: Record<string, any> = {}) {
  return routeFetch({
    "GET /api/apps/a1/manifest": () => jsonResponse(MAN), "GET /api/agents": () => jsonResponse(AGENTS), "GET /api/apps/a1/env": () => jsonResponse([{ name: "TOKEN", description: "the token", required: false, set: false }]),
    "GET /api/apps/a1/key": () => jsonResponse({ exists: false, agents: ["coder"], note: "The key is created the first time the app runs." }), "PUT /api/apps/a1/manifest": () => jsonResponse(MAN), ...over,
  });
}

describe("SettingsPanel", () => {
  it("edits the manifest: agents, what it may reach, variables, and saves", async () => {
    const { calls } = settingsRoutes();
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<SettingsPanel app={APP} onChanged={onChanged} />);
    expect(await screen.findByLabelText("App name")).toHaveValue("Demo");
    expect(screen.queryByText("App Builder")).toBeNull();
    expect(screen.queryByLabelText("Start command")).toBeNull();
    await user.clear(screen.getByLabelText("App name")); await user.type(screen.getByLabelText("App name"), "Renamed");
    await user.clear(screen.getByLabelText("App version")); await user.type(screen.getByLabelText("App version"), "1.0.0");
    await user.clear(screen.getByLabelText("App description")); await user.type(screen.getByLabelText("App description"), "new text");
    await user.click(screen.getByLabelText("Use agent Reviewer"));
    await user.type(screen.getByLabelText("What Reviewer does for this app"), "reviews");
    await user.click(screen.getByLabelText("Use agent Coder"));
    expect(screen.queryByLabelText("What Coder does for this app")).toBeNull();
    await user.selectOptions(screen.getByLabelText("Network access"), "allowlist");
    await user.type(screen.getByLabelText("Allowed addresses"), "https://api.example.com{Enter}https://b.example.com");
    await user.type(screen.getByLabelText("Variable 1 name"), "x");
    await user.type(screen.getByLabelText("Variable 1 description"), " more");
    await user.click(screen.getByRole("button", { name: "Add a variable" }));
    await user.type(screen.getByLabelText("Variable 2 name"), "other");
    await user.click(screen.getByRole("button", { name: "Remove variable OTHER" }));
    await user.click(screen.getByRole("button", { name: "Save app settings" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const body = calls.find((c) => c.method === "PUT")!.body;
    expect(body).toMatchObject({ name: "Renamed", version: "1.0.0", description: "new text", agents: [{ id: "reviewer", purpose: "reviews" }], permissions: { network: "allowlist", allow: ["https://api.example.com", "https://b.example.com"] } });
    expect(body.env).toEqual([{ name: "TOKENX", description: "the token more", required: false }]);
    expect(body.port).toBeUndefined();
    await user.selectOptions(screen.getByLabelText("Network access"), "open");
    expect(screen.getByRole("note")).toHaveTextContent("any https address");
    expect(screen.queryByLabelText("Allowed addresses")).toBeNull();
  });

  it("shows port and start for a server app and turns on API access", async () => {
    let enabled = false;
    const { calls } = settingsRoutes({ "GET /api/agents": () => jsonResponse(AGENTS.map((a) => (a.id === "coder" ? { ...a, api_access: enabled } : a))), "PUT /api/agents/coder": () => { enabled = true; return jsonResponse({}); },
      "GET /api/apps/a1/manifest": () => jsonResponse({ ...MAN, type: "node", start: "node server/index.js" }) });
    const user = userEvent.setup();
    render(<SettingsPanel app={{ ...APP, type: "node" }} onChanged={() => {}} />);
    await user.clear(await screen.findByLabelText("App port")); await user.type(screen.getByLabelText("App port"), "9000");
    await user.clear(screen.getByLabelText("Start command")); await user.type(screen.getByLabelText("Start command"), "node a.js");
    await user.click(screen.getByRole("button", { name: "Save app settings" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.pathname.endsWith("/manifest"))).toBe(true));
    expect(calls.find((c) => c.url.pathname.endsWith("/manifest") && c.method === "PUT")!.body).toMatchObject({ port: 9000, start: "node a.js" });
    await user.click(screen.getByRole("button", { name: "Turn on API access" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Turn on API access" })).toBeNull());
  });

  it("keeps environment values write-only", async () => {
    let set = false;
    const { calls } = settingsRoutes({
      "GET /api/apps/a1/env": () => jsonResponse([{ name: "TOKEN", description: "the token", required: false, set }]),
      "PUT /api/apps/a1/env": (init: any) => { const b = JSON.parse(init.body); set = !!b.TOKEN; return jsonResponse([{ name: "TOKEN", description: "the token", required: false, set }]); },
    });
    const user = userEvent.setup();
    render(<SettingsPanel app={APP} onChanged={() => {}} />);
    const box = await screen.findByLabelText("Value of TOKEN");
    expect(box).toHaveAttribute("type", "password");
    expect(box).toHaveAttribute("placeholder", "not set");
    expect(screen.getByRole("button", { name: "Save value of TOKEN" })).toBeDisabled();
    await user.type(box, "s3cret");
    await user.click(screen.getByRole("button", { name: "Save value of TOKEN" }));
    await waitFor(() => expect(screen.getByLabelText("Value of TOKEN")).toHaveAttribute("placeholder", "set (hidden)"));
    expect(screen.getByLabelText("Value of TOKEN")).toHaveValue("");
    expect(document.body.textContent).not.toContain("s3cret");
    await user.click(screen.getByRole("button", { name: "Clear value of TOKEN" }));
    await waitFor(() => expect(screen.getByLabelText("Value of TOKEN")).toHaveAttribute("placeholder", "not set"));
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ TOKEN: "s3cret" }, { TOKEN: "" }]);
  });

  it("makes, rotates and revokes the app's key (shown once)", async () => {
    let key: any = { exists: false, agents: ["coder"], note: "The key is created the first time the app runs." };
    settingsRoutes({
      "GET /api/apps/a1/key": () => jsonResponse(key),
      "POST /api/apps/a1/key/rotate": () => { key = { exists: true, id: "k1", prefix: "asx_k1_abcde", agents: ["coder"], revoked: false, uses: 3 }; return jsonResponse({ key: "asx_k1_SECRET", ...key }); },
      "POST /api/apps/a1/key/revoke": () => { key = { ...key, revoked: true }; return jsonResponse(key); },
    });
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    render(<SettingsPanel app={APP} onChanged={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Make a key to use outside Agent Studio" }));
    const dlg = within(await screen.findByRole("dialog"));
    expect(dlg.getByLabelText("New app key")).toHaveValue("asx_k1_SECRET");
    await user.click(dlg.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("asx_k1_SECRET");
    await user.click(dlg.getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.textContent).not.toContain("asx_k1_SECRET");
    expect(await screen.findByText(/3 calls/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revoke key" }));
    expect(await screen.findByText(/revoked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke key" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Rotate key" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("shows no key button when there are no agents, and reports errors", async () => {
    settingsRoutes({ "GET /api/apps/a1/key": () => jsonResponse({ exists: false, agents: [], note: "Add an agent to the manifest first." }), "PUT /api/apps/a1/manifest": () => jsonResponse({ detail: "port must be a whole number" }, { status: 422 }),
      "PUT /api/apps/a1/env": () => jsonResponse({ detail: "'TOKEN' is not listed" }, { status: 422 }), "PUT /api/agents/coder": () => jsonResponse({ detail: "no such agent" }, { status: 404 }) });
    const user = userEvent.setup();
    render(<SettingsPanel app={APP} onChanged={() => {}} />);
    expect(await screen.findByText(/Add an agent to the manifest first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Make a key to use outside Agent Studio" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Save app settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("port must");
    await user.type(screen.getByLabelText("Value of TOKEN"), "x");
    await user.click(screen.getByRole("button", { name: "Save value of TOKEN" }));
    expect(await screen.findByText(/not listed/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn on API access" }));
    expect(await screen.findByText(/no such agent/)).toBeInTheDocument();
  });

  it("reports key and load errors", async () => {
    settingsRoutes({ "GET /api/apps/a1/key": () => jsonResponse({ exists: false, agents: ["coder"], note: "x" }), "POST /api/apps/a1/key/rotate": () => jsonResponse({ detail: "app is off" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<SettingsPanel app={APP} onChanged={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Make a key to use outside Agent Studio" }));
    expect(await screen.findByText(/app is off/)).toBeInTheDocument();
    routeFetch({ "GET /api/apps/a1/manifest": () => jsonResponse({ detail: "no manifest" }, { status: 404 }), "GET /api/agents": () => jsonResponse([]), "GET /api/apps/a1/env": () => jsonResponse([]), "GET /api/apps/a1/key": () => jsonResponse({}) });
    render(<SettingsPanel app={APP} onChanged={() => {}} />);
    expect(await screen.findByText("no manifest")).toBeInTheDocument();
  });
});

describe("GitPanel", () => {
  const OK = { initialised: true, remote: "", branch: "main", changes: [{ status: "M", path: "public/app.js" }, { status: "??", path: "new.txt" }], last_commit: "abc1234 first", has_token: false };

  it("starts a repository, then commits and shows the changes and history", async () => {
    let st: any = { initialised: false, remote: "", branch: "", changes: [], has_token: false };
    const { calls } = routeFetch({
      "GET /api/apps/a1/git": () => jsonResponse(st), "GET /api/apps/a1/git/log": () => jsonResponse([{ hash: "abc1234", author: "Ada", date: "2026-11-01", message: "first" }]),
      "POST /api/apps/a1/git/init": () => { st = OK; return jsonResponse(OK); }, "POST /api/apps/a1/git/commit": () => { st = { ...OK, changes: [] }; return jsonResponse({ commit: "abc" }); },
      "GET /api/apps/a1/git/diff": () => jsonResponse({ diff: "" }),
    });
    const user = userEvent.setup();
    render(<GitPanel appId="a1" refreshKey={0} />);
    expect(await screen.findByText(/not a git repository yet/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Branch name")); await user.type(screen.getByLabelText("Branch name"), "trunk");
    await user.click(screen.getByRole("button", { name: "Start a repository" }));
    expect(await screen.findByRole("list", { name: "Changed files" })).toBeInTheDocument();
    expect(calls.find((c) => c.url.pathname.endsWith("/init"))!.body).toEqual({ branch: "trunk" });
    expect(screen.getByRole("list", { name: "Commit history" })).toHaveTextContent("first");
    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();
    await user.type(screen.getByLabelText("Commit message"), "second");
    await user.click(screen.getByRole("button", { name: "Commit" }));
    expect(await screen.findByText("Nothing to commit.")).toBeInTheDocument();
    expect(screen.getByLabelText("Commit message")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Show changes" }));
    expect(await screen.findByLabelText("Git diff")).toHaveTextContent("(no changes)");
  });

  it("saves a remote (token write-only), pushes and pulls only after a confirmation", async () => {
    let st: any = { ...OK };
    const { calls } = routeFetch({
      "GET /api/apps/a1/git": () => jsonResponse(st), "GET /api/apps/a1/git/log": () => jsonResponse([]),
      "PUT /api/apps/a1/git/remote": () => { st = { ...st, remote: "https://github.com/a/b.git", has_token: true }; return jsonResponse(st); },
      "POST /api/apps/a1/git/push": () => jsonResponse({ pushed: true }), "POST /api/apps/a1/git/pull": () => jsonResponse({ pulled: true }),
      "GET /api/apps/a1/git/diff": () => jsonResponse({ diff: "diff --git a/x b/x" }),
    });
    const user = userEvent.setup();
    render(<GitPanel appId="a1" refreshKey={0} />);
    await screen.findByLabelText("Remote address");
    expect(screen.getByRole("button", { name: "Push" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save remote" })).toBeDisabled();
    await user.type(screen.getByLabelText("Remote address"), "https://github.com/a/b.git");
    await user.type(screen.getByLabelText("Remote token"), "tok-1234");
    await user.click(screen.getByRole("button", { name: "Save remote" }));
    await waitFor(() => expect(screen.getByLabelText("Remote token")).toHaveAttribute("placeholder", "set (hidden)"));
    expect(screen.getByLabelText("Remote token")).toHaveValue("");
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ url: "https://github.com/a/b.git", branch: "main", token: "tok-1234" });
    await user.click(screen.getByRole("button", { name: "Push" }));
    const dlg = within(screen.getByRole("dialog"));
    expect(dlg.getByText(/sends the app's committed files to https:\/\/github.com\/a\/b.git/)).toBeInTheDocument();
    await user.click(dlg.getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.url.pathname.endsWith("/push"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Push" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Push" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname.endsWith("/push"))!.body).toEqual({ confirm: true }));
    await user.click(screen.getByRole("button", { name: "Pull" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("saved as a version first");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Pull" }));
    await waitFor(() => expect(calls.find((c) => c.url.pathname.endsWith("/pull"))!.body).toEqual({ confirm: true }));
    await user.click(screen.getByRole("button", { name: "Show changes" }));
    expect(await screen.findByLabelText("Git diff")).toHaveTextContent("diff --git");
  });

  it("explains a missing git identity and other errors", async () => {
    routeFetch({
      "GET /api/apps/a1/git": () => jsonResponse({ ...OK, remote: "https://x.test/a.git" }), "GET /api/apps/a1/git/log": () => jsonResponse([]),
      "POST /api/apps/a1/git/commit": () => jsonResponse({ detail: "Set your name and email for commits in Settings > Apps first (git needs them)." }, { status: 422 }),
      "GET /api/apps/a1/git/diff": () => jsonResponse({ detail: "no repo" }, { status: 422 }), "POST /api/apps/a1/git/push": () => jsonResponse({ detail: "rejected" }, { status: 422 }),
    });
    const user = userEvent.setup();
    render(<GitPanel appId="a1" refreshKey={0} />);
    await user.type(await screen.findByLabelText("Commit message"), "x");
    await user.click(screen.getByRole("button", { name: "Commit" }));
    expect(await screen.findByRole("link", { name: "Open Settings" })).toHaveAttribute("href", "/settings/");
    await user.click(screen.getByRole("button", { name: "Show changes" }));
    expect(await screen.findByText(/no repo/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Push" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Push" }));
    expect(await screen.findByText(/rejected/)).toBeInTheDocument();
    routeFetch({ "GET /api/apps/a1/git": () => jsonResponse({ detail: "git is not installed" }, { status: 422 }) });
    render(<GitPanel appId="a1" refreshKey={0} />);
    expect(await screen.findByText("git is not installed")).toBeInTheDocument();
  });
});

describe("SimpleCreate", () => {
  const TPLS = [{ id: "static-chat", label: "Chat page", type: "static", description: "A chat page." }, { id: "node-api", label: "Node server", type: "node", description: "A server." }];

  it("creates the app, keeps the attachments and remembers the prompt for the builder", async () => {
    const created = vi.fn();
    const { calls } = routeFetch({
      "GET /api/apps/templates": () => jsonResponse(TPLS), "GET /api/agents": () => jsonResponse(AGENTS),
      "POST /api/apps": () => jsonResponse({ id: "n1" }),
      "POST /api/apps/n1/attachments": (init: any) => { const b = JSON.parse(init.body); return b.name === "big.pdf" ? jsonResponse({ detail: "too large" }, { status: 422 }) : jsonResponse({ path: `assets/${b.name}` }); },
    });
    const user = userEvent.setup();
    render(<SimpleCreate onCreated={created} />);
    const go = screen.getByRole("button", { name: "Build it" });
    expect(go).toBeDisabled();
    await user.type(screen.getByLabelText("What should the app do?"), "A page that writes polite replies to customer emails");
    await waitFor(() => expect(screen.getByRole("option", { name: "Coder" })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: "App Builder" })).toBeNull();
    await user.selectOptions(screen.getByLabelText("Agent the app uses"), "coder");
    expect(screen.getByRole("note")).toHaveTextContent("Coder has API access switched off");
    await user.selectOptions(screen.getByLabelText("Agent the app uses"), "reviewer");
    expect(screen.queryByRole("note")).toBeNull();
    await user.selectOptions(screen.getByLabelText("Kind of app"), "node-api");
    expect(screen.getByText("A server.")).toBeInTheDocument();
    const input = screen.getByLabelText("Attach files") as HTMLInputElement;
    await user.upload(input, [new File(["# spec"], "spec.md"), new File([new Uint8Array([1, 2])], "big.pdf"), new File(["x"], "gone.csv")]);
    expect(screen.getByRole("list", { name: "Attached files" })).toHaveTextContent("gone.csv");
    await user.click(screen.getByRole("button", { name: "Remove gone.csv" }));
    await user.click(screen.getByRole("button", { name: "Attach files" }));
    await user.click(screen.getByRole("button", { name: "Build it" }));
    await waitFor(() => expect(created).toHaveBeenCalledWith("n1"));
    expect(calls.find((c) => c.url.pathname === "/api/apps" && c.method === "POST")!.body).toMatchObject({ name: "A page that writes polite replies", template: "node-api", agent_id: "reviewer", mode: "simple" });
    expect(JSON.parse(sessionStorage.getItem(BUILD_KEY("n1"))!)).toEqual({ prompt: "A page that writes polite replies to customer emails", attachments: ["spec.md"], failed: ["big.pdf: too large"] });
  });

  it("shows errors when creating fails", async () => {
    routeFetch({ "GET /api/apps/templates": () => jsonResponse({ detail: "no templates" }, { status: 500 }), "GET /api/agents": () => jsonResponse({ detail: "no agents" }, { status: 500 }), "POST /api/apps": () => jsonResponse({ detail: "at most 50 apps" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<SimpleCreate onCreated={() => {}} />);
    await user.type(screen.getByLabelText("What should the app do?"), "x");
    await user.click(screen.getByRole("button", { name: "Build it" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("at most 50 apps"));
    expect(screen.getByRole("button", { name: "Build it" })).toBeEnabled();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
    routeFetch({ "GET /api/apps/templates": () => jsonResponse(TPLS), "GET /api/agents": () => jsonResponse(AGENTS), "POST /api/apps": () => jsonResponse({ id: "n2" }) });
    const created = vi.fn();
    render(<SimpleCreate onCreated={created} />);
    await user.type(screen.getAllByLabelText("What should the app do?")[1], "y");
    await user.click(screen.getAllByRole("button", { name: "Build it" })[1]);
    await waitFor(() => expect(created).toHaveBeenCalledWith("n2"));
  });
});

describe("AppsSettings", () => {
  const CFG = { enabled: true, auto_apply: false, allow_server_apps: false, preview_port: 8767, max_apps: 50, max_files: 300, max_file_kb: 512, max_total_mb: 20, keep_versions: 50, key_rate_per_min: 60, git_name: "", git_email: "" };

  it("loads, edits and saves the settings, warning when server apps are allowed", async () => {
    const { calls } = routeFetch({ "GET /api/apps/config": () => jsonResponse(CFG), "PUT /api/apps/config": (init: any) => jsonResponse(JSON.parse(init.body)) });
    const user = userEvent.setup();
    render(<AppsSettings />);
    await screen.findByText("Apps are on");
    await user.click(screen.getByLabelText(/Apply the builder/));
    await user.click(screen.getByLabelText(/Allow server apps to run/));
    expect(screen.getByRole("note")).toHaveTextContent("open network");
    await user.clear(screen.getByLabelText("Most apps")); await user.type(screen.getByLabelText("Most apps"), "7");
    await user.type(screen.getByLabelText("Git name"), "Ada"); await user.type(screen.getByLabelText("Git email"), "ada@example.com");
    await user.click(screen.getByLabelText("Apps are on"));
    await user.click(screen.getByRole("button", { name: "Save apps settings" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ enabled: false, auto_apply: true, allow_server_apps: true, max_apps: 7, git_name: "Ada", git_email: "ada@example.com" });
  });

  it("shows load and save errors", async () => {
    routeFetch({ "GET /api/apps/config": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    const { unmount } = render(<AppsSettings />);
    expect(await screen.findByRole("status")).toHaveTextContent("could not be loaded: down");
    unmount();
    routeFetch({ "GET /api/apps/config": () => jsonResponse(CFG), "PUT /api/apps/config": () => jsonResponse({ detail: "max_apps must be a whole number from 1 to 500" }, { status: 422 }) });
    render(<AppsSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Save apps settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("max_apps must be");
  });
});

describe("small gaps", () => {
  it("lets you change the remote branch and close the push dialog with Escape", async () => {
    const { calls } = routeFetch({
      "GET /api/apps/a1/git": () => jsonResponse({ initialised: true, remote: "https://x.test/a.git", branch: "main", changes: [], has_token: false }), "GET /api/apps/a1/git/log": () => jsonResponse([]),
      "PUT /api/apps/a1/git/remote": () => jsonResponse({}),
    });
    const user = userEvent.setup();
    render(<GitPanel appId="a1" refreshKey={0} />);
    const branch = await screen.findByLabelText("Remote branch");
    await user.clear(branch); await user.type(branch, "dev");
    await user.click(screen.getByRole("button", { name: "Save remote" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ branch: "dev" });
    await user.click(screen.getByRole("button", { name: "Push" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes the new-key dialog with Escape", async () => {
    settingsRoutes({ "GET /api/apps/a1/key": () => jsonResponse({ exists: false, agents: ["coder"], note: "x" }), "POST /api/apps/a1/key/rotate": () => jsonResponse({ key: "asx_x", id: "k", prefix: "p", agents: ["coder"] }) });
    const user = userEvent.setup();
    render(<SettingsPanel app={APP} onChanged={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "Make a key to use outside Agent Studio" }));
    await screen.findByRole("dialog");
    await user.click(screen.getByLabelText("New app key"));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
