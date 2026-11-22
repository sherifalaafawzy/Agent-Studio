import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Page from "@/app/artifacts/page";
import { API_BASE } from "@/lib/api";
import type { Artifact } from "@/lib/artifacts";
import { jsonResponse, routeFetch, type Call } from "../helpers/http";

const nav = vi.hoisted(() => ({ search: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/artifacts/", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ replace: nav.replace, push: vi.fn() }) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

const copy = (storage = "local", status: Artifact["copies"][number]["status"] = "synced", detail = "") => ({ storage, status, detail, size: 10, updated: 0 });
const A = (id: string, over: Partial<Artifact> = {}): Artifact => ({ id, name: `${id}.md`, path: "", type: "document", mime: "text/markdown", preview: "markdown", size: 2048, sha256: "f".repeat(64), version: 1, created: Date.now() / 1000 - 120,
  agent: "engineer", session_id: "s1", trace_id: "t1", workspace: "", source: "tool", tags: ["q3"], summary: "Revenue grew in EMEA", secrets: false, copies: [copy()], ...over });
const REPORT = A("aaaaaaaaaaaa", { name: "q3-report.md" });
const PLOT = A("bbbbbbbbbbbb", { name: "plot.png", type: "image", mime: "image/png", preview: "image", summary: "" });
const DATA = A("cccccccccccc", { name: "users.csv", type: "data", mime: "text/csv", preview: "csv", copies: [copy(), copy("team", "pending")] });
const FACETS = { agents: [{ value: "engineer", count: 3 }], types: [{ value: "document", count: 1 }, { value: "image", count: 1 }, { value: "data", count: 1 }, { value: "weird", count: 0 }], sources: [{ value: "tool", count: 3 }, { value: "odd", count: 0 }],
  storages: [{ value: "local", count: 3 }], total: 3, bytes: 6144 };

let toasts: { text: string; tone: string }[] = [];
const onToast = (e: Event) => toasts.push((e as CustomEvent).detail);
beforeEach(() => { nav.search = ""; nav.replace.mockClear(); toasts = []; window.addEventListener("as:toast", onToast); });
afterEach(() => { window.removeEventListener("as:toast", onToast); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function setup(items: Artifact[] = [REPORT, PLOT, DATA], extra: Record<string, any> = {}, total?: number) {
  return routeFetch({
    "GET /api/artifacts": () => jsonResponse({ total: total ?? items.length, items }), "GET /api/artifacts/facets": () => jsonResponse(FACETS),
    "GET /api/artifacts/aaaaaaaaaaaa/preview": () => jsonResponse({ kind: "markdown", text: "# Q3\n\nRevenue grew.", truncated: false }),
    "GET /api/artifacts/aaaaaaaaaaaa": () => jsonResponse(REPORT),
    ...extra,
  });
}
const lastList = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.url.pathname === "/api/artifacts").at(-1)!.url.searchParams;

describe("the artifacts list", () => {
  it("shows every file with its kind, size, origin and state, and counts by kind", async () => {
    setup();
    render(<Page />);
    const tiles = await screen.findAllByTestId("tile");
    expect(tiles).toHaveLength(3);
    expect(within(tiles[0]).getByText("q3-report.md")).toBeInTheDocument();
    expect(within(tiles[0]).getByText(/Documents · 2.0 KB · 2 min ago/)).toBeInTheDocument();
    expect(within(tiles[0]).getByText("Revenue grew in EMEA")).toBeInTheDocument();
    expect(tiles[1].querySelector("img")).toHaveAttribute("src", `${API_BASE}/api/artifacts/bbbbbbbbbbbb/content`);
    expect(within(tiles[2]).getByText("copying…")).toBeInTheDocument();
    expect(screen.getByText("3 files · 6.0 KB in all")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Documents · 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "weird · 0" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Files (3)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Saved by an agent (3)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "odd (0)" })).toBeInTheDocument();
  });

  it("explains an empty store and an empty search", async () => {
    setup([], { "GET /api/artifacts/facets": () => jsonResponse({ ...FACETS, total: 0, bytes: 0 }) });
    const { unmount } = render(<Page />);
    expect(await screen.findByText("No artifacts yet")).toBeInTheDocument();
    unmount();
    routeFetch({ "GET /api/artifacts": () => jsonResponse({ total: 0, items: [] }), "GET /api/artifacts/facets": () => jsonResponse(FACETS) });
    render(<Page />);
    expect(await screen.findByText("Nothing matches these filters")).toBeInTheDocument();
    expect(screen.getByText("0 files · 6.0 KB in all")).toBeInTheDocument();
  });

  it("says when the list cannot be loaded", async () => {
    routeFetch({ "GET /api/artifacts": () => jsonResponse({ detail: "database is busy" }, { status: 500 }), "GET /api/artifacts/facets": () => jsonResponse(FACETS) });
    render(<Page />);
    expect(await screen.findByRole("alert")).toHaveTextContent("database is busy");
  });

  it("searches after a short pause and sends every filter to the server", async () => {
    const net = setup();
    render(<Page />);
    await screen.findAllByTestId("tile");
    fireEvent.change(screen.getByLabelText("Search artifacts"), { target: { value: "emea" } });
    await waitFor(() => expect(lastList(net.calls).get("q")).toBe("emea"));
    await userEvent.click(screen.getByRole("button", { name: "Documents · 1" }));
    expect(lastList(net.calls).get("type")).toBe("document");
    await userEvent.click(screen.getByRole("button", { name: "Documents · 1" }));
    expect(lastList(net.calls).has("type")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Images · 1" }));
    await userEvent.click(screen.getByRole("button", { name: "All kinds" }));
    expect(lastList(net.calls).has("type")).toBe(false);
    await userEvent.selectOptions(screen.getByLabelText("Agent"), "engineer");
    await userEvent.selectOptions(screen.getByLabelText("Storage"), "local");
    await userEvent.selectOptions(screen.getByLabelText("Made by"), "tool");
    await userEvent.selectOptions(screen.getByLabelText("When"), "7");
    let p = lastList(net.calls);
    expect([p.get("agent"), p.get("storage"), p.get("source")]).toEqual(["engineer", "local", "tool"]);
    expect(Number(p.get("since"))).toBeCloseTo(Date.now() / 1000 - 7 * 86400, -2);
    await userEvent.selectOptions(screen.getByLabelText("Size"), "small");
    expect(lastList(net.calls).get("max_size")).toBe(String(100 * 1024));
    await userEvent.selectOptions(screen.getByLabelText("Size"), "medium");
    p = lastList(net.calls);
    expect([p.get("min_size"), p.get("max_size")]).toEqual([String(100 * 1024), String(5 * 1024 * 1024)]);
    await userEvent.selectOptions(screen.getByLabelText("Size"), "large");
    expect(lastList(net.calls).get("min_size")).toBe(String(5 * 1024 * 1024));
    await userEvent.selectOptions(screen.getByLabelText("Sort"), "3");
    p = lastList(net.calls);
    expect([p.get("sort"), p.get("order")]).toEqual(["size", "desc"]);
  });

  it("pages through a long list and switches to rows", async () => {
    const net = setup([REPORT, PLOT, DATA], {}, 130);
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Show more (127 left)" }));
    await waitFor(() => expect(lastList(net.calls).get("limit")).toBe("120"));
    await userEvent.click(screen.getByRole("button", { name: "List" }));
    const rows = screen.getAllByTestId("row");
    expect(rows).toHaveLength(3);
    expect(screen.queryAllByTestId("tile")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Grid" }));
    expect(screen.getAllByTestId("tile")).toHaveLength(3);
  });
});

describe("the details of one file", () => {
  it("previews markdown, shows where it came from and where it is kept", async () => {
    setup();
    render(<Page />);
    await userEvent.click((await screen.findAllByTestId("tile"))[0]);
    expect(nav.replace).toHaveBeenCalledWith("/artifacts/?id=aaaaaaaaaaaa", { scroll: false });
    const pv = await screen.findByTestId("preview");
    expect(await within(pv).findByText("Revenue grew.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", `${API_BASE}/api/artifacts/aaaaaaaaaaaa/content?download=true`);
    expect(screen.getByRole("link", { name: "Open the chat" })).toHaveAttribute("href", "/?s=s1");
    expect(screen.getByRole("link", { name: "Open the run" })).toHaveAttribute("href", "/runs/?s=s1");
    expect(screen.getByText(/engineer · Saved by an agent/)).toBeInTheDocument();
    expect(screen.getByText("q3")).toBeInTheDocument();
    expect(screen.getAllByTestId("copy")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(nav.replace).toHaveBeenLastCalledWith("/artifacts/", { scroll: false });
    expect(screen.queryByTestId("preview")).toBeNull();
  });

  it("previews each kind of file in its own way", async () => {
    const files = [A("d1", { preview: "csv", name: "t.tsv", type: "data" }), A("d2", { preview: "json", name: "j.json" }), A("d3", { preview: "json", name: "bad.json" }), A("d4", { preview: "text", name: "n.txt" }),
      A("d5", { preview: "pdf", name: "p.pdf" }), A("d6", { preview: "none", name: "m.bin", secrets: true, path: "out/m.bin", tags: [], session_id: "" }), A("d7", { preview: "text", name: "big.txt" }), A("d8", { preview: "markdown", name: "x.md" })];
    const bodies: Record<string, any> = { d1: { kind: "csv", text: "a\tb\n1\t2", truncated: false }, d2: { kind: "json", text: '{"a":1}', truncated: false }, d3: { kind: "json", text: "{broken", truncated: false },
      d4: { kind: "text", text: "plain words", truncated: false }, d7: { kind: "text", text: "start", truncated: true } };
    routeFetch({ "GET /api/artifacts": () => jsonResponse({ total: files.length, items: files }), "GET /api/artifacts/facets": () => jsonResponse(FACETS),
      ...Object.fromEntries(Object.keys(bodies).map((k) => [`GET /api/artifacts/${k}/preview`, () => jsonResponse(bodies[k])])), "GET /api/artifacts/d8/preview": () => jsonResponse({ detail: "the file could not be read" }, { status: 422 }) });
    render(<Page />);
    const open = async (name: string) => { await userEvent.click((await screen.findAllByTestId("tile")).find((t) => within(t).queryByText(name))!); return screen.findByTestId("preview"); };
    expect(await within(await open("t.tsv")).findByRole("columnheader", { name: "b" })).toBeInTheDocument();
    expect((await within(await open("j.json")).findByTestId("json-viewer")).textContent).toContain("a: 1");
    expect(await within(await open("bad.json")).findByText("{broken")).toBeInTheDocument();
    expect(await within(await open("n.txt")).findByText("plain words")).toBeInTheDocument();
    const pdf = await open("p.pdf");
    expect(pdf.querySelector("iframe")).toHaveAttribute("src", `${API_BASE}/api/artifacts/d5/content`);
    const none = await open("m.bin");
    expect(within(none).getByText(/No preview for this kind of file/)).toBeInTheDocument();
    expect(screen.getByText(/looks like a secret/)).toBeInTheDocument();
    expect(screen.getByText("out/m.bin")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open the chat" })).toBeNull();
    expect(await within(await open("big.txt")).findByText(/Showing the start of the file/)).toBeInTheDocument();
    expect(await within(await open("x.md")).findByRole("alert")).toHaveTextContent("the file could not be read");
  });

  it("shows a picture inline", async () => {
    setup();
    render(<Page />);
    await userEvent.click((await screen.findAllByTestId("tile"))[1]);
    expect((await screen.findByTestId("preview")).querySelector("img")).toHaveAttribute("src", `${API_BASE}/api/artifacts/bbbbbbbbbbbb/content`);
  });

  it("copies the link, retries the copies and shows a failed one", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    const synced = { ...REPORT, copies: [copy(), copy("team", "synced")] };
    setup([REPORT], { "POST /api/artifacts/aaaaaaaaaaaa/resync": () => jsonResponse(synced) });
    render(<Page />);
    await userEvent.click(await screen.findByTestId("tile"));
    await userEvent.click(await screen.findByRole("button", { name: "Copy link" }));
    expect(writeText).toHaveBeenCalledWith(`${API_BASE}/api/artifacts/aaaaaaaaaaaa/content`);
    await waitFor(() => expect(toasts.map((t) => t.text)).toContain("Link copied"));
    await userEvent.click(screen.getByRole("button", { name: "Retry or update copies" }));
    await waitFor(() => expect(screen.getAllByTestId("copy")).toHaveLength(2));
    expect(toasts.map((t) => t.text)).toContain("Copies updated");
  });

  it("reports a failed retry", async () => {
    setup([REPORT], { "POST /api/artifacts/aaaaaaaaaaaa/resync": () => jsonResponse({ detail: "no such artifact" }, { status: 404 }) });
    render(<Page />);
    await userEvent.click(await screen.findByTestId("tile"));
    await userEvent.click(await screen.findByRole("button", { name: "Retry or update copies" }));
    await waitFor(() => expect(toasts).toContainEqual({ text: "no such artifact", tone: "err" }));
  });

  it("asks before deleting, then deletes everywhere", async () => {
    const net = setup([REPORT], { "DELETE /api/artifacts/aaaaaaaaaaaa": () => jsonResponse({ deleted: true, storage_errors: [] }) });
    render(<Page />);
    await userEvent.click(await screen.findByTestId("tile"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete…" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("It is removed from every storage that holds a copy. This cannot be undone.");
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(net.calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => expect(net.calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(toasts).toContainEqual({ text: "Deleted", tone: "ok" });
    await waitFor(() => expect(screen.queryByTestId("preview")).toBeNull());
  });

  it("says when a storage could not remove its copy, or deleting failed", async () => {
    setup([REPORT], { "DELETE /api/artifacts/aaaaaaaaaaaa": () => jsonResponse({ deleted: true, storage_errors: [{ storage: "team", error: "refused" }] }) });
    const { unmount } = render(<Page />);
    await userEvent.click(await screen.findByTestId("tile"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => expect(toasts).toContainEqual({ text: "Deleted, but 1 storage could not remove its copy", tone: "warn" }));
    unmount();
    setup([REPORT], { "DELETE /api/artifacts/aaaaaaaaaaaa": () => jsonResponse({ detail: "no such artifact" }, { status: 404 }) });
    render(<Page />);
    await userEvent.click(await screen.findByTestId("tile"));
    await userEvent.click(await screen.findByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await waitFor(() => expect(toasts).toContainEqual({ text: "no such artifact", tone: "err" }));
  });

  it("opens a row from the keyboard", async () => {
    setup();
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "List" }));
    const row = (await screen.findAllByTestId("row"))[0];
    fireEvent.keyDown(row, { key: "x" });
    expect(screen.queryByTestId("preview")).toBeNull();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(await screen.findByTestId("preview")).toBeInTheDocument();
  });
});

describe("unusual files", () => {
  it("copes with an unknown kind and origin, a missing agent and copies in every state", async () => {
    const odd = A("eeeeeeeeeeee", { name: "odd.bin", type: "weird", source: "odd", agent: "", preview: "none", session_id: "", tags: [],
      copies: [copy("local", "synced"), copy("team", "failed", "the storage refused the credentials"), copy("dav", "blocked", "contains a secret"), copy("gcs", "synced")] });
    routeFetch({ "GET /api/artifacts": () => jsonResponse({ total: 1, items: [odd] }), "GET /api/artifacts/facets": () => jsonResponse(null) });
    render(<Page />);
    const tile = await screen.findByTestId("tile");
    expect(within(tile).getByText(/weird · 2.0 KB/)).toBeInTheDocument();
    expect(screen.getByText("1 file")).toBeInTheDocument();
    await userEvent.click(tile);
    expect(screen.getByText(/weird · application|weird · text/)).toBeInTheDocument();
    expect(screen.getByText(/unknown · odd/)).toBeInTheDocument();
    expect(screen.getByText("the storage refused the credentials")).toBeInTheDocument();
    expect(screen.getByText("not sent")).toBeInTheDocument();
    expect(screen.getAllByTestId("copy")).toHaveLength(4);
  });

  it("opens a row with a click and shows its details in the list view", async () => {
    const odd = A("ffffffffffff", { name: "row.bin", type: "weird", preview: "none" });
    routeFetch({ "GET /api/artifacts": () => jsonResponse({ total: 1, items: [odd] }), "GET /api/artifacts/facets": () => jsonResponse(FACETS) });
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "List" }));
    const row = await screen.findByTestId("row");
    expect(within(row).getByText("weird")).toBeInTheDocument();
    await userEvent.click(row);
    expect(await screen.findByTestId("preview")).toBeInTheDocument();
  });
});

describe("links from the chat", () => {
  it("opens the file named in the address", async () => {
    nav.search = "id=aaaaaaaaaaaa";
    setup();
    render(<Page />);
    expect(await screen.findByRole("complementary", { name: "Details of q3-report.md" })).toBeInTheDocument();
  });

  it("says when that file no longer exists", async () => {
    nav.search = "id=gone";
    setup([REPORT], { "GET /api/artifacts/gone": () => jsonResponse({ detail: "no such artifact" }, { status: 404 }) });
    render(<Page />);
    await waitFor(() => expect(toasts).toContainEqual({ text: "That artifact no longer exists", tone: "warn" }));
    expect(screen.queryByRole("complementary")).toBeNull();
  });
});

describe("the storage tab", () => {
  it("shows the rules and storages", async () => {
    routeFetch({ "GET /api/artifacts": () => jsonResponse({ total: 0, items: [] }), "GET /api/artifacts/facets": () => jsonResponse({ ...FACETS, total: 0 }),
      "GET /api/artifacts/config": () => jsonResponse({ enabled: true, capture: "new", max_file_mb: 25, max_per_turn: 50, secret_scan: "redact", retention_days: 0, ignore: [], storages: [], policy: [], default_storages: ["local"],
        capture_modes: { new: "New files only" }, scan_modes: { redact: "Remove" }, types: [], default_ignore: [], kinds: [] }) });
    render(<Page />);
    await userEvent.click(await screen.findByRole("tab", { name: "Storage and rules" }));
    expect(await screen.findByText(/A copy always stays on this PC/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /^Files/ }));
    expect(await screen.findByLabelText("Search artifacts")).toBeInTheDocument();
  });
});
