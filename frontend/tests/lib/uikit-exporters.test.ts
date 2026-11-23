import { afterEach, describe, expect, it, vi } from "vitest";
import * as tables from "@/lib/tables";
import { downloadDataUrl, exportPng, exportSvg, fileName, htmlPage, inlineSvg, printElement } from "@/lib/uikit/exporters";

vi.mock("@/lib/tables", async (orig) => ({ ...(await orig<typeof import("@/lib/tables")>()), downloadFile: vi.fn() }));

function svgEl(withViewBox = true) {
  document.body.innerHTML = `<svg ${withViewBox ? 'viewBox="0 0 200 100"' : ""} id="s"><rect class="x" width="10" height="10" style="fill:red"/><text>hi</text></svg>`;
  return document.getElementById("s") as unknown as SVGSVGElement;
}

afterEach(() => { vi.mocked(tables.downloadFile).mockClear(); });

describe("exports", () => {
  it("makes a standalone svg with computed styles, a size and a background", () => {
    const r = inlineSvg(svgEl(), "#fff");
    expect(r.width).toBe(200); expect(r.height).toBe(100); expect(r.text).toContain('xmlns="http://www.w3.org/2000/svg"'); expect(r.text).toContain('fill="#fff"'); expect(r.text).not.toContain("class=");
    const noBox = inlineSvg(svgEl(false));
    expect(noBox.width).toBe(600); expect(noBox.height).toBe(360); expect(noBox.text).toContain("<rect");
  });
  it("names files safely", () => { expect(fileName("A/B: c", "svg")).toBe("AB c.svg"); expect(fileName("///", "png")).toBe("chart.png"); });
  it("downloads an svg", () => {
    exportSvg(svgEl(), "My chart");
    expect(tables.downloadFile).toHaveBeenCalledWith("My chart.svg", expect.stringContaining("<svg"), "image/svg+xml;charset=utf-8");
  });
  it("makes a png through a canvas", async () => {
    const drawn: any[] = [];
    class FakeImage { onload: any; onerror: any; set src(_v: string) { setTimeout(() => this.onload?.(), 0); } }
    vi.stubGlobal("Image", FakeImage);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ scale: vi.fn(), drawImage: (...a: any[]) => drawn.push(a) } as any);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((cb) => cb(new Blob(["x"], { type: "image/png" })));
    await exportPng(svgEl(), "pic");
    expect(drawn).toHaveLength(1); expect(tables.downloadFile).toHaveBeenCalledWith("pic.png", expect.any(Blob), "image/png");
  });
  it("reports why a png could not be made", async () => {
    class BadImage { onload: any; onerror: any; set src(_v: string) { setTimeout(() => this.onerror?.(), 0); } }
    vi.stubGlobal("Image", BadImage);
    await expect(exportPng(svgEl(), "x")).rejects.toThrow(/could not be made/);
    class OkImage { onload: any; onerror: any; set src(_v: string) { setTimeout(() => this.onload?.(), 0); } }
    vi.stubGlobal("Image", OkImage);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    await expect(exportPng(svgEl(), "x")).rejects.toThrow(/cannot make a picture/);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ scale: vi.fn(), drawImage: vi.fn() } as any);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((cb) => cb(null));
    await expect(exportPng(svgEl(), "x")).rejects.toThrow(/could not be made/);
  });
  it("builds a self-contained page that escapes everything", () => {
    const h = htmlPage("A <b>", { svg: "<svg></svg>", table: { head: ["h&"], rows: [["<x>"]] }, note: 'say "hi"' });
    expect(h).toContain("&lt;b&gt;"); expect(h).toContain("<svg></svg>"); expect(h).toContain("h&amp;"); expect(h).toContain("&lt;x&gt;"); expect(h).toContain("&quot;hi&quot;"); expect(h).toContain("default-src 'none'");
    expect(htmlPage("T", {})).not.toContain("<table");
  });
  it("saves a data: URL as a file", () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    downloadDataUrl("pic.png", "data:image/png;base64,AAAA");
    expect(click).toHaveBeenCalled(); expect(document.querySelector("a[download]")).toBeNull();
  });
  it("prints one element and cleans up afterwards", () => {
    const print = vi.fn(); vi.stubGlobal("print", print);
    const el = document.createElement("div"); document.body.appendChild(el);
    printElement(el);
    expect(print).toHaveBeenCalled(); expect(el.classList.contains("ui-print-target")).toBe(true); expect(document.body.classList.contains("ui-printing")).toBe(true);
    window.dispatchEvent(new Event("afterprint"));
    expect(el.classList.contains("ui-print-target")).toBe(false); expect(document.body.classList.contains("ui-printing")).toBe(false);
  });
});
