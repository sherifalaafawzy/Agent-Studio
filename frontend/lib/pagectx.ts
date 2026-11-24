// What the assistant may know about the screen the user is looking at, and the one thing it may do to it (change a form field, after approval).
//
// Two sources are merged: (1) the visible screen itself, read from the page (title, headings, form fields, tabs, selected rows, errors) and
// (2) pages that register structured facts of their own (registerPageContext). Secrets never leave this file: password and key-like fields are
// listed as "kept private" with no value, can never be changed by the assistant, and registered facts are scrubbed of secret-looking keys.
import { useEffect, useRef } from "react";

export type FieldInfo = {
  ref: string; label: string; type: string; value?: string; options?: string[]; checked?: boolean;
  required?: boolean; editable: boolean; private?: boolean; note?: string;
};
export type PageContext = {
  route: string; title: string; headings: string[]; fields: FieldInfo[]; buttons: string[]; tabs: { label: string; selected: boolean }[];
  selected: string[]; errors: string[]; registered: Record<string, unknown>; shortened?: boolean;
};

export const SECRET_WORDS = /(api[\s_-]?key|secret|token|passw|passphrase|credential|private[\s_-]?key|bearer|authorization|auth[\s_-]?header|service[\s_-]?account|\bsas\b|access[\s_-]?key|signing[\s_-]?key|webhook[\s_-]?(url|address)|cookie|session[\s_-]?id|\bpin\b|\bssn\b|card[\s_-]?number)/i;
const SKIP_TYPES = new Set(["hidden", "submit", "button", "image", "reset", "file", "range", "color"]);
const MAX_TEXT = 600, MAX_FIELDS = 60, MAX_BUTTONS = 30, MAX_HEADINGS = 14, MAX_JSON = 9000;
const OFF_LIMITS = "[data-no-assist],.sa-panel,.sa-fab,.palette,.toasts,.driver-popover";

// ------------------------------------------------------------------------------------------------ pages that describe themselves
const providers = new Map<string, () => Record<string, unknown>>();

/** A page offers structured facts about itself (the open agent, the chosen workflow...). Returns the function that withdraws them. */
export function registerPageContext(id: string, provider: () => Record<string, unknown>): () => void {
  providers.set(id, provider);
  return () => { if (providers.get(id) === provider) providers.delete(id); };
}

/** The same, for a React component: registers while it is on screen and always reads the newest values. */
export function usePageContext(id: string, facts: () => Record<string, unknown>): void {
  const ref = useRef(facts);
  ref.current = facts;
  useEffect(() => registerPageContext(id, () => ref.current()), [id]);
}

/** Cuts a registered value down: secret-looking keys vanish, text is shortened, depth and length are limited. */
export function scrub(v: unknown, depth = 0): unknown {
  if (v == null || typeof v === "boolean" || typeof v === "number") return v;
  if (typeof v === "string") return v.length > 300 ? v.slice(0, 300) + "…" : v;
  if (depth >= 4) return "…";
  if (Array.isArray(v)) return v.slice(0, 30).map((x) => scrub(x, depth + 1));
  if (typeof v === "object") {
    const o: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>).slice(0, 40)) o[k] = SECRET_WORDS.test(k) ? "(kept private)" : scrub(x, depth + 1);
    return o;
  }
  return String(v);
}

// ------------------------------------------------------------------------------------------------ reading the screen
function shown(el: Element): boolean {
  if (el.closest("[hidden],[aria-hidden='true'],[inert]")) return false;
  for (let n: Element | null = el; n; n = n.parentElement) {
    const s = getComputedStyle(n);
    if (s.display === "none" || s.visibility === "hidden") return false;
  }
  return true;
}
/** On screen, and not part of the assistant's own panel or other overlays. */
const visible = (el: Element) => !el.closest(OFF_LIMITS) && shown(el);

const clean = (s: string | null | undefined, n = 120) => (s ?? "").replace(/\s+/g, " ").trim().slice(0, n);

function labelOf(el: HTMLElement): string {
  const aria = el.getAttribute("aria-label");
  if (aria) return clean(aria);
  const by = el.getAttribute("aria-labelledby");
  if (by) { const t = by.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" "); if (clean(t)) return clean(t); }
  if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l && clean(l.textContent)) return clean(l.textContent); }
  const wrap = el.closest("label");
  if (wrap) {                                                       // the label's own words, not the words of other controls inside it
    const t = Array.from(wrap.childNodes).filter((n) => n.nodeType === 3 || (n as Element).tagName === "SPAN" || (n as Element).tagName === "B").map((n) => n.textContent).join(" ");
    if (clean(t)) return clean(t);
  }
  const prev = el.previousElementSibling;
  if (prev && /^(LABEL|SPAN|B|DIV)$/.test(prev.tagName) && prev.children.length === 0 && clean(prev.textContent, 60)) return clean(prev.textContent, 60);
  return clean(el.getAttribute("placeholder") || el.getAttribute("name") || el.id || el.tagName.toLowerCase(), 60);
}

