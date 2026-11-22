"use client";
// HTML an agent wrote, shown the only safe way: inside a frame with an empty sandbox (no scripts, no forms, no popups, no access to this app) and a content policy
// that blocks every network request. Its source can be read, downloaded and shown full screen. It is never added to the page itself.
import { useEffect, useState } from "react";
import { downloadFile, fileSafe } from "@/lib/tables";
import BlockActions from "./BlockActions";

export const HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:";
/** The document the frame shows, with its policy written in first. (The frame's own `csp` attribute is not used: browsers only honour it when the framed page opts in, and otherwise leave the frame blank.) */
export const frameDoc = (html: string) => `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${HTML_CSP}">${html}`;

export default function HtmlPreview({ html, title = "", plain = false }: { html: string; title?: string; plain?: boolean }) {
  const [mode, setMode] = useState<"preview" | "source">("preview");
  const [full, setFull] = useState(false);
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);
  const file = `${fileSafe(title, "page")}.html`;
  return (
    <figure className={`html-preview${full ? " full" : ""}`} data-testid="html-preview">
      <div className="dt-bar">
        <b>{title || "HTML preview"}</b>
        <span className="pill" title="Scripts, forms, links out and network requests are all blocked in this preview.">sandboxed</span>
        <span className="seg" role="group" aria-label="Preview or source">
          <button className="ghost small" aria-pressed={mode === "preview"} onClick={() => setMode("preview")}>Preview</button>
          <button className="ghost small" aria-pressed={mode === "source"} onClick={() => setMode("source")}>Source</button>
        </span>
        <button className="ghost small" onClick={() => downloadFile(file, html, "text/html;charset=utf-8")}>Download</button>
        <button className="ghost small" aria-pressed={full} onClick={() => setFull((v) => !v)}>{full ? "Close" : "Full screen"}</button>
      </div>
      {mode === "preview"
        ? <iframe className="html-frame" title={`${title || "HTML"} preview (sandboxed)`} sandbox="" referrerPolicy="no-referrer" srcDoc={frameDoc(html)} />
        : <pre className="html-source"><code>{html}</code></pre>}
      {!full && !plain && <div className="dt-foot small"><span className="mut" /><BlockActions canvas={{ type: "html" }} label="HTML" name={file} content={() => html} /></div>}
    </figure>
  );
}
