import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, routeFetch, sseResponse } from "../helpers/http";

vi.mock("@/components/canvas/CodeEditor", () => ({
  default: ({ value, onChange, label }: any) => <textarea aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />,
}));

import FilePanel from "@/components/apps/FilePanel";
import PreviewPanel from "@/components/apps/PreviewPanel";
import BuilderPanel from "@/components/apps/BuilderPanel";
import { ChangesPanel, Diff, ExportPanel, VersionsPanel } from "@/components/apps/AppPanels";
import type { AppRow } from "@/lib/apps";

const APP: AppRow = { id: "a1", name: "Demo", slug: "demo", description: "", template: "static-chat", type: "static", mode: "advanced", session_id: "", version: 1, git: {}, agents: [], pending: 0, files: 3, size: 100, created: 1, updated: 1 };
const FILES = [{ path: "public/index.html", size: 20, binary: false }, { path: "public/logo.png", size: 5, binary: true }, { path: "app.json", size: 30, binary: false }, { path: "README.md", size: 8, binary: false }];
const file = (path: string, content: string | null, binary = false) => jsonResponse({ path, content, size: content?.length ?? 5, binary });

describe("FilePanel", () => {
  it("opens index.html, edits it, saves, and tells you when it is unsaved", async () => {
    const { calls } = routeFetch({ "GET /api/apps/a1/file": (_i, u) => file(u.searchParams.get("path")!, u.searchParams.get("path") === "public/index.html" ? "<p>hi</p>" : "text"), "PUT /api/apps/a1/file": () => jsonResponse({}) });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<FilePanel appId="a1" files={FILES} onChanged={onChanged} refreshKey={0} />);
    expect(await screen.findByTestId("open-file")).toHaveTextContent("public/index.html");
    const editor = screen.getByLabelText("Edit public/index.html") as HTMLTextAreaElement;
    expect(editor.value).toBe("<p>hi</p>");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.type(editor, "!");
    expect(screen.getByText("unsaved")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ path: "public/index.html", content: "<p>hi</p>!" });
    expect(screen.queryByText("unsaved")).toBeNull();
  });

  it("shows folders, collapses them, and opens another file (asking first when there are unsaved changes)", async () => {
    routeFetch({ "GET /api/apps/a1/file": (_i, u) => file(u.searchParams.get("path")!, "body of " + u.searchParams.get("path")) });
    const user = userEvent.setup();
    render(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={0} />);
    await screen.findByTestId("open-file");
    await user.click(screen.getByRole("button", { name: /README.md/ }));
    expect(await screen.findByDisplayValue("body of README.md")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Edit README.md"), "x");
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: /app.json/ }));
    expect(screen.getByTestId("open-file")).toHaveTextContent("README.md");
    await user.click(screen.getByRole("button", { name: /app.json/ }));
    expect(await screen.findByDisplayValue("body of app.json")).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: /public/ }));
    expect(screen.queryByRole("button", { name: /index.html/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: /public/ }));
    expect(screen.getByRole("button", { name: /index.html/ })).toBeInTheDocument();
  });

  it("explains a file that is not text and has no delete for app.json", async () => {
    routeFetch({ "GET /api/apps/a1/file": (_i, u) => (u.searchParams.get("path") === "public/logo.png" ? file("public/logo.png", null, true) : file("app.json", "{}")) });
    const user = userEvent.setup();
    render(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={0} />);
    await screen.findByTestId("open-file");
    await user.click(screen.getByRole("button", { name: /logo.png/ }));
    expect(await screen.findByRole("note")).toHaveTextContent("not a text file");
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /app.json/ }));
    await screen.findByDisplayValue("{}");
    expect(screen.queryByRole("button", { name: /Delete app.json/ })).toBeNull();
  });

  it("creates a file, deletes one after a confirmation, and shows errors", async () => {
    let fail = false;
    const { calls } = routeFetch({
      "GET /api/apps/a1/file": (_i, u) => file(u.searchParams.get("path")!, "text"),
      "PUT /api/apps/a1/file": () => (fail ? jsonResponse({ detail: "that file path is not allowed" }, { status: 422 }) : jsonResponse({})),
      "DELETE /api/apps/a1/file": () => (fail ? jsonResponse({ detail: "no such file" }, { status: 404 }) : jsonResponse({ deleted: "x" })),
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<FilePanel appId="a1" files={FILES} onChanged={onChanged} refreshKey={0} />);
    await screen.findByTestId("open-file");
    await user.click(screen.getByRole("button", { name: "New file" }));
    await user.type(screen.getByLabelText("New file path"), "public/about.html");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.body.path === "public/about.html")).toBe(true));
    expect(await screen.findByTestId("open-file")).toHaveTextContent("public/about.html");
    fail = true;
    await user.click(screen.getByRole("button", { name: "New file" }));
    await user.type(screen.getByLabelText("New file path"), "../bad{Enter}");
    expect(await screen.findByText(/not allowed/)).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Delete public/about.html" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(screen.getByText("Pick a file to read or edit it.")).toBeInTheDocument();
    fail = true;
    await user.click(screen.getByRole("button", { name: /README.md/ }));
    await screen.findByTestId("open-file");
    await user.click(screen.getByRole("button", { name: "Delete README.md" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such file");
    await user.click(screen.getByRole("button", { name: "New file" }));
    await user.type(screen.getByLabelText("New file path"), "x.txt");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/not allowed/)).toBeInTheDocument();
  });

  it("refreshes an open file when it changes elsewhere, but never over your typing, and shows load errors", async () => {
    let body = "one";
    routeFetch({ "GET /api/apps/a1/file": (_i, u) => file(u.searchParams.get("path")!, body) });
    const user = userEvent.setup();
    const { rerender } = render(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={0} />);
    await screen.findByDisplayValue("one");
    body = "two";
    rerender(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={1} />);
    await screen.findByDisplayValue("two");
    await user.type(screen.getByLabelText("Edit public/index.html"), "!");
    body = "three";
    rerender(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={2} />);
    await waitFor(() => expect(screen.getByLabelText("Edit public/index.html")).toHaveValue("two!"));
    rerender(<FilePanel appId="a1" files={FILES.filter((f) => f.path !== "public/index.html")} onChanged={() => {}} refreshKey={3} />);
    await waitFor(() => expect(screen.queryByTestId("open-file")).toBeNull());
    routeFetch({ "GET /api/apps/a1/file": () => jsonResponse({ detail: "gone" }, { status: 404 }) });
    render(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={0} />);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("gone");
  });
});

