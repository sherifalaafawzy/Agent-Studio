import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Markdown from "@/components/Markdown";
import BlockActions, { KeepContext } from "@/components/rich/BlockActions";
import DataTable from "@/components/rich/DataTable";
import HtmlPreview, { frameDoc, HTML_CSP } from "@/components/rich/HtmlPreview";
import JsonViewer, { findMatches } from "@/components/rich/JsonViewer";
import MediaBlock, { parseMedia } from "@/components/rich/MediaBlock";
import { RichFence } from "@/components/RichBlocks";
import { API_BASE } from "@/lib/api";
import * as tables from "@/lib/tables";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));
vi.mock("@/lib/tables", async (orig) => ({ ...(await orig<typeof import("@/lib/tables")>()), downloadFile: vi.fn() }));

const META = (over: any = {}) => ({ id: "0123456789ab", name: "song.mp3", type: "audio", mime: "audio/mpeg", size: 2048, ...over });
const table = (n: number, extra: Partial<tables.Table> = {}): tables.Table => ({
  title: "", truncated: false,
  columns: [{ key: "name", label: "Name", type: "text" }, { key: "n", label: "Count", type: "number" }, { key: "note", label: "Note", type: "text" }],
  rows: Array.from({ length: n }, (_, i) => [`item ${String(i).padStart(3, "0")}`, i, i === 0 ? "x".repeat(300) : ""]), ...extra,
});

beforeEach(() => { vi.mocked(tables.downloadFile).mockClear(); });

