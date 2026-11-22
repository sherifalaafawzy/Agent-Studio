// Tables in answers: parsing (CSV / TSV / the structured ```table block), sorting, and the four export formats (CSV, JSON, Excel, Markdown).
// Everything runs in the browser and builds files from data only; nothing here evaluates a cell.

export type ColType = "text" | "number" | "date" | "boolean";
export type Cell = string | number | boolean | null;
export type Col = { key: string; label: string; type: ColType };
export type Table = { title: string; columns: Col[]; rows: Cell[][]; truncated: boolean };

export const MAX_ROWS = 5000, MAX_COLS = 50, MAX_CELL = 2000;
const NUMBER = /^-?\d+(\.\d+)?$/, DATE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?/;
const TYPES = ["text", "number", "date", "boolean"];

/** Split one CSV / TSV line, honouring "quoted, values". */
export function splitLine(line: string, sep: string): string[] {
  const out: string[] = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) { if (c === '"') { if (line[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += c; }
    else if (c === '"') q = true;
    else if (c === sep) { out.push(cur); cur = ""; }
    else cur += c;
  }
  return [...out, cur];
}

const cell = (v: unknown): Cell => (v === null || v === undefined ? null : typeof v === "boolean" || typeof v === "number" ? v : String(v).slice(0, MAX_CELL));

/** Text / number / boolean / date, from the non-empty values of one column. */
export function kindOf(values: Cell[]): ColType {
  const vals = values.filter((v) => v !== null && v !== "");
  if (!vals.length) return "text";
  if (vals.every((v) => typeof v === "boolean" || String(v).toLowerCase() === "true" || String(v).toLowerCase() === "false")) return "boolean";
  if (vals.every((v) => typeof v === "number" || (typeof v === "string" && NUMBER.test(v.trim())))) return "number";
  if (vals.every((v) => typeof v === "string" && DATE.test(v.trim()))) return "date";
  return "text";
}

/** A table from {columns, rows}: rows as arrays or as objects keyed by column. Anything else is refused with a reason. */
export function parseTable(raw: unknown, title = ""): { table?: Table; error?: string } {
  const r = raw as any;
  if (!r || typeof r !== "object" || Array.isArray(r) || !Array.isArray(r.rows)) return { error: "a table is a JSON object with a rows list" };
  let colsIn: unknown[] = Array.isArray(r.columns) ? r.columns : [];
  if (!colsIn.length) {
    if (r.rows[0] && typeof r.rows[0] === "object" && !Array.isArray(r.rows[0])) colsIn = Object.keys(r.rows[0]);
    else return { error: "give the columns, or rows as objects" };
  }
  const columns: Col[] = colsIn.slice(0, MAX_COLS).map((c: any, i) => {
    if (c && typeof c === "object") {
      const key = String(c.key ?? c.label ?? i).slice(0, 60);
      return { key, label: String(c.label ?? key).slice(0, 80), type: TYPES.includes(c.type) ? c.type : ("" as ColType) };
    }
    return { key: String(c).slice(0, 60), label: String(c).slice(0, 80), type: "" as ColType };
  });
  const rows: Cell[][] = [];
  for (const row of r.rows.slice(0, MAX_ROWS)) {
    if (Array.isArray(row)) rows.push(columns.map((_, i) => (i < row.length ? cell(row[i]) : null)));
    else if (row && typeof row === "object") rows.push(columns.map((c) => cell((row as any)[c.key])));
    else return { error: "each row is a list or an object" };
  }
  columns.forEach((c, i) => { if (!c.type) c.type = kindOf(rows.map((x) => x[i])); });
  return { table: { title: String(r.title ?? title).slice(0, 100), columns, rows, truncated: r.rows.length > MAX_ROWS } };
}

/** A table from CSV / TSV text (the first line is the header). */
export function tableFromDelimited(text: string, sep: string): Table | null {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter((l) => l.trim());
  if (!lines.length) return null;
  const head = splitLine(lines[0], sep).slice(0, MAX_COLS);
  const body = lines.slice(1, MAX_ROWS + 1).map((l) => splitLine(l, sep).slice(0, MAX_COLS));
  const { table } = parseTable({ columns: head, rows: body });
  return { ...table!, truncated: lines.length - 1 > MAX_ROWS };      // cannot fail: the columns and rows given are always lists
}

/** Order two cells for sorting: numbers by value, everything else as text; empty cells always last. */
export function compareCells(a: Cell, b: Cell, type: ColType): number {
  const ea = a === null || a === "", eb = b === null || b === "";
  if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1;
  if (type === "number") return Number(a) - Number(b);
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

export const cellText = (v: Cell) => (v === null ? "" : String(v));

// ---------------------------------------------------------------------------------------------------- exports
/** A cell that starts with = + - or @ would be read as a formula by a spreadsheet: text cells get a leading apostrophe so they stay text. */
const safeText = (v: string) => (/^[=+\-@\t\r]/.test(v) ? `'${v}` : v);
const csvCell = (v: Cell, type: ColType) => {
  const s = v === null ? "" : type === "text" && typeof v === "string" ? safeText(v) : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const toCsv = (cols: Col[], rows: Cell[][]) => [cols.map((c) => csvCell(c.label, "text")), ...rows.map((r) => r.map((v, i) => csvCell(v, cols[i].type)))].map((r) => r.join(",")).join("\r\n") + "\r\n";
export const toJson = (cols: Col[], rows: Cell[][]) => JSON.stringify(rows.map((r) => Object.fromEntries(cols.map((c, i) => [c.label, r[i]]))), null, 2);
const mdCell = (v: Cell) => cellText(v).replace(/\\/g, "\\\\").replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
export const toMarkdown = (cols: Col[], rows: Cell[][]) =>
  [`| ${cols.map((c) => mdCell(c.label)).join(" | ")} |`, `| ${cols.map((c) => (c.type === "number" ? "---:" : "---")).join(" | ")} |`, ...rows.map((r) => `| ${r.map(mdCell).join(" | ")} |`)].join("\n") + "\n";
export const toTsv = (cols: Col[], rows: Cell[][]) => [cols.map((c) => c.label), ...rows.map((r) => r.map(cellText))].map((r) => r.map((v) => v.replace(/[\t\r\n]+/g, " ")).join("\t")).join("\n");

// --- a minimal .xlsx: a ZIP of five small XML parts (stored, not compressed)
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
export function crc32(data: Uint8Array): number { let c = 0xffffffff; for (const b of data) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
const xml = (s: string) => s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
export const colLetter = (i: number) => { let s = ""; for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s; return s; };
const sheetName = (n: string) => n.replace(/[\][:*?/\\]/g, " ").trim().slice(0, 31) || "Sheet1";

/** A minimal ZIP (no compression) of the given files. */
export function zip(files: { name: string; data: Uint8Array }[]): Uint8Array {
  const enc = new TextEncoder(), parts: Uint8Array[] = [], central: Uint8Array[] = [];
  let offset = 0;
  const u16 = (v: number) => [v & 255, (v >>> 8) & 255], u32 = (v: number) => [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255];
  for (const f of files) {
    const name = enc.encode(f.name), crc = crc32(f.data);
    const local = new Uint8Array([...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(f.data.length), ...u32(f.data.length), ...u16(name.length), ...u16(0), ...name]);
    central.push(new Uint8Array([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(f.data.length), ...u32(f.data.length), ...u16(name.length),
      ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset), ...name]));
    parts.push(local, f.data);
    offset += local.length + f.data.length;
  }
  const cdSize = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length), ...u32(cdSize), ...u32(offset), ...u16(0)]);
  const all = [...parts, ...central, end], out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of all) { out.set(p, at); at += p.length; }
  return out;
}