describe("PreviewPanel", () => {
  it("starts a page by itself and shows it in a sandboxed frame", async () => {
    const { calls } = routeFetch({ "POST /api/apps/a1/run": () => jsonResponse({ kind: "static", status: "ready", url: "http://127.0.0.1:8767/apps-run/a1/", detail: "Static apps have nothing to start." }) });
    const user = userEvent.setup();
    const { rerender } = render(<PreviewPanel app={APP} refreshKey={0} />);
    const frame = (await screen.findByTitle("App preview")) as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.src).toBe("http://127.0.0.1:8767/apps-run/a1/");
    expect(screen.getByTestId("run-status")).toHaveTextContent("ready");
    expect(screen.getByRole("link", { name: "Open in a new tab" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("button", { name: "Show logs" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reload" }));
    rerender(<PreviewPanel app={APP} refreshKey={1} />);
    expect(calls.filter((c) => c.url.pathname.endsWith("/run"))).toHaveLength(1);
  });

  it("runs a server app, stops it and shows logs", async () => {
    const server = { ...APP, type: "node" as const };
    let status = "stopped";
    routeFetch({
      "GET /api/apps/a1/status": () => jsonResponse({ kind: "node", status }),
      "POST /api/apps/a1/run": () => { status = "running"; return jsonResponse({ kind: "node", status: "running", url: "http://127.0.0.1:18001/", detail: "Running in a container" }); },
      "POST /api/apps/a1/stop": () => jsonResponse({ status: "stopped" }),
      "GET /api/apps/a1/logs": () => jsonResponse({ logs: "listening on 8080" }),
    });
    const user = userEvent.setup();
    render(<PreviewPanel app={server} refreshKey={0} />);
    expect(await screen.findByText("Press Run to start this app in a container.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByTitle("App preview")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show logs" }));
    expect(await screen.findByLabelText("App logs")).toHaveTextContent("listening on 8080");
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByTestId("run-status")).toHaveTextContent("stopped"));
  });

  it("explains a server app that is not allowed to run, and other errors", async () => {
    const server = { ...APP, type: "python" as const };
    routeFetch({ "GET /api/apps/a1/status": () => jsonResponse({ kind: "python", status: "stopped" }), "POST /api/apps/a1/run": () => jsonResponse({ detail: "Server apps run in a container. Allow that in Settings > Apps first." }, { status: 422 }),
      "GET /api/apps/a1/logs": () => jsonResponse({ detail: "docker is gone" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<PreviewPanel app={server} refreshKey={0} />);
    await user.click(await screen.findByRole("button", { name: "Run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Allow that in Settings > Apps");
    expect(screen.getByRole("link", { name: "Open Settings" })).toHaveAttribute("href", "/settings/");
    await user.click(screen.getByRole("button", { name: "Show logs" }));
    expect(await screen.findByText(/docker is gone/)).toBeInTheDocument();
    routeFetch({ "GET /api/apps/a1/status": () => jsonResponse({ detail: "nope" }, { status: 500 }) });
    render(<PreviewPanel app={server} refreshKey={0} />);
    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
  });

  it("starts failing static pages gracefully, stops errors, and lets you check the app and turn on API access", async () => {
    let ok = true;
    routeFetch({
      "POST /api/apps/a1/run": () => jsonResponse({ detail: "the preview server did not start" }, { status: 503 }),
      "GET /api/apps/a1/check": () => jsonResponse({ ok: ok, problems: ok ? [] : [{ level: "warn", text: "the agent 'coder' has API access switched off" }, { level: "error", text: "no entry" }, { level: "info", text: "fyi" }], agents: ok ? [] : [{ id: "coder", exists: true, api_access: false }, { id: "ghost", exists: false, api_access: false }] }),
      "PUT /api/agents/coder": () => { ok = true; return jsonResponse({}); },
    });
    const user = userEvent.setup();
    render(<PreviewPanel app={APP} refreshKey={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("did not start");
    await user.click(screen.getByRole("button", { name: "Check the app" }));
    expect(await screen.findByText("No problems found.")).toBeInTheDocument();
    ok = false;
    await user.click(screen.getByRole("button", { name: "Check the app" }));
    const list = await screen.findByRole("list", { name: "Check results" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    await user.click(screen.getByRole("button", { name: "Turn on API access for coder" }));
    expect(await screen.findByText("No problems found.")).toBeInTheDocument();
  });

  it("reports failing check, stop and API-access calls", async () => {
    routeFetch({
      "POST /api/apps/a1/run": () => jsonResponse({ kind: "static", status: "ready", url: "http://127.0.0.1:8767/x/" }),
      "GET /api/apps/a1/check": () => jsonResponse({ ok: false, problems: [], agents: [{ id: "coder", exists: true, api_access: false }] }),
      "PUT /api/agents/coder": () => jsonResponse({ detail: "no such agent" }, { status: 404 }),
    });
    const user = userEvent.setup();
    render(<PreviewPanel app={APP} refreshKey={0} />);
    await screen.findByTitle("App preview");
    await user.click(screen.getByRole("button", { name: "Check the app" }));
    await user.click(await screen.findByRole("button", { name: "Turn on API access for coder" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such agent");
    routeFetch({ "POST /api/apps/a1/run": () => jsonResponse({ kind: "static", status: "ready", url: "http://x/" }), "GET /api/apps/a1/check": () => jsonResponse({ detail: "broken" }, { status: 500 }) });
    render(<PreviewPanel app={{ ...APP, id: "a1" }} refreshKey={0} />);
    await user.click((await screen.findAllByRole("button", { name: "Check the app" }))[1]);
    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("broken"))).toBe(true);
  });
});

describe("BuilderPanel", () => {
  const MODEL = { saved: { mode: "best", provider: "", model: "" }, effective: { provider: "claude", label: "Claude", kind: "anthropic", model: "big", source: "best" } };
  const OPTIONS = { options: [{ provider: "claude", label: "Claude", kind: "anthropic", model: "big", ready: true }, { provider: "mock", label: "Mock", kind: "mock", model: "m", ready: true }, { provider: "off", label: "Off", kind: "x", model: "z", ready: false }] };
  const base = (over: Record<string, any> = {}) => ({ "GET /api/apps/a1/model": () => jsonResponse(MODEL), "GET /api/apps/models": () => jsonResponse(OPTIONS), "GET /api/apps/a1/plan": () => jsonResponse({ plan: null }),
    "POST /api/apps/a1/verify/reset": () => jsonResponse({ round: 0 }), ...over });
  const ev = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;

  it("tells you when no provider is on", async () => {
    routeFetch(base({ "GET /api/apps/a1/model": () => jsonResponse({ ...MODEL, effective: { provider: "", label: "", kind: "", model: "", source: "none" } }), "GET /api/apps/models": () => jsonResponse({ options: [] }) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No model provider is switched on");
    expect(screen.getByLabelText("Message to the builder")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    routeFetch(base({ "GET /api/apps/a1/model": () => jsonResponse({ detail: "x" }, { status: 500 }) }));
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  });

  it("sends a message and shows the streamed answer, the work in progress and refreshes the app", async () => {
    const onChanged = vi.fn();
    const { calls } = routeFetch({
      ...base({ "PUT /api/apps/a1/model": (init: any) => jsonResponse({ saved: JSON.parse(init.body), effective: { provider: "mock", label: "Mock", kind: "mock", model: "m", source: "custom" } }) }),
      "POST /api/chat/stream": () => sseResponse([ev({ type: "session", session_id: "s9" }), ev({ type: "tool_call", name: "app_write_file" }), ev({ type: "app", action: "applied" }), ev({ type: "text", delta: "Done " }), ev({ type: "text", delta: "now." }), ev({ type: "done" })]),
    });
    const user = userEvent.setup();
    render(<BuilderPanel app={APP} onChanged={onChanged} settleMs={0} />);
    await waitFor(() => expect((screen.getByLabelText("Builder model") as HTMLSelectElement).value).toBe("best"));
    await user.selectOptions(screen.getByLabelText("Builder model"), "mock|m");
    await waitFor(() => expect((screen.getByLabelText("Builder model") as HTMLSelectElement).value).toBe("mock|m"));
    await user.type(screen.getByLabelText("Message to the builder"), "Make it blue{Enter}");
    const log = screen.getByRole("log", { name: "Builder conversation" });
    await waitFor(() => expect(log).toHaveTextContent("Done now."));
    expect(log).toHaveTextContent("Make it blue");
    expect(onChanged).toHaveBeenCalled();
    const body = calls.find((c) => c.url.pathname === "/api/chat/stream")!.body;
    expect(body).toMatchObject({ provider: "mock", agent: "app_builder", model: "m" });
    expect(body.message).toContain('app_id a1');
    expect(body.session_id).toBeUndefined();
    await user.type(screen.getByLabelText("Message to the builder"), "again");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(calls.filter((c) => c.url.pathname === "/api/chat/stream")).toHaveLength(2));
    expect(calls.filter((c) => c.url.pathname === "/api/chat/stream")[1].body.session_id).toBe("s9");
  });

  it("shows refusals, errors and failed requests as messages", async () => {
    let n = 0;
    routeFetch({
      ...base(),
      "POST /api/chat/stream": () => { n++; return n === 1 ? sseResponse([ev({ type: "refused", detail: { message: "over the cap" } })]) : n === 2 ? sseResponse([ev({ type: "error", title: "The model failed" })]) : n === 3 ? sseResponse([ev({ type: "refused", detail: {} }), ev({ type: "error", message: "m" })]) : jsonResponse({ detail: "down" }, { status: 500 }); },
    });
    const user = userEvent.setup();
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    const box = await screen.findByLabelText("Message to the builder");
    for (const [i, expected] of [[0, "over the cap"], [1, "The model failed"], [2, "The request was refused."], [3, "down"]] as const) {
      await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeDisabled());
      await user.type(box, `go ${i}`);
      await user.click(screen.getByRole("button", { name: "Send" }));
      await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes(expected))).toBe(true));
    }
  });

  it("stops a running answer", async () => {
    routeFetch({
      ...base(),
      "POST /api/chat/stream": (init) => new Promise<Response>((_res, rej) => { (init?.signal as AbortSignal).addEventListener("abort", () => rej(new DOMException("aborted", "AbortError"))); }),
    });
    const user = userEvent.setup();
    render(<BuilderPanel app={APP} onChanged={() => {}} />);
    await user.type(await screen.findByLabelText("Message to the builder"), "long job");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("starts by itself with the first prompt and its attachments", async () => {
    const started = vi.fn();
    const { calls } = routeFetch({ ...base(), "POST /api/chat/stream": () => sseResponse([ev({ type: "text", delta: "ok" })]) });
    const { rerender } = render(<BuilderPanel app={APP} onChanged={() => {}} start={{ prompt: "A reply helper", attachments: ["spec.md"], failed: ["big.pdf: too large"] }} onStarted={started} />);
    await waitFor(() => expect(started).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("note")).toHaveTextContent("big.pdf: too large");
    const sent = calls.find((c) => c.url.pathname === "/api/chat/stream")!.body.message as string;
    expect(sent).toContain("A reply helper");
    expect(sent).toContain("spec.md");
    rerender(<BuilderPanel app={APP} onChanged={() => {}} start={{ prompt: "A reply helper", attachments: [] }} onStarted={started} />);
    expect(started).toHaveBeenCalledTimes(1);
    routeFetch({ ...base(), "POST /api/chat/stream": () => sseResponse([]) });
    render(<BuilderPanel app={{ ...APP, session_id: "old" }} onChanged={() => {}} start={{ prompt: "go", attachments: [] }} />);
  });
});

describe("Diff", () => {
  it("marks added and removed lines and cuts long diffs", () => {
    render(<Diff before={"a\nb\nc"} after={"a\nB\nc"} />);
    expect(screen.getByLabelText("Changes in this file")).toHaveTextContent("- b");
    expect(screen.getByLabelText("Changes in this file")).toHaveTextContent("+ B");
    const long = Array.from({ length: 200 }, (_, i) => `line ${i}`).join("\n");
    render(<Diff before={null} after={long} />);
    expect(screen.getAllByLabelText("Changes in this file")[1]).toHaveTextContent("more lines");
    render(<Diff before={null} after={null} />);
  });
});

const PROPOSAL = (over: Record<string, unknown> = {}) => ({ id: "p1", app_id: "a1", author: "agent:App Builder", note: "Narrow the page", status: "pending", created: Date.now() / 1000 - 60, resolved: 0,
  changes: [{ op: "patch", path: "public/style.css", old: "a\nb", new: "a\nB", fits: true }, { op: "write", path: "public/new.html", old: null, new: "<p>x</p>", fits: true }, { op: "write", path: "public/index.html", old: "o", new: "n", fits: true }, { op: "delete", path: "old.txt", old: "x", new: null, fits: false }], ...over });

describe("ChangesPanel", () => {
  it("shows each suggested change as a diff and accepts or rejects it", async () => {
    let list = [PROPOSAL(), PROPOSAL({ id: "p2", note: "", changes: [{ op: "write", path: "z.txt", old: null, new: "z", fits: true }] })];
    const { calls } = routeFetch({
      "GET /api/apps/a1/proposals": () => jsonResponse(list),
      "POST /api/apps/a1/proposals/p1/accept": () => { list = list.filter((p) => p.id !== "p1"); return jsonResponse({ version: 2 }); },
      "POST /api/apps/a1/proposals/p2/reject": () => { list = []; return jsonResponse({ rejected: true }); },
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<ChangesPanel appId="a1" onChanged={onChanged} refreshKey={0} />);
    expect(await screen.findByText("Narrow the page")).toBeInTheDocument();
    expect(screen.getAllByText("new file")).toHaveLength(2);
    expect(screen.getByText("edit")).toBeInTheDocument();
    expect(screen.getByText("replace")).toBeInTheDocument();
    expect(screen.getByText("delete")).toBeInTheDocument();
    expect(screen.getByText("The file will be removed.")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("no longer fits");
    await user.click(screen.getByRole("button", { name: "Accept: Narrow the page" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.some((c) => c.url.pathname.endsWith("/p1/accept"))).toBe(true);
    await user.click(await screen.findByRole("button", { name: "Reject: z.txt" }));
    expect(await screen.findByText(/No changes are waiting/)).toBeInTheDocument();
  });

  it("accepts or rejects everything, reports changes that no longer fit and errors", async () => {
    let list = [PROPOSAL(), PROPOSAL({ id: "p2" })];
    let reply: any = { accepted: 1, stale: [{ id: "p2", reason: "does not fit" }] };
    routeFetch({
      "GET /api/apps/a1/proposals": () => jsonResponse(list),
      "POST /api/apps/a1/accept-all": () => { list = []; return jsonResponse(reply); },
      "POST /api/apps/a1/reject-all": () => jsonResponse({ rejected: 2 }),
      "POST /api/apps/a1/proposals/p1/accept": () => jsonResponse({ detail: "this change no longer fits" }, { status: 422 }),
    });
    const user = userEvent.setup();
    render(<ChangesPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await user.click(await screen.findAllByRole("button", { name: /^Accept:/ }).then((b) => b[0]));
    expect(await screen.findByRole("alert")).toHaveTextContent("no longer fits");
    await user.click(screen.getByRole("button", { name: "Reject all" }));
    await user.click(screen.getByRole("button", { name: "Accept all" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("1 change(s) no longer fit: does not fit");
    list = [PROPOSAL(), PROPOSAL({ id: "p2" })];
    reply = { accepted: 2, stale: [] };
    routeFetch({ "GET /api/apps/a1/proposals": () => jsonResponse(list), "POST /api/apps/a1/accept-all": () => jsonResponse(reply) });
    render(<ChangesPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    routeFetch({ "GET /api/apps/a1/proposals": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<ChangesPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent === "down")).toBe(true));
  });
});

describe("VersionsPanel", () => {
  const V = [{ version: 3, author: "user", note: "", created: Date.now() / 1000, size: 10 }, { version: 2, author: "agent:App Builder", note: "added about", created: Date.now() / 1000 - 3600, size: 9 }, { version: 1, author: "restore", note: "restored version 0", created: 1, size: 8 }];

  it("lists versions, compares one with now and restores after a confirmation", async () => {
    const { calls } = routeFetch({
      "GET /api/apps/a1/versions": () => jsonResponse(V),
      "GET /api/apps/a1/versions/2/diff": () => jsonResponse([{ path: "a.txt", status: "changed", old: "x", new: "y" }, { path: "b.png", status: "added", old: null, new: null }, { path: "c.txt", status: "removed", old: "z", new: null }]),
      "GET /api/apps/a1/versions/1/diff": () => jsonResponse([]),
      "POST /api/apps/a1/restore": () => jsonResponse({ version: 4 }),
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<VersionsPanel appId="a1" onChanged={onChanged} refreshKey={0} />);
    expect(await screen.findByText(/added about/)).toBeInTheDocument();
    expect(screen.getByText("now")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Compare version 2" }));
    const diff = await screen.findByTestId("version-diff");
    expect(diff).toHaveTextContent("changed");
    expect(diff).toHaveTextContent("added since");
    expect(diff).toHaveTextContent("removed since");
    await user.click(screen.getByRole("button", { name: "Compare version 1" }));
    expect(await screen.findByText("No differences.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Restore version 2" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Restore version 2" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ version: 2 });
  });

  it("saves a version on purpose and reports errors", async () => {
    let fail = false;
    const { calls } = routeFetch({
      "GET /api/apps/a1/versions": () => (fail ? jsonResponse({ detail: "no such app" }, { status: 404 }) : jsonResponse(V)),
      "POST /api/apps/a1/versions": () => (fail ? jsonResponse({ detail: "nope" }, { status: 422 }) : jsonResponse({ version: 4 })),
      "GET /api/apps/a1/versions/2/diff": () => jsonResponse({ detail: "gone" }, { status: 422 }),
      "POST /api/apps/a1/restore": () => jsonResponse({ detail: "cannot" }, { status: 422 }),
    });
    const user = userEvent.setup();
    render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await screen.findByText(/added about/);
    await user.click(screen.getByRole("button", { name: "Save a version" }));
    await user.type(screen.getByLabelText("Version note"), "before redesign");
    await user.click(screen.getByRole("button", { name: "Save a version" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body.note)).toEqual(["saved on purpose", "before redesign"]);
    await user.click(screen.getByRole("button", { name: "Compare version 2" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("gone");
    await user.click(screen.getByRole("button", { name: "Restore version 2" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Restore" }));
    expect(await screen.findByText("cannot")).toBeInTheDocument();
    fail = true;
    await user.click(screen.getByRole("button", { name: "Save a version" }));
    expect(await screen.findByText("nope")).toBeInTheDocument();
  });

  it("shows a load error", async () => {
    routeFetch({ "GET /api/apps/a1/versions": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});

describe("ExportPanel", () => {
  const PLAN = { app: "Demo", image: "demo:0.1.0", steps: [{ title: "Build the image", command: "docker build -t demo:0.1.0 .", where: "in the app folder" }], notes: ["Agent Studio does not deploy apps itself."] };

  it("downloads a zip and shows how to host the app elsewhere", async () => {
    const create = vi.fn(() => "blob:x"), revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    routeFetch({ "GET /api/apps/a1/deploy-plan": () => jsonResponse(PLAN), "GET /api/apps/a1/export.zip": () => new Response(new Blob(["zip"]), { status: 200, headers: { "Content-Type": "application/zip" } }) });
    const user = userEvent.setup();
    render(<ExportPanel appId="a1" slug="demo" />);
    expect(await screen.findByText(/docker build -t demo:0.1.0/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("does not deploy");
    await user.click(screen.getByRole("button", { name: "Download as a zip" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(create).toHaveBeenCalled();
    expect(revoke).toHaveBeenCalledWith("blob:x");
  });

  it("explains why an export was refused", async () => {
    routeFetch({ "GET /api/apps/a1/deploy-plan": () => jsonResponse({ detail: "down" }, { status: 500 }), "GET /api/apps/a1/export.zip": () => jsonResponse({ detail: "not exported: a file looks like it has a secret" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<ExportPanel appId="a1" slug="demo" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
    await user.click(screen.getByRole("button", { name: "Download as a zip" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("not exported"));
    routeFetch({ "GET /api/apps/a1/deploy-plan": () => jsonResponse(PLAN), "GET /api/apps/a1/export.zip": () => new Response("<html>", { status: 500 }) });
    render(<ExportPanel appId="a1" slug="demo" />);
    await user.click((await screen.findAllByRole("button", { name: "Download as a zip" }))[1]);
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent?.includes("HTTP 500"))).toBe(true));
  });
});

describe("small gaps", () => {
  it("reports a failed save, and a new-file dialog can be cancelled or closed with Escape", async () => {
    routeFetch({ "GET /api/apps/a1/file": (_i, u) => file(u.searchParams.get("path")!, "text"), "PUT /api/apps/a1/file": () => jsonResponse({ detail: "disk full" }, { status: 422 }) });
    const user = userEvent.setup();
    render(<FilePanel appId="a1" files={FILES} onChanged={() => {}} refreshKey={0} />);
    await user.type(await screen.findByLabelText("Edit public/index.html"), "!");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("disk full");
    await user.click(screen.getByRole("button", { name: "New file" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "New file" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Delete public/index.html" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes the restore dialog with Escape", async () => {
    routeFetch({ "GET /api/apps/a1/versions": () => jsonResponse([{ version: 2, author: "user", note: "", created: 1, size: 1 }, { version: 1, author: "user", note: "", created: 1, size: 1 }]) });
    const user = userEvent.setup();
    render(<VersionsPanel appId="a1" onChanged={() => {}} refreshKey={0} />);
    await user.click(await screen.findByRole("button", { name: "Restore version 1" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("first file", () => {
  it("opens the first text file when there is no public/index.html", async () => {
    routeFetch({ "GET /api/apps/a1/file": (_i, u) => file(u.searchParams.get("path")!, "body of " + u.searchParams.get("path")) });
    render(<FilePanel appId="a1" files={[{ path: "a.png", size: 1, binary: true }, { path: "notes.txt", size: 2, binary: false }]} onChanged={() => {}} refreshKey={0} />);
    expect(await screen.findByTestId("open-file")).toHaveTextContent("notes.txt");
    routeFetch({});
    render(<FilePanel appId="a1" files={[{ path: "a.png", size: 1, binary: true }]} onChanged={() => {}} refreshKey={0} />);
    expect(screen.getAllByText("Pick a file to read or edit it.")).toHaveLength(1);
  });
});