describe("DataTable", () => {
  it("sorts by a column, three clicks to go ascending, descending and back to the original order", async () => {
    render(<DataTable table={table(3)} />);
    const names = () => screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    const head = screen.getByRole("columnheader", { name: /Count/ });
    expect(head).toHaveAttribute("aria-sort", "none");
    await userEvent.click(within(head).getByRole("button"));
    expect(head).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(within(head).getByRole("button"));
    expect(head).toHaveAttribute("aria-sort", "descending");
    expect(names()).toEqual(["item 002", "item 001", "item 000"]);
    await userEvent.click(within(head).getByRole("button"));
    expect(head).toHaveAttribute("aria-sort", "none");
    expect(names()).toEqual(["item 000", "item 001", "item 002"]);
    await userEvent.click(within(screen.getByRole("columnheader", { name: /Name/ })).getByRole("button"));
    await userEvent.click(head.querySelector("button")!);                           // another column: starts ascending again
    expect(head).toHaveAttribute("aria-sort", "ascending");
  });

  it("filters rows and says when nothing matches", async () => {
    render(<DataTable table={table(30)} />);
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Filter rows"), "item 02");
    expect(screen.getByText("10 of 30 rows")).toBeInTheDocument();
    expect(screen.queryByText(/Page 1/)).toBeNull();
    await userEvent.clear(screen.getByLabelText("Filter rows"));
    await userEvent.type(screen.getByLabelText("Filter rows"), "zzz");
    expect(screen.getByText("No rows match this filter.")).toBeInTheDocument();
  });

  it("pages through big results and changes the page size", async () => {
    render(<DataTable table={table(120, { truncated: true })} />);
    expect(screen.getAllByRole("row")).toHaveLength(26);
    expect(screen.getByText(/120 rows \(the first 120 only\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 5")).toBeInTheDocument();
    expect(screen.getByText("item 025")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    await userEvent.selectOptions(screen.getByLabelText("Rows per page"), "100");
    expect(screen.getAllByRole("row")).toHaveLength(101);
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("hides columns but always keeps one", async () => {
    render(<DataTable table={table(2)} />);
    const group = screen.getByRole("group", { name: "Show or hide columns" });
    await userEvent.click(within(group).getByLabelText("Count"));
    expect(screen.queryByRole("columnheader", { name: /Count/ })).toBeNull();
    await userEvent.click(within(group).getByLabelText("Note"));
    await userEvent.click(within(group).getByLabelText("Name"));                     // the last one stays
    expect(screen.getByRole("columnheader", { name: /Name/ })).toBeInTheDocument();
    await userEvent.click(within(group).getByLabelText("Count"));                    // and a hidden one comes back
    expect(screen.getByRole("columnheader", { name: /Count/ })).toBeInTheDocument();
  });

  it("shortens long cells until you ask for the rest", async () => {
    render(<DataTable table={table(1)} />);
    const more = screen.getByRole("button", { name: "more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(more);
    expect(screen.getByText(/x{300}/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "less" }));
    expect(screen.queryByText(/x{300}/)).toBeNull();
  });

  it("copies what is shown as tab-separated text", async () => {
    const write = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText: write } });
    render(<DataTable table={table(2)} />);
    await userEvent.type(screen.getByLabelText("Filter rows"), "001");
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(write).toHaveBeenCalledWith(expect.stringContaining("item 001\t1"));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("exports the rows and columns you are showing in four formats", async () => {
    render(<DataTable table={table(3, { title: "Q3: sales" })} />);
    await userEvent.type(screen.getByLabelText("Filter rows"), "item 001");
    await userEvent.click(within(screen.getByRole("group", { name: "Show or hide columns" })).getByLabelText("Note"));
    const group = screen.getByRole("group", { name: "Export the rows and columns shown" });
    for (const label of ["CSV", "JSON", "Markdown", "Excel (.xlsx)"]) await userEvent.click(within(group).getByRole("button", { name: label }));
    const calls = vi.mocked(tables.downloadFile).mock.calls;
    expect(calls.map((c) => c[0])).toEqual(["Q3 sales.csv", "Q3 sales.json", "Q3 sales.md", "Q3 sales.xlsx"]);
    expect(calls[0][1]).toBe("Name,Count\r\nitem 001,1\r\n");
    expect(JSON.parse(calls[1][1] as string)).toEqual([{ Name: "item 001", Count: 1 }]);
    expect(String(calls[2][1])).toContain("| item 001 | 1 |");
    expect(calls[3][1]).toBeInstanceOf(Uint8Array);
    expect(calls[3][2]).toContain("spreadsheetml");
  });

  it("keeps the rows you are showing as a CSV file", async () => {
    const f = routeFetch({ "POST /api/artifacts": () => jsonResponse({ id: "eeeeeeeeeeee" }) });
    render(<DataTable table={table(3)} />);
    await userEvent.type(screen.getByLabelText("Filter rows"), "item 002");
    await userEvent.click(screen.getByRole("button", { name: "Keep table" }));
    await waitFor(() => expect(f.calls).toHaveLength(1));
    expect(f.calls[0].body).toMatchObject({ name: "table.csv", content: expect.stringContaining("item 002,2") });
    expect(f.calls[0].body.content).not.toContain("item 001");
  });

  it("can be shown without the Keep buttons", () => {
    render(<DataTable table={table(1)} plain name="file.csv" />);
    expect(screen.queryByRole("button", { name: "Keep table" })).toBeNull();
    expect(screen.getByTestId("data-table")).toBeInTheDocument();
  });
});

describe("Keep and Open in Artifacts", () => {
  it("keeps once, under the chat it came from, and opens the page in a new tab", async () => {
    const f = routeFetch({ "POST /api/artifacts": () => jsonResponse({ id: "aaaaaaaaaaaa" }) });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<KeepContext.Provider value={{ agent: "engineer", session: "s1" }}><BlockActions label="table" name="t.csv" content={() => "a,b"} /></KeepContext.Provider>);
    await userEvent.click(screen.getByRole("button", { name: "Keep table" }));
    expect(await screen.findByRole("button", { name: "Keep table" })).toHaveTextContent("Kept");
    expect(f.calls[0].body).toEqual({ name: "t.csv", content: "a,b", agent: "engineer", session_id: "s1" });
    await userEvent.click(screen.getByRole("button", { name: "Open table in Artifacts" }));
    expect(open).toHaveBeenCalledWith("/artifacts/?id=aaaaaaaaaaaa", "_blank", "noopener");
    expect(f.calls).toHaveLength(1);                                                   // already kept: not saved twice
  });

  it("saves first when you open it straight away, and says why it could not", async () => {
    const f = routeFetch({ "POST /api/artifacts": () => jsonResponse({ id: "bbbbbbbbbbbb" }) });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { unmount } = render(<BlockActions label="JSON" name="j.json" content={() => "{}"} />);
    await userEvent.click(screen.getByRole("button", { name: "Open JSON in Artifacts" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("/artifacts/?id=bbbbbbbbbbbb", "_blank", "noopener"));
    expect(f.calls[0].body).toMatchObject({ agent: "", session_id: "" });
    unmount();
    routeFetch({ "POST /api/artifacts": () => jsonResponse({ detail: "artifacts are switched off in Settings" }, { status: 422 }) });
    render(<BlockActions label="JSON" name="j.json" content={() => "{}"} />);
    await userEvent.click(screen.getByRole("button", { name: "Keep JSON" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("switched off");
    await userEvent.click(screen.getByRole("button", { name: "Open JSON in Artifacts" }));
    await waitFor(() => expect(open).toHaveBeenCalledTimes(1));                        // a failed save opens nothing
  });

  it("does not save a file that already is an artifact", async () => {
    const f = routeFetch({});
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<BlockActions label="audio" name="a.mp3" content={() => ""} artifactId="cccccccccccc" />);
    expect(screen.getByRole("button", { name: "Keep audio" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Open audio in Artifacts" }));
    expect(open).toHaveBeenCalledWith("/artifacts/?id=cccccccccccc", "_blank", "noopener");
    expect(f.calls).toHaveLength(0);
  });
});

describe("JsonViewer", () => {
  const doc = { name: "Ada", tags: ["a", "b"], nested: { deep: { deeper: { n: 1, ok: true, none: null } } }, long: "x".repeat(400) };

  it("shows a tree that opens and closes", async () => {
    render(<JsonViewer value={doc} title="Doc" />);
    expect(screen.getByText('"Ada"')).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /deep/ })).toBeNull();                  // below the first level: closed
    const nested = screen.getByRole("button", { name: /nested/, expanded: false });
    await userEvent.click(nested);
    expect(screen.getByRole("button", { name: /deep/, expanded: false })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /nested/, expanded: true }));
    expect(screen.queryByRole("button", { name: /deep/ })).toBeNull();
    expect(screen.getByText(/x{300}…/)).toBeInTheDocument();                            // long text is cut
  });

  it("searches keys and values and opens the branches that match", async () => {
    render(<JsonViewer value={doc} />);
    await userEvent.type(screen.getByLabelText("Search the JSON"), "deeper");
    expect(screen.getByRole("button", { name: /deeper/, expanded: true })).toBeInTheDocument();     // opened down to the match
    expect(screen.getByText("none:", { exact: false })).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Search the JSON"));
    await userEvent.type(screen.getByLabelText("Search the JSON"), "ada");
    expect(document.querySelector(".jv-hit")).not.toBeNull();
  });

  it("copies a path or a value", async () => {
    const write = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText: write } });
    render(<JsonViewer value={{ "odd key": { list: [1, { z: "text" }] }, name: "Ada" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy path of name" }));
    expect(write).toHaveBeenLastCalledWith("name");
    expect(await screen.findByRole("status")).toHaveTextContent("Path copied");
    await userEvent.click(screen.getByRole("button", { name: "Copy value of name" }));
    expect(write).toHaveBeenLastCalledWith("Ada");
    await userEvent.click(screen.getByRole("button", { name: "Copy value of odd key" }));
    expect(write).toHaveBeenLastCalledWith(JSON.stringify({ list: [1, { z: "text" }] }, null, 2));
    await userEvent.click(screen.getByRole("button", { name: "Copy path of root" }));
    expect(write).toHaveBeenLastCalledWith("$");
    await userEvent.click(screen.getByRole("button", { name: /odd key/, expanded: false }));
    await userEvent.click(screen.getByRole("button", { name: /list/, expanded: false }));
    await userEvent.click(screen.getByRole("button", { name: "Copy path of 1" }));          // the object inside the list
    expect(write).toHaveBeenLastCalledWith('["odd key"].list[1]');
  });

  it("downloads the document and keeps long lists short", async () => {
    render(<JsonViewer value={Array.from({ length: 230 }, (_, i) => i)} title="Numbers" />);
    expect(screen.getByRole("button", { name: /Show 100 more of 130 left/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show 100 more/ }));
    await userEvent.click(screen.getByRole("button", { name: /Show 30 more of 30 left/ }));
    expect(screen.queryByRole("button", { name: /more of/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(tables.downloadFile).toHaveBeenCalledWith("Numbers.json", expect.stringContaining("229"), "application/json");
  });

  it("shows a document that is too large as text", async () => {
    render(<JsonViewer text={"[1,2,3"} title="Huge" />);
    expect(screen.getByRole("note")).toHaveTextContent("too large");
    expect(screen.getByText("[1,2,3")).toBeInTheDocument();
    expect(screen.queryByLabelText("Search the JSON")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(tables.downloadFile).toHaveBeenCalledWith("Huge.json", "[1,2,3", "application/json");
  });

  it("can be shown without the Keep buttons, and says when a search was cut short", async () => {
    const { unmount } = render(<JsonViewer value={{ a: 1 }} plain />);
    expect(screen.queryByRole("button", { name: "Keep JSON" })).toBeNull();
    unmount();
    render(<JsonViewer value={Array.from({ length: 20010 }, (_, i) => ({ i }))} />);
    await userEvent.type(screen.getByLabelText("Search the JSON"), "9");
    expect(await screen.findByText(/Search stopped after 20000 items/)).toBeInTheDocument();
  });

  it("finds matches and their ancestors", () => {
    const m = findMatches({ a: { b: [1, "needle"] }, c: 2 }, "needle");
    expect([...m.hits]).toEqual(["a.b[1]"]);
    expect([...m.open].sort()).toEqual(["", "a", "a.b"]);
    expect(findMatches({ a: 1 }, "  ")).toEqual({ hits: new Set(), open: new Set(), cut: false });
  });
});

describe("HtmlPreview", () => {
  it("draws the page in a frame that can do nothing, with a policy that blocks the network", () => {
    render(<HtmlPreview html="<h1>Hi</h1><script>steal()</script>" title="Report" />);
    const frame = screen.getByTitle("Report preview (sandboxed)") as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.hasAttribute("csp")).toBe(false);
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.getAttribute("srcdoc")).toBe(frameDoc("<h1>Hi</h1><script>steal()</script>"));
    expect(frameDoc("x")).toContain(`http-equiv="Content-Security-Policy" content="${HTML_CSP}"`);
    expect(HTML_CSP).toContain("default-src 'none'");
    expect(document.querySelector("h1")).toBeNull();                                    // never added to the page itself
    expect(screen.getByText("sandboxed")).toBeInTheDocument();
  });

  it("switches to the source, downloads it and goes full screen (Escape closes)", async () => {
    render(<HtmlPreview html="<b>x</b>" />);
    await userEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(screen.getByText("<b>x</b>")).toBeInTheDocument();
    expect(screen.queryByTitle(/preview \(sandboxed\)/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    await userEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(tables.downloadFile).toHaveBeenCalledWith("page.html", "<b>x</b>", "text/html;charset=utf-8");
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByTestId("html-preview")).toHaveClass("full");
    expect(screen.queryByRole("button", { name: "Keep HTML" })).toBeNull();
    fireEvent.keyDown(window, { key: "a" });
    expect(screen.getByTestId("html-preview")).toHaveClass("full");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByTestId("html-preview")).not.toHaveClass("full");
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByRole("button", { name: "Keep HTML" })).toBeInTheDocument();
  });

  it("keeps the page as an HTML file", async () => {
    const f = routeFetch({ "POST /api/artifacts": () => jsonResponse({ id: "ffffffffffff" }) });
    render(<HtmlPreview html="<i>kept</i>" title="My page" />);
    await userEvent.click(screen.getByRole("button", { name: "Keep HTML" }));
    await waitFor(() => expect(f.calls).toHaveLength(1));
    expect(f.calls[0].body).toMatchObject({ name: "My page.html", content: "<i>kept</i>" });
  });

  it("can be shown without the Keep buttons", () => {
    render(<HtmlPreview html="x" plain />);
    expect(screen.queryByRole("button", { name: "Keep HTML" })).toBeNull();
  });
});

describe("MediaBlock", () => {
  it("plays a saved audio file, changes speed and offers the download", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse(META()) });
    const { container } = render(<MediaBlock kind="audio" artifact="0123456789ab" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the audio");
    const audio = await waitFor(() => { const a = container.querySelector("audio"); expect(a).not.toBeNull(); return a!; });
    expect(audio).toHaveAttribute("src", `${API_BASE}/api/artifacts/0123456789ab/content`);
    expect(audio).toHaveAttribute("aria-label", "song.mp3");
    await userEvent.selectOptions(screen.getByLabelText("Playback speed"), "1.5");
    expect(audio.playbackRate).toBe(1.5);
    expect(screen.getByRole("link", { name: "Download song.mp3" })).toHaveAttribute("href", `${API_BASE}/api/artifacts/0123456789ab/content?download=true`);
    expect(screen.getByRole("button", { name: "Keep audio" })).toBeDisabled();
  });

  it("plays a video with captions from another saved file", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse(META({ name: "demo.mp4", type: "video", mime: "video/mp4" })) });
    const { container } = render(<MediaBlock kind="video" artifact="0123456789ab" title="Demo" captions="ba9876543210" />);
    await screen.findByTestId("video-block");
    const video = container.querySelector("video")!;
    expect(video).toHaveAttribute("crossorigin", "anonymous");
    expect(video.querySelector("track")).toHaveAttribute("src", `${API_BASE}/api/artifacts/ba9876543210/content`);
    expect(screen.getByText("Demo")).toBeInTheDocument();
  });

  it("plays a video without captions, in plain mode", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse(META({ name: "d.webm", type: "video", mime: "video/webm" })) });
    const { container } = render(<MediaBlock kind="video" artifact="0123456789ab" plain />);
    await screen.findByTestId("video-block");
    expect(container.querySelector("track")).toBeNull();
    expect(container.querySelector("video")).not.toHaveAttribute("crossorigin");
    expect(screen.queryByRole("button", { name: "Keep video" })).toBeNull();
  });

  it("will not play a file of the wrong kind or one a browser cannot play", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse(META({ type: "video", mime: "video/x-matroska", name: "c.mkv" })), "GET /api/artifacts/111111111111": () => jsonResponse(META({ type: "document", mime: "text/plain" })) });
    const { container, rerender } = render(<MediaBlock kind="video" artifact="0123456789ab" />);
    expect(await screen.findByRole("note")).toHaveTextContent("cannot be played in the browser");
    expect(container.querySelector("video")).toBeNull();
    expect(screen.queryByLabelText("Playback speed")).toBeNull();
    rerender(<MediaBlock kind="audio" artifact="111111111111" />);
    expect(await screen.findByText(/text\/plain\) cannot be played/)).toBeInTheDocument();
  });

  it("says when the file cannot be found", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse({ detail: "no such artifact" }, { status: 404 }) });
    render(<MediaBlock kind="audio" artifact="0123456789ab" />);
    expect(await screen.findByRole("note")).toHaveTextContent("Could not load this audio: no such artifact");
  });

  it("only takes the id of a saved file, never an address", () => {
    expect(parseMedia("0123456789ab").media).toEqual({ artifact: "0123456789ab", title: "", captions: null });
    expect(parseMedia(' {"artifact":"0123456789ab","title":"T","captions":"ba9876543210"} ').media).toEqual({ artifact: "0123456789ab", title: "T", captions: "ba9876543210" });
    expect(parseMedia('{"artifact":"0123456789ab","captions":"https://evil.example/x.vtt"}').media!.captions).toBeNull();
    expect(parseMedia("https://evil.example/a.mp3").error).toMatch(/id of a saved file/);
    expect(parseMedia('{"artifact":"https://evil.example/a.mp3"}').error).toMatch(/12-character id/);
    expect(parseMedia("null").error).toMatch(/12-character id/);
  });
});

