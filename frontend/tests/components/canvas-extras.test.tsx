import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Markdown from "@/components/Markdown";
import CanvasSettings from "@/components/CanvasSettings";
import { CanvasContext } from "@/components/rich/BlockActions";
import DataTable from "@/components/rich/DataTable";
import HtmlPreview from "@/components/rich/HtmlPreview";
import JsonViewer from "@/components/rich/JsonViewer";
import { RichFence } from "@/components/RichBlocks";
import { jsonResponse, routeFetch } from "../helpers/http";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

let toasts: { text: string; tone: string }[] = [];
beforeEach(() => { toasts = []; window.addEventListener("as:toast", (e) => toasts.push((e as CustomEvent).detail), { once: false }); });

describe("Open in canvas, from the pieces of an answer", () => {
  const withCanvas = (ui: React.ReactElement) => { const open = vi.fn((_o: any) => Promise.resolve()); render(<CanvasContext.Provider value={open}>{ui}</CanvasContext.Provider>); return open; };

  it("opens a code block as the right kind of canvas, and is not offered outside the chat", async () => {
    const open = withCanvas(<Markdown text={"```python\nprint(1)\n```\n\n```\nplain words\n```"} />);
    const buttons = screen.getAllByRole("button", { name: "Open in canvas" });
    await userEvent.click(buttons[0]);
    expect(open).toHaveBeenLastCalledWith({ title: "python snippet", type: "code", language: "python", content: "print(1)" });
    await userEvent.click(buttons[1]);
    expect(open).toHaveBeenLastCalledWith({ title: "Snippet", type: "text", language: "", content: "plain words" });
  });

  it("has no canvas button where there is no chat", () => {
    render(<Markdown text={"```python\nprint(1)\n```"} />);
    expect(screen.queryByRole("button", { name: "Open in canvas" })).toBeNull();
    render(<DataTable table={{ title: "", truncated: false, columns: [{ key: "a", label: "A", type: "text" }], rows: [["x"]] }} />);
    expect(screen.queryByRole("button", { name: "Open table in the canvas" })).toBeNull();
  });

  it("opens a table as CSV, JSON as code, a web page as a page, a chart and a diff as code", async () => {
    const open = withCanvas(<>
      <DataTable table={{ title: "Plans", truncated: false, columns: [{ key: "a", label: "A", type: "text" }, { key: "n", label: "N", type: "number" }], rows: [["x", 1]] }} />
      <JsonViewer value={{ a: 1 }} title="Data" />
      <HtmlPreview html="<b>hi</b>" title="Page" />
      <RichFence lang="chart" code={'{"title":"Sales","data":[{"label":"a","value":1}]}'} />
      <RichFence lang="diff" code={"+a"} />
    </>);
    await userEvent.click(screen.getByRole("button", { name: "Open table in the canvas" }));
    expect(open).toHaveBeenLastCalledWith({ title: "Plans", type: "table", language: undefined, content: "A,N\r\nx,1\r\n" });
    await userEvent.click(screen.getByRole("button", { name: "Open JSON in the canvas" }));
    expect(open).toHaveBeenLastCalledWith({ title: "Data", type: "code", language: "json", content: JSON.stringify({ a: 1 }, null, 2) });
    await userEvent.click(screen.getByRole("button", { name: "Open HTML in the canvas" }));
    expect(open).toHaveBeenLastCalledWith({ title: "Page", type: "html", language: undefined, content: "<b>hi</b>" });
    await userEvent.click(screen.getByRole("button", { name: "Open chart in the canvas" }));
    expect(open.mock.lastCall![0]).toMatchObject({ title: "Sales", type: "code", language: "json" });
    await userEvent.click(screen.getByRole("button", { name: "Open diff in the canvas" }));
    expect(open).toHaveBeenLastCalledWith({ title: "changes", type: "code", language: undefined, content: "+a" });
  });
});

describe("CanvasSettings", () => {
  const cfg = { enabled: true, auto_apply: false, max_chars: 200000, keep_versions: 100 };

  it("shows the settings, warns about applying changes at once, and saves", async () => {
    let saved: any = null;
    routeFetch({ "GET /api/canvas/config": () => jsonResponse(cfg), "PUT /api/canvas/config": (init) => { saved = JSON.parse(String(init?.body)); return jsonResponse(saved); } });
    render(<CanvasSettings />);
    const card = await screen.findByTestId("canvas-settings");
    expect(within(card).getByLabelText("Canvas is on")).toBeChecked();
    expect(within(card).queryByRole("note")).toBeNull();
    await userEvent.click(within(card).getByLabelText(/Apply agent changes straight away/));
    expect(within(card).getByRole("note")).toHaveTextContent("plan mode can only propose");
    await userEvent.clear(within(card).getByLabelText("Versions kept"));
    await userEvent.type(within(card).getByLabelText("Versions kept"), "20");
    await userEvent.clear(within(card).getByLabelText("Largest document"));
    await userEvent.type(within(card).getByLabelText("Largest document"), "5000");
    await userEvent.click(within(card).getByLabelText("Canvas is on"));
    await userEvent.click(within(card).getByRole("button", { name: "Save canvas settings" }));
    await waitFor(() => expect(saved).toEqual({ enabled: false, auto_apply: true, max_chars: 5000, keep_versions: 20 }));
    await waitFor(() => expect(toasts.at(-1)?.text).toBe("Canvas settings saved"));
  });

  it("says why it could not load or save", async () => {
    routeFetch({ "GET /api/canvas/config": () => jsonResponse({ detail: "down" }, { status: 500 }) });
    const { unmount } = render(<CanvasSettings />);
    expect(await screen.findByText(/could not be loaded: down/)).toBeInTheDocument();
    unmount();
    routeFetch({ "GET /api/canvas/config": () => jsonResponse(cfg), "PUT /api/canvas/config": () => jsonResponse({ detail: "keep_versions must be a whole number from 5 to 1000" }, { status: 422 }) });
    render(<CanvasSettings />);
    await userEvent.click(await screen.findByRole("button", { name: "Save canvas settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("keep_versions must be");
  });
});