let counter = 0;
export function refOf(el: Element): string {
  let r = el.getAttribute("data-as-ref");
  if (!r) { r = `f${++counter}`; el.setAttribute("data-as-ref", r); }
  return r;
}

type Control = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

/** Why the assistant must not change this field (null = it may, once the user approves). */
export function lockReason(el: Control, label: string): string | null {
  const type = el.getAttribute("type")?.toLowerCase() ?? "";
  if (type === "password" || el.closest("[data-sensitive]") || el.hasAttribute("data-sensitive") || /password|one-time/i.test(el.getAttribute("autocomplete") ?? "")) return "kept private";
  if (SECRET_WORDS.test(`${label} ${el.getAttribute("name") ?? ""} ${el.id} ${el.getAttribute("placeholder") ?? ""}`)) return "kept private";
  if (el.hasAttribute("disabled") || el.hasAttribute("readonly")) return "read-only";
  return null;
}

function describe(el: Control): FieldInfo | null {
  const type = (el.tagName === "SELECT" ? "select" : el.tagName === "TEXTAREA" ? "textarea" : el.getAttribute("type") || "text").toLowerCase();
  if (SKIP_TYPES.has(type)) return null;
  const label = labelOf(el);
  const lock = lockReason(el, label);
  const f: FieldInfo = { ref: refOf(el), label, type, editable: lock === null, required: el.hasAttribute("required") || undefined };
  if (lock) { f.private = lock === "kept private"; f.note = lock; return f; }
  if (type === "checkbox" || type === "radio") f.checked = (el as HTMLInputElement).checked;
  else f.value = clean((el as HTMLInputElement).value, MAX_TEXT);
  if (el.tagName === "SELECT") f.options = Array.from((el as HTMLSelectElement).options).slice(0, 40).map((o) => clean(o.textContent || o.value, 60));
  return f;
}

function selectedRows(): string[] {
  const out: string[] = [];
  document.querySelectorAll("[aria-selected='true']:not([role='tab']),[aria-current='true'],[data-selected='true'],.li.on,.sb-chat.on").forEach((e) => {
    if (visible(e) && !e.closest(".sb")) out.push(clean(e.textContent, 80));
  });
  return [...new Set(out.filter(Boolean))].slice(0, 6);
}

/** Reads the page the user is on. Never includes a secret. */
export function collectContext(): PageContext {
  const route = typeof location === "undefined" ? "/" : location.pathname + location.search + location.hash;
  const main = document.querySelector("main") ?? document.body;
  const h1 = Array.from(main.querySelectorAll("h1,h2")).find((h) => visible(h));
  const title = clean(h1?.textContent, 100) || clean(document.title, 100);
  const headings = Array.from(main.querySelectorAll("h2,h3")).filter((h) => visible(h)).map((h) => clean(h.textContent, 80)).filter(Boolean).slice(0, MAX_HEADINGS);
  const fields: FieldInfo[] = [];
  for (const el of Array.from(main.querySelectorAll<Control>("input,textarea,select"))) {
    if (fields.length >= MAX_FIELDS) break;
    if (!visible(el)) continue;
    const f = describe(el);
    if (f) fields.push(f);
  }
  const buttons = Array.from(main.querySelectorAll("button,[role='button']")).filter((b) => visible(b) && !b.closest(".icon-btn,[role='tab']")).map((b) => {
    const t = clean(b.textContent, 50) || clean(b.getAttribute("aria-label"), 50);
    return t && b.hasAttribute("disabled") ? `${t} (disabled)` : t;
  }).filter(Boolean);
  const tabs = Array.from(main.querySelectorAll("[role='tab'],.tabs button,.seg button")).filter((t) => visible(t)).slice(0, 20)
    .map((t) => ({ label: clean(t.textContent, 40) || clean(t.getAttribute("aria-label"), 40), selected: t.getAttribute("aria-selected") === "true" || t.getAttribute("aria-pressed") === "true" || t.classList.contains("on") }))
    .filter((t) => t.label);
  const errors = Array.from(document.querySelectorAll("[role='alert'],.err,.err-card,.toast.err,.pill.bad")).filter((e) => visible(e)).map((e) => clean(e.textContent, 200)).filter(Boolean);
  const registered: Record<string, unknown> = {};
  for (const [id, fn] of providers) { try { registered[id] = scrub(fn()); } catch { /* a page's own facts failing must not break the assistant */ } }
  return { route, title, headings, fields, buttons: [...new Set(buttons)].slice(0, MAX_BUTTONS), tabs, selected: selectedRows(), errors: [...new Set(errors)].slice(0, 6), registered };
}

