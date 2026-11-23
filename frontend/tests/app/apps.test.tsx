import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

const push = vi.fn(), replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace }), useSearchParams: () => new URLSearchParams(search), usePathname: () => "/apps/" }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("@/components/apps/SimpleCreate", () => ({ BUILD_KEY: (id: string) => `app-build:${id}`, default: ({ onCreated }: any) => <button onClick={() => onCreated("n1")}>fake simple form</button> }));
vi.mock("@/components/apps/FilePanel", () => ({ default: ({ files, refreshKey }: any) => <div data-testid="files">{files.length} files, refresh {refreshKey}</div> }));
vi.mock("@/components/apps/PreviewPanel", () => ({ default: ({ app }: any) => <div data-testid="preview">preview of {app.name}</div> }));
vi.mock("@/components/apps/StudioPanel", () => ({ default: ({ app, start, onStarted, request, onAsk, tick, consoleTick }: any) => (
  <div data-testid="studio">studio of {app.name} {start ? `starting: ${start.prompt}` : "idle"} {request ? `asked: ${request.text}` : ""} tick {tick} console {consoleTick}
    <button onClick={() => onStarted?.()}>ack</button><button onClick={() => onAsk("hello builder")}>ask</button></div>) }));
vi.mock("@/components/apps/DesignPanel", () => ({ default: ({ onAsk }: any) => <div data-testid="design">design panel<button onClick={() => onAsk("use the table")}>ask from design</button></div> }));
vi.mock("@/components/apps/ConnectPanel", () => ({ default: () => <div data-testid="connect">connect panel</div> }));
vi.mock("@/components/apps/SettingsPanel", () => ({ default: () => <div data-testid="settings">settings panel</div> }));
vi.mock("@/components/apps/GitPanel", () => ({ default: () => <div data-testid="git">git panel</div> }));
vi.mock("@/components/apps/AppPanels", () => ({
  ChangesPanel: () => <div data-testid="changes">changes panel</div>, VersionsPanel: () => <div data-testid="versions">versions panel</div>, ExportPanel: () => <div data-testid="export">export panel</div>,
}));

import AppsPage from "@/app/apps/page";
import AppEditPage from "@/app/apps/edit/page";

const ROW = (id: string, over: Record<string, unknown> = {}) => ({ id, name: `App ${id}`, slug: id, description: `about ${id}`, template: "static-chat", type: "static", mode: "advanced", session_id: "", version: 2,
  git: {}, agents: ["coder"], pending: 0, files: 9, size: 2048, created: 1, updated: Date.now() / 1000 - 600, ...over });
const TPLS = [{ id: "static-chat", label: "Chat page", type: "static", description: "A chat." }, { id: "node-api", label: "Node server", type: "node", description: "A server." }];

beforeEach(() => { push.mockClear(); replace.mockClear(); search = ""; });

