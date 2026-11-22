"use client";
// The text editor inside the canvas (CodeMirror 6): line wrapping, undo, syntax colours for the chosen language, the agent's pending changes marked in the text,
// and a report of what the user has selected. It never runs what it holds.
import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { yaml } from "@codemirror/lang-yaml";
import { syntaxHighlighting } from "@codemirror/language";
import { Compartment, EditorState, StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView, keymap, placeholder as cmPlaceholder } from "@codemirror/view";
import { classHighlighter } from "@lezer/highlight";

export type Range = { from: number; to: number };
const NO_MARKS: Range[] = [];

/** The syntax support for a canvas: a Markdown document, or a code language. Anything else is plain text. */
export function languageSupport(type: string, language: string): Extension {
  if (type === "markdown") return markdown();
  if (type === "html") return html();
  if (type !== "code") return [];
  switch (language) {
    case "python": return python();
    case "javascript": return javascript();
    case "typescript": return javascript({ typescript: true });
    case "json": return json();
    case "html": return html();
    case "css": return css();
    case "sql": return sql();
    case "yaml": return yaml();
    case "markdown": return markdown();
    default: return [];
  }
}

const setMarks = StateEffect.define<Range[]>();
const marksField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, tr) {
    for (const e of tr.effects) {
      if (e.is(setMarks)) return Decoration.set(e.value.filter((r) => r.to > r.from && r.to <= tr.state.doc.length).sort((a, b) => a.from - b.from).map((r) => Decoration.mark({ class: "cm-pending" }).range(r.from, r.to)));
    }
    return value.map(tr.changes);
  },
  provide: (f) => EditorView.decorations.from(f),
});

type Props = {
  value: string; onChange: (v: string) => void; type: string; language: string; marks?: Range[]; readOnly?: boolean; label?: string;
  onSelect?: (r: { start: number; end: number }) => void; placeholder?: string;
};

export default function CodeEditor({ value, onChange, type, language, marks = NO_MARKS, readOnly = false, label = "Canvas text", onSelect, placeholder = "" }: Props) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  const lang = useRef(new Compartment());
  const ro = useRef(new Compartment());
  const onChangeRef = useRef(onChange), onSelectRef = useRef(onSelect);
  onChangeRef.current = onChange;
  onSelectRef.current = onSelect;

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          history(), keymap.of([...defaultKeymap, ...historyKeymap]), EditorView.lineWrapping, syntaxHighlighting(classHighlighter), marksField,
          lang.current.of(languageSupport(type, language)), ro.current.of(EditorState.readOnly.of(readOnly)), cmPlaceholder(placeholder),
          EditorView.contentAttributes.of({ "aria-label": label, "aria-multiline": "true" }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) onChangeRef.current(u.state.doc.toString());
            if (u.selectionSet || u.docChanged) { const s = u.state.selection.main; onSelectRef.current?.({ start: s.from, end: s.to }); }
          }),
        ],
      }),
    });
    view.current = v;
    return () => { v.destroy(); view.current = null; };
    // the editor is created once; the effects below keep it in step with the props
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { // text changed from outside (the agent's change was accepted, a version was restored)
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);
  useEffect(() => { view.current?.dispatch({ effects: lang.current.reconfigure(languageSupport(type, language)) }); }, [type, language]);
  useEffect(() => { view.current?.dispatch({ effects: ro.current.reconfigure(EditorState.readOnly.of(readOnly)) }); }, [readOnly]);
  useEffect(() => { view.current?.dispatch({ effects: setMarks.of(marks) }); }, [marks]);

  return <div className="code-editor" ref={host} data-testid="code-editor" />;
}