export function toXlsx(cols: Col[], rows: Cell[][], title = "Sheet1"): Uint8Array {
  const enc = new TextEncoder();
  const cellXml = (v: Cell, r: number, c: number) => {
    const ref = `${colLetter(c)}${r}`;
    if (v === null || v === "") return "";
    if (typeof v === "number" && Number.isFinite(v)) return `<c r="${ref}"><v>${v}</v></c>`;
    if (typeof v === "boolean") return `<c r="${ref}" t="b"><v>${v ? 1 : 0}</v></c>`;
    return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xml(String(v))}</t></is></c>`;
  };
  const head = `<row r="1">${cols.map((c, i) => cellXml(c.label, 1, i)).join("")}</row>`;
  const body = rows.map((r, ri) => `<row r="${ri + 2}">${r.map((v, ci) => cellXml(cols[ci].type === "number" && typeof v === "string" && NUMBER.test(v.trim()) ? Number(v) : v, ri + 2, ci)).join("")}</row>`).join("");
  const files = [
    ["[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>`],
    ["_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${xml(sheetName(title))}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
    ["xl/_rels/workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`],
    ["xl/worksheets/sheet1.xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${head}${body}</sheetData></worksheet>`],
  ];
  return zip(files.map(([name, text]) => ({ name, data: enc.encode(text) })));
}

/** Save generated data as a file through the browser's download. */
export function downloadFile(name: string, data: BlobPart, mime: string) {
  const url = URL.createObjectURL(new Blob([data], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.rel = "noopener";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const fileSafe = (s: string, fallback: string) => s.replace(/[^\w .-]+/g, "").trim().slice(0, 50) || fallback;