/** The context as text for the model, kept under a size limit by dropping the least useful parts first. */
export function contextText(c: PageContext): string {
  let x = { ...c };
  const trims: ((k: PageContext) => PageContext)[] = [
    (k) => ({ ...k, buttons: k.buttons.slice(0, 12) }), (k) => ({ ...k, headings: k.headings.slice(0, 6) }),
    (k) => ({ ...k, fields: k.fields.slice(0, 30), shortened: true }), (k) => ({ ...k, fields: k.fields.slice(0, 15).map((f) => ({ ...f, value: f.value?.slice(0, 120) })), shortened: true }),
    (k) => ({ ...k, registered: {}, shortened: true }),
  ];
  let s = JSON.stringify(x);
  for (const t of trims) { if (s.length <= MAX_JSON) break; x = t(x); s = JSON.stringify(x); }
  return s.length > MAX_JSON ? s.slice(0, MAX_JSON) : s;
}

// ------------------------------------------------------------------------------------------------ the one edit: a form field, after approval
export type FieldChange = { ref: string; value: string | boolean };
export type FieldPreview = { ref: string; label: string; before: string; after: string; ok: boolean; why?: string };

function find(ref: string): Control | null {
  if (!/^f\d+$/.test(ref)) return null;
  const el = document.querySelector<Control>(`[data-as-ref="${ref}"]`);
  return el && visible(el) ? el : null;
}

const asBool = (v: string | boolean) => (typeof v === "boolean" ? v : /^(true|yes|on|1|checked)$/i.test(v.trim()));

function pickOption(el: HTMLSelectElement, v: string): HTMLOptionElement | null {
  const want = v.trim().toLowerCase();
  const opts = Array.from(el.options);
  return opts.find((o) => o.value.toLowerCase() === want) ?? opts.find((o) => clean(o.textContent).toLowerCase() === want) ?? null;
}

/** Checks one proposed edit against the live page and describes it as before and after. Nothing is changed. */
export function previewChange(c: FieldChange): FieldPreview {
  const el = find(c.ref);
  const bad = (why: string, label = c.ref): FieldPreview => ({ ref: c.ref, label, before: "", after: "", ok: false, why });
  if (!el) return bad("that field is not on this screen any more");
  const label = labelOf(el);
  const lock = lockReason(el, label);
  if (lock === "kept private") return bad("this field holds a secret, so only you can fill it in", label);
  if (lock) return bad(`this field is ${lock}`, label);
  const type = (el.getAttribute("type") ?? "").toLowerCase();
  if (type === "checkbox" || type === "radio") {
    const now = (el as HTMLInputElement).checked, want = asBool(c.value);
    return { ref: c.ref, label, before: now ? "ticked" : "not ticked", after: want ? "ticked" : "not ticked", ok: true };
  }
  const text = String(c.value);
  if (text.length > 20000) return bad("the text is too long", label);
  if (el.tagName === "SELECT") {
    const o = pickOption(el as HTMLSelectElement, text);
    if (!o) return bad(`"${clean(text, 40)}" is not one of the choices`, label);
    const cur = (el as HTMLSelectElement).selectedOptions[0];
    return { ref: c.ref, label, before: clean(cur?.textContent || ""), after: clean(o.textContent || o.value), ok: true };
  }
  if (type === "number" && text.trim() !== "" && Number.isNaN(Number(text))) return bad("this field takes a number", label);
  return { ref: c.ref, label, before: (el as HTMLInputElement).value, after: text, ok: true };
}

/** Applies one approved edit the way a person typing would, so the page's own code notices it. */
export function applyChange(c: FieldChange): { ok: boolean; text: string } {
  const p = previewChange(c);
  if (!p.ok) return { ok: false, text: `${p.label}: ${p.why}` };
  const el = find(c.ref) as Control;
  const type = (el.getAttribute("type") ?? "").toLowerCase();
  if (type === "checkbox" || type === "radio") {
    if ((el as HTMLInputElement).checked !== asBool(c.value)) (el as HTMLInputElement).click();
  } else if (el.tagName === "SELECT") {
    const o = pickOption(el as HTMLSelectElement, String(c.value)) as HTMLOptionElement;
    setNative(el, o.value);
    el.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    setNative(el, String(c.value));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  return { ok: true, text: `${p.label}: ${p.before || "(empty)"} -> ${p.after || "(empty)"}` };
}

function setNative(el: Control, value: string) {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);       // React ignores a plain `el.value = ...`; the native setter plus an event is what it listens to
}

/** Marks a field the assistant may point at but whose element has a name instead of a ref. Used by "highlight". */
export function elementFor(target: string): Element | null {
  if (/^f\d+$/.test(target)) return find(target);
  if (!/^[\w-]+$/.test(target)) return null;
  const el = document.querySelector(`[data-tour="${target}"]`);
  return el && shown(el) ? el : null;
}
