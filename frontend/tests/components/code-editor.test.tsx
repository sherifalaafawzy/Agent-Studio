import { render } from "@testing-library/react";
import { EditorView } from "@codemirror/view";
import { describe, expect, it, vi } from "vitest";
import CodeEditor, { languageSupport } from "@/components/canvas/CodeEditor";

// jsdom has no layout, so CodeMirror's measuring needs these to exist
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () { /* none */ } }) as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => ({ x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, toJSON: () => ({}) }) as DOMRect;
}

const viewOf = (c: HTMLElement) => EditorView.findFromDOM(c.querySelector(".cm-editor") as HTMLElement)!;

describe("CodeEditor", () => {
  it("shows the text, reports typing and selection, and takes new text from outside", () => {
    const onChange = vi.fn(), onSelect = vi.fn();
    const { container, rerender } = render(<CodeEditor value="hello" onChange={onChange} onSelect={onSelect} type="markdown" language="" label="My text" placeholder="Type here" />);
    const view = viewOf(container);
    expect(view.state.doc.toString()).toBe("hello");
    expect(container.querySelector(".cm-content")).toHaveAttribute("aria-label", "My text");
    view.dispatch({ changes: { from: 5, insert: "!" } });
    expect(onChange).toHaveBeenCalledWith("hello!");
    expect(onSelect).toHaveBeenCalledWith({ start: 0, end: 0 });
    view.dispatch({ selection: { anchor: 1, head: 4 } });
    expect(onSelect).toHaveBeenLastCalledWith({ start: 1, end: 4 });
    rerender(<CodeEditor value="from outside" onChange={onChange} onSelect={onSelect} type="markdown" language="" label="My text" />);
    expect(view.state.doc.toString()).toBe("from outside");
    const calls = onChange.mock.calls.length;
    rerender(<CodeEditor value="from outside" onChange={onChange} type="markdown" language="" />);
    expect(onChange.mock.calls.length).toBe(calls);                                 // the same text is not set again
  });

  it("marks the agent's pending changes and ignores marks that do not fit", () => {
    const { container, rerender } = render(<CodeEditor value="one two three" onChange={() => {}} type="text" language="" marks={[{ from: 4, to: 7 }, { from: 5, to: 5 }, { from: 8, to: 99 }, { from: 0, to: 3 }]} />);
    const marked = [...container.querySelectorAll(".cm-pending")].map((e) => e.textContent);
    expect(marked).toEqual(["one", "two"]);
    rerender(<CodeEditor value="one two three" onChange={() => {}} type="text" language="" marks={[]} />);
    expect(container.querySelector(".cm-pending")).toBeNull();
    rerender(<CodeEditor value="one two three" onChange={() => {}} type="text" language="" marks={[{ from: 8, to: 13 }]} />);
    viewOf(container).dispatch({ changes: { from: 0, insert: ">> " } });          // the mark moves with the text
    expect(container.querySelector(".cm-pending")?.textContent).toBe("three");
  });

  it("can be read only, and changes language", () => {
    const { container, rerender } = render(<CodeEditor value="x = 1" onChange={() => {}} type="code" language="python" readOnly />);
    expect(viewOf(container).state.readOnly).toBe(true);
    rerender(<CodeEditor value="x = 1" onChange={() => {}} type="code" language="javascript" />);
    expect(viewOf(container).state.readOnly).toBe(false);
  });

  it("has syntax support for each language and none for the rest", () => {
    for (const lang of ["python", "javascript", "typescript", "json", "html", "css", "sql", "yaml", "markdown"]) expect(languageSupport("code", lang)).not.toEqual([]);
    expect(languageSupport("markdown", "")).not.toEqual([]);
    expect(languageSupport("html", "")).not.toEqual([]);
    expect(languageSupport("code", "bash")).toEqual([]);
    expect(languageSupport("code", "")).toEqual([]);
    expect(languageSupport("table", "")).toEqual([]);
    expect(languageSupport("text", "python")).toEqual([]);
  });
});
