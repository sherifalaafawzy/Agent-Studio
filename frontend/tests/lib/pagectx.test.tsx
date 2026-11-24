import { render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { applyChange, collectContext, contextText, elementFor, lockReason, previewChange, refOf, registerPageContext, scrub, usePageContext, type PageContext } from "@/lib/pagectx";

function screenOf(html: string) {
  document.body.innerHTML = `<div class="app"><main>${html}</main></div>`;
  return document.querySelector("main") as HTMLElement;
}
const ref = (sel: string) => refOf(document.querySelector(sel)!);
beforeEach(() => { window.history.replaceState({}, "", "/agents/?agent=eng#ag-live"); });
afterEach(() => { document.body.innerHTML = ""; });

describe("collectContext", () => {
  it("reads the route, title, headings, fields, buttons, tabs, selection and errors", () => {
    screenOf(`<h1>Agent Studio</h1><h2>Basics</h2><h3>Tools</h3>
      <label>Name<input id="n" value="Super Engineer"></label>
      <label for="d">Description</label><textarea id="d">Plans work</textarea>
      <label>Tier<select id="t"><option value="pro">Strong</option><option value="flash" selected>Fast</option></select></label>
      <label><input id="c" type="checkbox" checked> Create helpers</label>
      <input aria-label="Max turns" type="number" value="8" required>
      <button>Save</button><button disabled>Delete</button><button class="icon-btn" aria-label="Close">x</button>
      <div role="tablist"><button role="tab" aria-selected="true">Overview</button><button role="tab" aria-selected="false">Tools</button></div>
      <div class="li on">Super Engineer row</div>
      <p role="alert">Something failed</p>`);
    const c = collectContext();
    expect(c.route).toBe("/agents/?agent=eng#ag-live");
    expect(c.title).toBe("Agent Studio");
    expect(c.headings).toEqual(["Basics", "Tools"]);
    const f = Object.fromEntries(c.fields.map((x) => [x.label, x]));
    expect(f["Name"]).toMatchObject({ type: "text", value: "Super Engineer", editable: true });
    expect(f["Description"]).toMatchObject({ type: "textarea", value: "Plans work" });
    expect(f["Tier"]).toMatchObject({ type: "select", value: "flash", options: ["Strong", "Fast"] });
    expect(f["Create helpers"]).toMatchObject({ type: "checkbox", checked: true });
    expect(f["Max turns"]).toMatchObject({ type: "number", value: "8", required: true });
    expect(c.buttons).toEqual(["Save", "Delete (disabled)"]);                 // icon-only buttons are not listed
    expect(c.tabs).toEqual([{ label: "Overview", selected: true }, { label: "Tools", selected: false }]);
    expect(c.selected).toEqual(["Super Engineer row"]);
    expect(c.errors).toEqual(["Something failed"]);
  });

  it("gives every field a stable reference that survives reading the screen again", () => {
    screenOf(`<label>A<input value="1"></label><label>B<input value="2"></label>`);
    const first = collectContext().fields.map((f) => f.ref);
    const again = collectContext().fields.map((f) => f.ref);
    expect(first).toEqual(again);
    expect(new Set(first).size).toBe(2);
    expect(first[0]).toMatch(/^f\d+$/);
  });

  it("never gives the value of a secret and marks it as not changeable", () => {
    screenOf(`<label>Password<input type="password" value="hunter2"></label>
      <label>OpenAI API key<input value="sk-live-aaaaaaaaaaaaaaaa"></label>
      <input name="bearer_token" placeholder="paste" value="tok123">
      <label>Webhook address<input value="https://x/hook/secret"></label>
      <div data-sensitive="1"><label>Anything<input value="private-thing"></label></div>
      <label>OTP<input autocomplete="one-time-code" value="123456"></label>
      <label>Plain<input value="fine"></label>`);
    const c = collectContext();
    const json = JSON.stringify(c);
    for (const secret of ["hunter2", "sk-live-aaaa", "tok123", "hook/secret", "private-thing", "123456"]) expect(json).not.toContain(secret);
    const locked = c.fields.filter((f) => !f.editable);
    expect(locked).toHaveLength(6);
    expect(locked.every((f) => f.value === undefined && f.private)).toBe(true);
    expect(c.fields.find((f) => f.label === "Plain")).toMatchObject({ value: "fine", editable: true });
  });

  it("marks read-only and disabled fields as not changeable but still shows them", () => {
    screenOf(`<label>Id<input value="eng" readonly></label><label>Off<input value="x" disabled></label>`);
    const f = collectContext().fields;
    expect(f.map((x) => [x.label, x.editable, x.note])).toEqual([["Id", false, "read-only"], ["Off", false, "read-only"]]);
    expect(f[0].private).toBe(false);
  });

  it("skips hidden things, overlays and the assistant's own panel", () => {
    screenOf(`<label>Shown<input value="a"></label>
      <div style="display:none"><label>Gone<input value="b"></label></div>
      <div hidden><label>Hid<input value="c"></label></div>
      <div aria-hidden="true"><label>Aria<input value="d"></label></div>
      <label>Inv<input style="visibility:hidden" value="e"></label>
      <input type="hidden" value="h"><input type="file"><input type="submit" value="Go">
      <div data-no-assist><label>Off limits<input value="f"></label></div>
      <aside class="sa-panel"><label>Mine<input value="g"></label></aside>
      <div class="toasts"><p class="toast err">hidden toast error</p></div>`);
    const c = collectContext();
    expect(c.fields.map((f) => f.label)).toEqual(["Shown"]);
    expect(c.errors).toEqual([]);
  });

  it("works out a label from aria, the page, the neighbouring text, the placeholder or the name", () => {
    screenOf(`<span id="lab">Named by id</span><input aria-labelledby="lab" value="1">
      <div class="row"><span>Next to it</span><input value="2"></div>
      <input placeholder="Type here" value="3"><input name="bare_name" value="4"><input id="onlyid" value="5">`);
    expect(collectContext().fields.map((f) => f.label)).toEqual(["Named by id", "Next to it", "Type here", "bare_name", "onlyid"]);
  });

  it("limits how much it reads", () => {
    screenOf(Array.from({ length: 80 }, (_, i) => `<label>F${i}<input value="v"></label><button>B${i}</button><h2>H${i}</h2>`).join(""));
    const c = collectContext();
    expect(c.fields).toHaveLength(60);
    expect(c.buttons).toHaveLength(30);
    expect(c.headings).toHaveLength(14);
  });

  it("falls back to the document title and survives a screen with nothing on it", () => {
    document.title = "Fallback title";
    screenOf("");
    const c = collectContext();
    expect(c.title).toBe("Fallback title");
    expect(c.fields).toEqual([]);
    document.body.innerHTML = "<p>no main</p>";
    expect(collectContext().fields).toEqual([]);
  });

  it("includes facts that pages register, scrubbed of secrets, and survives a broken provider", () => {
    screenOf("<h1>X</h1>");
    const off1 = registerPageContext("agents", () => ({ editing: { id: "eng", api_key: "sk-123", nested: { token: "t", ok: "yes" } } }));
    const off2 = registerPageContext("bad", () => { throw new Error("boom"); });
    const c = collectContext();
    expect(c.registered).toEqual({ agents: { editing: { id: "eng", api_key: "(kept private)", nested: { token: "(kept private)", ok: "yes" } } } });
    off1(); off2();
    expect(collectContext().registered).toEqual({});
  });

  it("registerPageContext only withdraws its own provider", () => {
    const a = () => ({ a: 1 }), b = () => ({ b: 2 });
    const offA = registerPageContext("same", a);
    const offB = registerPageContext("same", b);
    offA();
    screenOf("<h1>X</h1>");
    expect(collectContext().registered).toEqual({ same: { b: 2 } });
    offB();
  });

  it("lets a component register facts with a hook that always reads the newest values", () => {
    function Page() {
      const [n, setN] = useState(1);
      usePageContext("counter", () => ({ n }));
      return <main><h1>Counter</h1><button onClick={() => setN(n + 1)}>Inc</button></main>;
    }
    const { unmount } = render(<Page />);
    expect(collectContext().registered).toEqual({ counter: { n: 1 } });
    screen.getByText("Inc").click();
    return Promise.resolve().then(() => {
      expect(collectContext().registered).toEqual({ counter: { n: 2 } });
      unmount();
      expect(collectContext().registered).toEqual({});
    });
  });
});

describe("scrub", () => {
  it("shortens text, limits depth and length, and hides secret-looking keys", () => {
    expect(scrub(null)).toBeNull();
    expect(scrub(5)).toBe(5);
    expect(scrub(true)).toBe(true);
    expect(scrub("x".repeat(400))).toBe("x".repeat(300) + "…");
    expect(scrub(Array.from({ length: 40 }, (_, i) => i))).toHaveLength(30);
    expect(scrub({ a: { b: { c: { d: { e: 1 } } } } })).toEqual({ a: { b: { c: { d: "…" } } } });
    expect(scrub({ password: "p", Authorization: "a", fine: 1, cookie: "c" })).toEqual({ password: "(kept private)", Authorization: "(kept private)", fine: 1, cookie: "(kept private)" });
    expect(scrub(Symbol.for("x"))).toBe("Symbol(x)");
    expect(Object.keys(scrub(Object.fromEntries(Array.from({ length: 60 }, (_, i) => [`k${i}`, i]))) as object)).toHaveLength(40);
  });
});

describe("contextText", () => {
  const base = (over: Partial<PageContext> = {}): PageContext => ({ route: "/", title: "T", headings: [], fields: [], buttons: [], tabs: [], selected: [], errors: [], registered: {}, ...over });
  it("is plain JSON when it is small", () => {
    expect(JSON.parse(contextText(base({ title: "Hi" })))).toMatchObject({ title: "Hi" });
  });
  it("drops the least useful parts first when it is too large, and never exceeds the limit", () => {
    const big = base({
      buttons: Array.from({ length: 30 }, (_, i) => "button number " + i), headings: Array.from({ length: 14 }, (_, i) => "heading " + i),
      fields: Array.from({ length: 60 }, (_, i) => ({ ref: `f${i}`, label: "Field " + i, type: "textarea", value: "v".repeat(600), editable: true })), registered: { big: "r".repeat(300) },
    });
    const t = contextText(big);
    expect(t.length).toBeLessThanOrEqual(9000);
    const parsed = JSON.parse(contextText(base({ buttons: Array.from({ length: 30 }, (_, i) => "b".repeat(300) + i) })));
    expect(parsed.buttons.length).toBeLessThanOrEqual(30);
    expect(contextText(base({ title: "y".repeat(20000) })).length).toBe(9000);
  });
});

describe("changing a field after approval", () => {
  it("previews and applies a text change, the way a person typing would", () => {
    screenOf(`<label>Name<input id="n" value="old"></label>`);
    const r = ref("#n");
    expect(previewChange({ ref: r, value: "new" })).toEqual({ ref: r, label: "Name", before: "old", after: "new", ok: true });
    expect(applyChange({ ref: r, value: "new" })).toEqual({ ok: true, text: "Name: old -> new" });
    expect((document.querySelector("#n") as HTMLInputElement).value).toBe("new");
  });

  it("is noticed by a React-controlled field", () => {
    function Form() {
      const [v, setV] = useState("start");
      return <main><label>Title<input value={v} onChange={(e) => setV(e.target.value)} /></label><p data-testid="echo">{v}</p></main>;
    }
    render(<Form />);
    const r = refOf(screen.getByLabelText("Title"));
    expect(applyChange({ ref: r, value: "changed" }).ok).toBe(true);
    expect(screen.getByTestId("echo")).toHaveTextContent("changed");
  });

  it("changes a textarea, a select (by value or by words) and a checkbox", () => {
    screenOf(`<label>Text<textarea id="t">a</textarea></label>
      <label>Tier<select id="s"><option value="pro">Strong</option><option value="flash">Fast</option></select></label>
      <label><input id="c" type="checkbox"> Helpers</label>`);
    const t = ref("#t"), s = ref("#s"), c = ref("#c");
    expect(applyChange({ ref: t, value: "multi\nline" }).ok).toBe(true);
    expect((document.querySelector("#t") as HTMLTextAreaElement).value).toBe("multi\nline");
    expect(applyChange({ ref: s, value: "fast" })).toEqual({ ok: true, text: "Tier: Strong -> Fast" });
    expect((document.querySelector("#s") as HTMLSelectElement).value).toBe("flash");
    expect(applyChange({ ref: s, value: "pro" }).ok).toBe(true);
    expect(previewChange({ ref: s, value: "nope" })).toMatchObject({ ok: false, why: '"nope" is not one of the choices' });
    expect(previewChange({ ref: c, value: "yes" })).toMatchObject({ before: "not ticked", after: "ticked", ok: true });
    expect(applyChange({ ref: c, value: true }).ok).toBe(true);
    expect((document.querySelector("#c") as HTMLInputElement).checked).toBe(true);
    expect(applyChange({ ref: c, value: "true" }).ok).toBe(true);          // already ticked: nothing to do
    expect(applyChange({ ref: c, value: false }).ok).toBe(true);
    expect((document.querySelector("#c") as HTMLInputElement).checked).toBe(false);
  });

  it("checks numbers and size", () => {
    screenOf(`<label>Turns<input id="n" type="number" value="3"></label><label>T<input id="x"></label>`);
    const n = ref("#n"), x = ref("#x");
    expect(previewChange({ ref: n, value: "abc" })).toMatchObject({ ok: false, why: "this field takes a number" });
    expect(previewChange({ ref: n, value: "" })).toMatchObject({ ok: true });
    expect(previewChange({ ref: n, value: "12" })).toMatchObject({ ok: true, after: "12" });
    expect(previewChange({ ref: x, value: "y".repeat(20001) })).toMatchObject({ ok: false, why: "the text is too long" });
  });

  it("refuses secrets, read-only fields and fields that are gone", () => {
    screenOf(`<label>API key<input id="k" value="sk-x"></label><label>Id<input id="i" value="eng" readonly></label><label>Pw<input id="p" type="password"></label>`);
    const k = ref("#k"), i = ref("#i"), p = ref("#p");
    expect(previewChange({ ref: k, value: "new" })).toMatchObject({ ok: false, why: "this field holds a secret, so only you can fill it in" });
    expect(previewChange({ ref: p, value: "new" }).ok).toBe(false);
    expect(previewChange({ ref: i, value: "new" })).toMatchObject({ ok: false, why: "this field is read-only" });
    expect(applyChange({ ref: k, value: "new" })).toEqual({ ok: false, text: "API key: this field holds a secret, so only you can fill it in" });
    expect((document.querySelector("#k") as HTMLInputElement).value).toBe("sk-x");
    expect(previewChange({ ref: "f99999", value: "x" })).toMatchObject({ ok: false, why: "that field is not on this screen any more", label: "f99999" });
    expect(previewChange({ ref: "'; drop", value: "x" }).ok).toBe(false);
    (document.querySelector("#i") as HTMLElement).style.display = "none";
    expect(previewChange({ ref: i, value: "x" }).ok).toBe(false);
  });

  it("lockReason explains why", () => {
    screenOf(`<input id="a" type="password"><input id="b" name="session_id"><input id="c" disabled><input id="d" name="ok">`);
    const el = (s: string) => document.querySelector(s) as HTMLInputElement;
    expect(lockReason(el("#a"), "x")).toBe("kept private");
    expect(lockReason(el("#b"), "x")).toBe("kept private");
    expect(lockReason(el("#c"), "x")).toBe("read-only");
    expect(lockReason(el("#d"), "ok")).toBeNull();
  });
});

describe("elementFor", () => {
  it("finds a field by reference and a tour target by name, and only things that are shown", () => {
    screenOf(`<input id="a" value="1"><div data-tour="composer">x</div><div data-tour="gone" style="display:none"></div>`);
    const r = ref("#a");
    expect(elementFor(r)).toBe(document.querySelector("#a"));
    expect(elementFor("composer")).toBe(document.querySelector("[data-tour=composer]"));
    expect(elementFor("gone")).toBeNull();
    expect(elementFor("nope")).toBeNull();
    expect(elementFor("a b")).toBeNull();
    expect(elementFor("f424242")).toBeNull();
  });
});