describe("Apps library", () => {
  it("lists the apps with search and a kind filter, and opens one", async () => {
    routeFetch({ "GET /api/apps": () => jsonResponse([ROW("a"), ROW("b", { type: "node", mode: "simple", pending: 2, agents: [], git: { remote: "https://x" }, files: 3 }), ROW("c", { description: "" })]) });
    const user = userEvent.setup();
    render(<AppsPage />);
    expect(await screen.findByText("App a")).toBeInTheDocument();
    const card = screen.getByText("App b").closest("[role=listitem]") as HTMLElement;
    expect(card).toHaveTextContent("Node server");
    expect(card).toHaveTextContent("simple");
    expect(card).toHaveTextContent("Calls no agent yet");
    expect(card).toHaveTextContent("2 changes waiting");
    expect(card).toHaveTextContent("· git");
    expect(screen.getAllByText("Calls: coder")).toHaveLength(2);
    expect(screen.getByText("No description")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search apps"), "about a");
    expect(screen.queryByText("App b")).toBeNull();
    expect(screen.getByText("App a")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search apps"));
    await user.selectOptions(screen.getByLabelText("Kind"), "python");
    expect(screen.getByText("No app matches.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Kind"), "node");
    expect(screen.getByText("App b")).toBeInTheDocument();
    expect(screen.queryByText("App a")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Open App b" }));
    expect(push).toHaveBeenLastCalledWith("/apps/edit/?id=b");
    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(push).toHaveBeenLastCalledWith("/apps/edit/?id=b");
  });

  it("starts with an empty state and offers both ways to create an app", async () => {
    routeFetch({ "GET /api/apps": () => jsonResponse([]), "GET /api/apps/templates": () => jsonResponse(TPLS), "GET /api/agents": () => jsonResponse([{ id: "coder", name: "Coder" }, { id: "app_builder", name: "App Builder" }]),
      "POST /api/apps": () => jsonResponse({ id: "n9" }) });
    const user = userEvent.setup();
    render(<AppsPage />);
    expect(await screen.findByTestId("apps-empty")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create the first one" }));
    expect(screen.getByRole("dialog", { name: "New app" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Simple/ }));
    await user.click(screen.getByRole("button", { name: "fake simple form" }));
    expect(push).toHaveBeenLastCalledWith("/apps/edit/?id=n1&build=1");
    await user.click(screen.getByRole("button", { name: "New app" }));
    await user.click(screen.getByRole("button", { name: /Advanced/ }));
    expect(screen.getByRole("button", { name: "Create and open" })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("option", { name: "Coder" })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: "App Builder" })).toBeNull();
    await user.type(screen.getByLabelText("App name"), "Mine");
    await user.click(screen.getByLabelText("Node server"));
    await user.selectOptions(screen.getByLabelText("Agent the app uses"), "coder");
    await user.click(screen.getByRole("button", { name: "Create and open" }));
    await waitFor(() => expect(push).toHaveBeenLastCalledWith("/apps/edit/?id=n9"));
  });

  it("copies and deletes an app, with a confirmation, and shows errors", async () => {
    let rows = [ROW("a")];
    const { calls } = routeFetch({
      "GET /api/apps": () => jsonResponse(rows), "POST /api/apps/a/duplicate": () => { rows = [...rows, ROW("a2")]; return jsonResponse({}); },
      "DELETE /api/apps/a": () => { rows = rows.filter((r) => r.id !== "a"); return jsonResponse({ deleted: true }); }, "DELETE /api/apps/a2": () => jsonResponse({ detail: "no such app" }, { status: 404 }),
    });
    const user = userEvent.setup();
    render(<AppsPage />);
    await screen.findByText("App a");
    await user.click(screen.getByRole("button", { name: "Duplicate App a" }));
    expect(await screen.findByText("App a2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete App a" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Delete App a" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("App a")).toBeNull());
    expect(calls.find((c) => c.method === "DELETE")!.url.search).toBe("?confirm=true");
    await user.click(screen.getByRole("button", { name: "Delete App a2" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such app");
  });

  it("shows load and copy errors", async () => {
    routeFetch({ "GET /api/apps": () => jsonResponse({ detail: "apps are off" }, { status: 422 }) });
    const { unmount } = render(<AppsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("apps are off");
    unmount();
    routeFetch({ "GET /api/apps": () => jsonResponse([ROW("a")]), "POST /api/apps/a/duplicate": () => jsonResponse({ detail: "at most 50 apps" }, { status: 422 }) });
    render(<AppsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Duplicate App a" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 50 apps");
  });

  it("closes the dialogs with Escape and shows errors from the advanced form", async () => {
    routeFetch({ "GET /api/apps": () => jsonResponse([ROW("a")]), "GET /api/apps/templates": () => jsonResponse({ detail: "t" }, { status: 500 }), "GET /api/agents": () => jsonResponse({ detail: "a" }, { status: 500 }), "POST /api/apps": () => jsonResponse({ detail: "no" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<AppsPage />);
    await user.click(await screen.findByRole("button", { name: "New app" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "New app" }));
    await user.click(screen.getByRole("button", { name: /Simple/ }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "New app" }));
    await user.click(screen.getByRole("button", { name: /Advanced/ }));
    await user.type(screen.getByLabelText("App name"), "x");
    await user.click(screen.getByRole("button", { name: "Create and open" }));
    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("no"))).toBe(true);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("App workspace", () => {
  const routes = (over: Record<string, any> = {}) => routeFetch({
    "GET /api/apps/a1": () => jsonResponse(ROW("a1", { name: "Demo" })), "GET /api/apps/a1/files": () => jsonResponse([{ path: "app.json", size: 1, binary: false }, { path: "README.md", size: 1, binary: false }]),
    "GET /api/apps/a1/events": () => sseResponse([": connected\n\n"]), ...over,
  });

  it("asks for an app when none is chosen and shows load errors", async () => {
    routeFetch({});
    const { unmount } = render(<AppEditPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No app was chosen");
    unmount();
    search = "id=a1";
    routeFetch({ "GET /api/apps/a1": () => jsonResponse({ detail: "no such app 'a1'" }, { status: 404 }), "GET /api/apps/a1/files": () => jsonResponse([]), "GET /api/apps/a1/events": () => jsonResponse({}, { status: 404 }) });
    render(<AppEditPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("no such app");
    expect(screen.getByRole("link", { name: "Back to your apps" })).toHaveAttribute("href", "/apps/");
  });

  it("opens an advanced app with the files beside the tabs, and switches panels and modes", async () => {
    search = "id=a1";
    const { calls } = routes({ "PUT /api/apps/a1": (init: any) => jsonResponse(ROW("a1", { name: "Demo", ...JSON.parse(init.body) })) });
    const user = userEvent.setup();
    render(<AppEditPage />);
    expect(await screen.findByTestId("studio")).toHaveTextContent("studio of Demo");
    expect(screen.queryByTestId("files")).toBeNull();
    for (const [tab, id] of [["Changes", "changes"], ["Versions", "versions"], ["Design", "design"], ["Connect", "connect"], ["Settings", "settings"], ["Git", "git"], ["Export", "export"]] as const) {
      await user.click(screen.getByRole("tab", { name: tab }));
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    await user.click(screen.getByRole("tab", { name: "Files" }));
    expect(screen.getByTestId("files")).toHaveTextContent("2 files");
    expect(screen.getByTestId("preview")).toHaveTextContent("preview of Demo");
    await user.click(screen.getByRole("tab", { name: "Build" }));
    expect(screen.queryByTestId("files")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Simple mode" }));
    await waitFor(() => expect(screen.queryByRole("tab", { name: "Git" })).toBeNull());
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ mode: "simple" });
    expect(screen.queryByRole("tab", { name: "Files" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Open in advanced mode" }));
    expect(await screen.findByRole("tab", { name: "Files" })).toBeInTheDocument();
  });

  it("shows a simple app with a builder first and the changes tab only when something waits", async () => {
    search = "id=a1";
    routes({ "GET /api/apps/a1": () => jsonResponse(ROW("a1", { name: "Demo", mode: "simple", pending: 3 })) });
    const user = userEvent.setup();
    render(<AppEditPage />);
    await screen.findByTestId("studio");
    expect(screen.queryByTestId("files")).toBeNull();
    expect(screen.getByRole("tab", { name: "Changes (3)" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Changes (3)" }));
    expect(screen.getByTestId("changes")).toBeInTheDocument();
  });

  it("hides the changes tab of a simple app with nothing waiting", async () => {
    search = "id=a1";
    routes({ "GET /api/apps/a1": () => jsonResponse(ROW("a1", { name: "Demo", mode: "simple", pending: 0 })) });
    render(<AppEditPage />);
    await screen.findByTestId("studio");
    expect(screen.queryByRole("tab", { name: /Changes/ })).toBeNull();
  });

  it("starts the builder with the prompt from the simple form and then clears it", async () => {
    search = "id=a1&build=1";
    sessionStorage.setItem("app-build:a1", JSON.stringify({ prompt: "Make a reply helper", attachments: ["spec.md"] }));
    routes({ "GET /api/apps/a1": () => jsonResponse(ROW("a1", { name: "Demo", mode: "simple" })) });
    const user = userEvent.setup();
    render(<AppEditPage />);
    expect(await screen.findByTestId("studio")).toHaveTextContent("starting: Make a reply helper");
    await user.click(screen.getByRole("button", { name: "ack" }));
    expect(sessionStorage.getItem("app-build:a1")).toBeNull();
    expect(replace).toHaveBeenCalledWith("/apps/edit/?id=a1");
  });

  it("copes with a broken or missing stored prompt and with storage that fails", async () => {
    search = "id=a1&build=1";
    sessionStorage.setItem("app-build:a1", "{broken");
    routes();
    const { unmount } = render(<AppEditPage />);
    expect(await screen.findByTestId("studio")).toHaveTextContent("idle");
    unmount();
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("no storage"); });
    routes();
    render(<AppEditPage />);
    await userEvent.click(await screen.findByRole("button", { name: "ack" }));
    expect(replace).toHaveBeenCalled();
  });

  it("reloads when the server reports a change", async () => {
    search = "id=a1";
    let loads = 0;
    routes({ "GET /api/apps/a1": () => { loads++; return jsonResponse(ROW("a1", { name: "Demo" })); }, "GET /api/apps/a1/events": () => sseResponse(['data: {"type":"files"}\n\n']) });
    render(<AppEditPage />);
    await waitFor(() => expect(loads).toBeGreaterThanOrEqual(2));
  });

  it("does not reload the preview for the preview's own reports, and tells the studio about them", async () => {
    search = "id=a1";
    let loads = 0;
    routes({ "GET /api/apps/a1": () => { loads++; return jsonResponse(ROW("a1", { name: "Demo" })); }, "GET /api/apps/a1/events": () => sseResponse(['data: {"type":"console","errors":1}\n\n']) });
    render(<AppEditPage />);
    await waitFor(() => expect(screen.getByTestId("studio")).toHaveTextContent("console 1"));
    expect(loads).toBe(1);
  });

  it("sends a request from another tab to the builder and goes back to the build view", async () => {
    search = "id=a1";
    routes();
    const user = userEvent.setup();
    render(<AppEditPage />);
    await screen.findByTestId("studio");
    await user.click(screen.getByRole("tab", { name: "Design" }));
    await user.click(screen.getByRole("button", { name: "ask from design" }));
    expect(screen.getByRole("tab", { name: "Build" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("studio")).toHaveTextContent("asked: use the table");
    await user.click(screen.getByRole("button", { name: "ask" }));
    expect(screen.getByTestId("studio")).toHaveTextContent("asked: hello builder");
  });

  it("deletes the app after a confirmation and goes back to the library", async () => {
    search = "id=a1";
    const { calls } = routes({ "DELETE /api/apps/a1": () => jsonResponse({ deleted: true }) });
    const user = userEvent.setup();
    render(<AppEditPage />);
    await user.click(await screen.findByRole("button", { name: "Delete this app" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Delete this app" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/apps/"));
  });

  it("reports failures to delete or change the mode", async () => {
    search = "id=a1";
    routes({ "DELETE /api/apps/a1": () => jsonResponse({ detail: "cannot delete" }, { status: 422 }), "PUT /api/apps/a1": () => jsonResponse({ detail: "mode must be simple or advanced" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<AppEditPage />);
    await user.click(await screen.findByRole("button", { name: "Simple mode" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("mode must be");
    await user.click(screen.getByRole("button", { name: "Delete this app" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("cannot delete"));
  });
});