describe("the fenced blocks", () => {
  it("draws a structured table, and says what is wrong with a bad one", () => {
    const { rerender } = render(<RichFence lang="table" code={JSON.stringify({ title: "Plans", columns: ["plan", "seats"], rows: [["pro", 5], ["team", 20]] })} />);
    expect(screen.getByTestId("data-table")).toBeInTheDocument();
    expect(screen.getByText("Plans")).toBeInTheDocument();
    rerender(<RichFence lang="table" code="{bad" />);
    expect(screen.getByRole("note")).toHaveTextContent("Could not show this table: the table is not valid JSON.");
    rerender(<RichFence lang="table" code='{"rows": 5}' />);
    expect(screen.getByRole("note")).toHaveTextContent("a table is a JSON object with a rows list");
  });

  it("draws JSON as a tree, a very large one as text, and refuses bad JSON", () => {
    const { rerender } = render(<RichFence lang="jsonview" code='{"a": {"b": 1}}' />);
    expect(screen.getByTestId("json-viewer")).toBeInTheDocument();
    rerender(<RichFence lang="jsonview" code={"[" + "1,".repeat(100_001) + "1]"} />);
    expect(screen.getByRole("note")).toHaveTextContent("too large");
    rerender(<RichFence lang="jsonview" code="{bad" />);
    expect(screen.getByRole("note")).toHaveTextContent("Could not show this JSON");
  });

  it("draws HTML only in the sandboxed frame", () => {
    render(<RichFence lang="html-preview" code="<p>hello</p>" />);
    expect(screen.getByTitle(/preview \(sandboxed\)/).getAttribute("sandbox")).toBe("");
  });

  it("plays audio and video by artifact id, and refuses a remote address", async () => {
    routeFetch({ "GET /api/artifacts/0123456789ab": () => jsonResponse(META()) });
    const { rerender } = render(<RichFence lang="audio" code="0123456789ab" />);
    expect(await screen.findByTestId("audio-block")).toBeInTheDocument();
    rerender(<RichFence lang="video" code="https://evil.example/v.mp4" />);
    expect(screen.getByRole("note")).toHaveTextContent("Could not play this video");
  });

  it("works inside an answer written in Markdown", () => {
    const text = "Here is the data:\n\n```table\n" + JSON.stringify({ columns: ["a"], rows: [[1]] }) + "\n```\n\nand a page:\n\n```html-preview\n<b>x</b>\n```\n\nand code stays code:\n\n```html\n<b>y</b>\n```\n";
    render(<Markdown text={text} />);
    expect(screen.getByTestId("data-table")).toBeInTheDocument();
    expect(screen.getAllByTitle(/preview \(sandboxed\)/)).toHaveLength(1);
    expect(screen.getByText("<b>y</b>")).toBeInTheDocument();
  });

  it("adds Keep to charts and diffs", () => {
    render(<><RichFence lang="chart" code='{"title":"My: chart","data":[{"label":"a","value":1}]}' /><RichFence lang="diff" code="+a" /></>);
    expect(screen.getByRole("button", { name: "Keep chart" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep diff" })).toBeInTheDocument();
  });

  it("keeps a chart as a JSON file", async () => {
    const f = routeFetch({ "POST /api/artifacts": () => jsonResponse({ id: "dddddddddddd" }) });
    render(<RichFence lang="chart" code='{"title":"My: chart","unit":"u","x":["a","b"],"series":[{"name":"s","values":[1,2]}]}' />);
    await userEvent.click(screen.getByRole("button", { name: "Keep chart" }));
    await act(async () => { await Promise.resolve(); });
    expect(f.calls[0].body.name).toBe("My chart.json");
    expect(JSON.parse(f.calls[0].body.content)).toMatchObject({ type: "bar", x: ["a", "b"] });
  });
});
