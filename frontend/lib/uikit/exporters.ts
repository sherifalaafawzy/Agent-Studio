// Export helpers for components: SVG and PNG pictures, a self-contained HTML page and a print-to-PDF. Everything is built in the browser from the data already on the
// screen; nothing is uploaded. A chart's colours are CSS variables, so a picture first copies the computed colours onto each element (otherwise it would be black).
import { downloadFile } from "@/lib/tables";

const PROPS = ["fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "opacity", "fill-opacity", "stroke-opacity", "font-size", "font-family", "font-weight", "text-anchor", "dominant-baseline", "letter-spacing"];

/** A standalone copy of an <svg> with its computed styles inlined, a background and an explicit size. */
export function inlineSvg(svg: SVGSVGElement, background = ""): { text: string; width: number; height: number } {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const src = [svg, ...Array.from(svg.querySelectorAll("*"))], dst = [clone, ...Array.from(clone.querySelectorAll("*"))];
  src.forEach((el, i) => {
    const cs = getComputedStyle(el), t = dst[i] as SVGElement;
    const css = PROPS.map((p) => `${p}:${cs.getPropertyValue(p)}`).filter((d) => !d.endsWith(":")).join(";");
    if (css) t.setAttribute("style", css);
    t.removeAttribute("class");
  });
  const vb = svg.viewBox?.baseVal, width = Math.round(vb?.width || svg.clientWidth || 600), height = Math.round(vb?.height || svg.clientHeight || 360);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width)); clone.setAttribute("height", String(height));
  const bg = background || getComputedStyle(document.body).backgroundColor || "#fff";
  clone.insertAdjacentHTML("afterbegin", `<rect x="0" y="0" width="100%" height="100%" fill="${bg}"/>`);
  return { text: new XMLSerializer().serializeToString(clone), width, height };
}

export const fileName = (title: string, ext: string) => `${title.replace(/[^\w .-]+/g, "").trim().slice(0, 50) || "chart"}.${ext}`;

export function exportSvg(svg: SVGSVGElement, title: string) {
  downloadFile(fileName(title, "svg"), inlineSvg(svg).text, "image/svg+xml;charset=utf-8");
}

/** A PNG picture (twice the size, for sharp text) of an <svg>. */
export function exportPng(svg: SVGSVGElement, title: string, scale = 2): Promise<void> {
  const { text, width, height } = inlineSvg(svg);
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = width * scale; c.height = height * scale;
      const g = c.getContext("2d");
      if (!g) { reject(new Error("this browser cannot make a picture")); return; }
      g.scale(scale, scale); g.drawImage(img, 0, 0, width, height);
      c.toBlob((b) => { if (b) { downloadFile(fileName(title, "png"), b, "image/png"); resolve(); } else reject(new Error("the picture could not be made")); }, "image/png");
    };
    img.onerror = () => reject(new Error("the picture could not be made"));
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;
  });
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** A self-contained HTML page (no scripts, no network) with a title, an optional picture and an optional table. */
export function htmlPage(title: string, parts: { svg?: string; table?: { head: string[]; rows: string[][] }; note?: string }): string {
  const t = parts.table;
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title>`
    + `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">`
    + `<style>body{font:14px system-ui,sans-serif;margin:24px;color:#1f1e1d}h1{font-size:18px}table{border-collapse:collapse}th,td{border:1px solid #ddd;padding:4px 8px;text-align:left}svg{max-width:100%;height:auto}</style></head><body>`
    + `<h1>${esc(title)}</h1>${parts.svg ?? ""}${t ? `<table><thead><tr>${t.head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${t.rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : ""}`
    + `${parts.note ? `<p>${esc(parts.note)}</p>` : ""}</body></html>`;
}

/** Print one element (the browser's print dialog offers "Save as PDF"). */
export function printElement(el: HTMLElement) {
  const body = document.body;
  el.classList.add("ui-print-target"); body.classList.add("ui-printing");
  const done = () => { el.classList.remove("ui-print-target"); body.classList.remove("ui-printing"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  window.print();
}

/** Save a data: URL (a picture a chart library made) as a file. */
export function downloadDataUrl(name: string, url: string) {
  const a = document.createElement("a");
  a.href = url; a.download = name; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
}
