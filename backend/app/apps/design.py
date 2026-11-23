"""Looks and parts for apps: colour themes, fonts, corner styles and a small library of ready-made components.

Everything is plain CSS and JavaScript that lives in the app's own `public/` folder (no remote fonts, no libraries, no inline script), so an app that uses
them still passes the preview's Content-Security-Policy and exports as ordinary files. A theme is one marked block at the END of `public/style.css`
(it overrides the template's own colours); applying another theme replaces that block.
"""
from __future__ import annotations

import re

KEYS = ("bg", "fg", "muted", "card", "line", "accent", "accent-fg")


def _t(label: str, light: tuple, dark: tuple, note: str = "") -> dict:
    return dict(label=label, light=dict(zip(KEYS, light)), dark=dict(zip(KEYS, dark)), note=note)


THEMES: dict[str, dict] = {
    "warm": _t("Warm", ("#fbfaf8", "#1f1d1a", "#6b665e", "#ffffff", "#e3dfd8", "#c4552d", "#ffffff"), ("#1a1816", "#f1ede6", "#a39d92", "#24211e", "#3a3631", "#e0724a", "#1a1816"), "the standard look"),
    "ocean": _t("Ocean", ("#f5f9fc", "#13222f", "#58707f", "#ffffff", "#d6e3ec", "#0b6fa4", "#ffffff"), ("#0e1a24", "#e6f0f7", "#8fa7b8", "#142431", "#243a4b", "#4cb3e6", "#0a1620")),
    "forest": _t("Forest", ("#f6f9f4", "#18261b", "#5d7260", "#ffffff", "#dbe5d6", "#2f7d4f", "#ffffff"), ("#111a13", "#e8f1e6", "#9bb09e", "#17231a", "#2a3d2e", "#5fc283", "#0d1710")),
    "slate": _t("Slate", ("#f7f8fa", "#1c2230", "#667085", "#ffffff", "#e1e5ec", "#3b5bdb", "#ffffff"), ("#151a24", "#e9ecf3", "#9aa3b5", "#1b2230", "#2c3547", "#7c96f5", "#0f1420")),
    "midnight": _t("Midnight", ("#12151c", "#e8eaf0", "#9aa0b0", "#1a1e28", "#2b3040", "#8b7cf6", "#10121a"), ("#0c0e14", "#eceef4", "#9aa0b0", "#141824", "#252a3a", "#9d90ff", "#0c0e14"), "dark in both modes"),
    "rose": _t("Rose", ("#fdf7f8", "#2a1a1f", "#7a5f66", "#ffffff", "#f0dde1", "#c2255c", "#ffffff"), ("#1c1114", "#f6e9ec", "#b49aa1", "#26181c", "#412a31", "#f06595", "#1c1114")),
    "sunrise": _t("Sunrise", ("#fffaf2", "#2b2112", "#7a6a4f", "#ffffff", "#f0e4cc", "#d9730d", "#ffffff"), ("#1b1509", "#f7ecd6", "#b8a888", "#251d0e", "#40341b", "#f59f3a", "#1b1509")),
    "mono": _t("Monochrome", ("#fafafa", "#111111", "#6a6a6a", "#ffffff", "#e0e0e0", "#111111", "#ffffff"), ("#111111", "#f2f2f2", "#9a9a9a", "#1a1a1a", "#333333", "#f2f2f2", "#111111")),
}
FONTS = {
    "system": ("System", "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"),
    "serif": ("Serif", "Georgia, 'Times New Roman', serif"),
    "mono": ("Monospace", "ui-monospace, 'Cascadia Code', Consolas, monospace"),
    "rounded": ("Rounded", "ui-rounded, 'SF Pro Rounded', 'Segoe UI', system-ui, sans-serif"),
}
RADII = {"sharp": "4px", "soft": "12px", "round": "20px"}
MARK_START, MARK_END = "/* studio-theme:start */", "/* studio-theme:end */"
BLOCK = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?", re.S)


def catalog() -> dict:
    return dict(themes=[dict(id=k, label=v["label"], note=v["note"], light=v["light"], dark=v["dark"]) for k, v in THEMES.items()],
                fonts=[dict(id=k, label=v[0]) for k, v in FONTS.items()], radii=list(RADII))


def theme_block(theme: str = "warm", font: str = "system", radius: str = "soft") -> str:
    if theme not in THEMES:
        raise ValueError(f"theme must be one of: {', '.join(THEMES)}")
    if font not in FONTS:
        raise ValueError(f"font must be one of: {', '.join(FONTS)}")
    if radius not in RADII:
        raise ValueError(f"radius must be one of: {', '.join(RADII)}")
    t = THEMES[theme]
    vars_ = lambda d: " ".join(f"--{k}: {v};" for k, v in d.items())          # noqa: E731
    return (f"{MARK_START}\n/* theme: {theme}, font: {font}, corners: {radius} */\n"
            f":root {{ {vars_(t['light'])} --radius: {RADII[radius]}; --font: {FONTS[font][1]}; }}\n"
            f"@media (prefers-color-scheme: dark) {{ :root {{ {vars_(t['dark'])} }} }}\n"
            f"body {{ font-family: var(--font); }}\n{MARK_END}\n")


def apply_theme(css: str, theme: str = "warm", font: str = "system", radius: str = "soft") -> str:
    """The stylesheet with the theme block replaced (or added at the end)."""
    block = theme_block(theme, font, radius)
    if BLOCK.search(css):
        return BLOCK.sub(lambda _m: block, css, count=1)
    return css.rstrip("\n") + "\n\n" + block


def current_theme(css: str) -> dict:
    m = re.search(r"/\* theme: (\w+), font: (\w+), corners: (\w+) \*/", css or "")
    return dict(theme=m.group(1), font=m.group(2), radius=m.group(3)) if m else dict(theme="", font="", radius="")


# ----------------------------------------------------------------------------------------------------------------- components
UI_CSS = {
    "navbar": """.ui-nav { display: flex; align-items: center; gap: 12px; padding: 10px 16px; background: var(--card); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 5; }
.ui-nav b { font-size: 1.05rem; } .ui-nav nav { display: flex; gap: 4px; margin-left: auto; flex-wrap: wrap; }
.ui-nav a { color: var(--muted); text-decoration: none; padding: 6px 10px; border-radius: calc(var(--radius, 12px) / 1.5); } .ui-nav a:hover, .ui-nav a[aria-current] { color: var(--fg); background: var(--bg); }""",
    "hero": """.ui-hero { text-align: center; padding: 48px 16px; } .ui-hero h1 { font-size: clamp(1.6rem, 5vw, 2.6rem); margin: 0 0 8px; } .ui-hero p { color: var(--muted); max-width: 52ch; margin: 0 auto 18px; }
.ui-hero .ui-actions { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }""",
    "stats": """.ui-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 12px 0; }
.ui-stat { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius, 12px); padding: 14px 16px; } .ui-stat .label { color: var(--muted); font-size: .85rem; }
.ui-stat .value { font-size: 1.7rem; font-weight: 650; line-height: 1.2; } .ui-stat .delta { font-size: .85rem; } .ui-stat .delta.up { color: #2f8f5b; } .ui-stat .delta.down { color: #b3261e; }""",
    "table": """.ui-table-wrap { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius, 12px); padding: 10px; overflow-x: auto; }
.ui-table-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; } .ui-table-bar input { flex: 1; min-width: 140px; font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line); border-radius: calc(var(--radius, 12px) / 1.5); padding: 8px 10px; }
.ui-table-bar button, .ui-table-foot button { padding: 6px 12px; font-size: .9rem; }
.ui-table { width: 100%; border-collapse: collapse; font-size: .95rem; } .ui-table th, .ui-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
.ui-table th { cursor: pointer; user-select: none; color: var(--muted); font-weight: 600; } .ui-table th[aria-sort] { color: var(--fg); } .ui-table tr:hover td { background: var(--bg); }
.ui-table-foot { display: flex; gap: 8px; align-items: center; justify-content: space-between; margin-top: 8px; color: var(--muted); font-size: .85rem; }""",
    "chart": """.ui-chart { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius, 12px); padding: 12px; } .ui-chart h3 { margin: 0 0 6px; font-size: 1rem; }
.ui-chart svg { width: 100%; height: auto; display: block; } .ui-chart .axis { stroke: var(--line); } .ui-chart .tick { fill: var(--muted); font-size: 11px; }
.ui-chart .bar, .ui-chart .dot { fill: var(--accent); } .ui-chart .line { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-linejoin: round; }""",
    "tabs": """.ui-tabs [role=tablist] { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 12px; flex-wrap: wrap; }
.ui-tabs [role=tab] { background: none; color: var(--muted); border: 0; border-bottom: 2px solid transparent; border-radius: 0; padding: 8px 12px; } .ui-tabs [role=tab][aria-selected=true] { color: var(--fg); border-bottom-color: var(--accent); }""",
    "modal": """.ui-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 50; }
.ui-modal { background: var(--card); color: var(--fg); border: 1px solid var(--line); border-radius: var(--radius, 12px); padding: 18px; max-width: 480px; width: 100%; max-height: 90vh; overflow: auto; }
.ui-modal h3 { margin: 0 0 8px; } .ui-modal .ui-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }""",
    "toast": """.ui-toasts { position: fixed; right: 12px; bottom: 12px; display: flex; flex-direction: column; gap: 8px; z-index: 60; max-width: min(360px, calc(100vw - 24px)); }
.ui-toast { background: var(--fg); color: var(--bg); padding: 10px 14px; border-radius: calc(var(--radius, 12px) / 1.3); box-shadow: 0 4px 16px rgba(0,0,0,.25); } .ui-toast.error { background: #b3261e; color: #fff; } .ui-toast.ok { background: #2f8f5b; color: #fff; }""",
    "form": """.ui-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; } .ui-field label { font-weight: 600; font-size: .9rem; } .ui-field .hint { color: var(--muted); font-size: .85rem; }
.ui-field input, .ui-field select, .ui-field textarea { font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line); border-radius: calc(var(--radius, 12px) / 1.5); padding: 9px 10px; }
.ui-field .error { color: #b3261e; font-size: .85rem; } .ui-field.invalid input { border-color: #b3261e; }""",
    "empty": """.ui-empty { text-align: center; color: var(--muted); padding: 32px 16px; border: 1px dashed var(--line); border-radius: var(--radius, 12px); } .ui-empty b { display: block; color: var(--fg); margin-bottom: 4px; }""",
}
UI_JS = {
    "table": r"""  // UI.table(element, { columns: [{ key, label }], rows: [{...}], pageSize: 10, filename: "data" })  sortable, searchable, paged, with Copy / Download CSV
  UI.table = function (el, o) {
    var cols = o.columns, all = o.rows || [], size = o.pageSize || 10, page = 0, q = "", sort = null;
    el.className = "ui-table-wrap"; el.textContent = "";
    var bar = h("div", "ui-table-bar"), search = h("input"); search.type = "search"; search.placeholder = "Search"; search.setAttribute("aria-label", "Search the table");
    var copy = h("button", "", "Copy CSV"), dl = h("button", "", "Download CSV"); bar.append(search, copy, dl);
    var table = h("table", "ui-table"), foot = h("div", "ui-table-foot"); el.append(bar, table, foot);
    function view() {
      var rows = all.filter(function (r) { return !q || cols.some(function (c) { return String(r[c.key] == null ? "" : r[c.key]).toLowerCase().indexOf(q) >= 0; }); });
      if (sort) rows = rows.slice().sort(function (a, b) { var x = a[sort.key], y = b[sort.key]; var r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y)); return sort.dir * r; });
      return rows;
    }
    function csv(rows) { function q2(v) { v = v == null ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; } return [cols.map(function (c) { return q2(c.label); }).join(",")].concat(rows.map(function (r) { return cols.map(function (c) { return q2(r[c.key]); }).join(","); })).join("\n"); }
    function draw() {
      var rows = view(), pages = Math.max(1, Math.ceil(rows.length / size)); page = Math.min(page, pages - 1);
      table.textContent = "";
      var head = h("tr"); cols.forEach(function (c) { var th = h("th", "", c.label); th.tabIndex = 0; if (sort && sort.key === c.key) th.setAttribute("aria-sort", sort.dir > 0 ? "ascending" : "descending");
        function go() { sort = sort && sort.key === c.key ? { key: c.key, dir: -sort.dir } : { key: c.key, dir: 1 }; draw(); } th.onclick = go; th.onkeydown = function (e) { if (e.key === "Enter") go(); }; head.append(th); });
      var thead = h("thead"); thead.append(head); table.append(thead);
      var tb = h("tbody"); rows.slice(page * size, page * size + size).forEach(function (r) { var tr = h("tr"); cols.forEach(function (c) { tr.append(h("td", "", r[c.key] == null ? "" : r[c.key])); }); tb.append(tr); }); table.append(tb);
      foot.textContent = ""; var info = h("span", "", rows.length + " row" + (rows.length === 1 ? "" : "s")), pg = h("span"), prev = h("button", "", "Back"), next = h("button", "", "Next");
      prev.disabled = page === 0; next.disabled = page >= pages - 1; prev.onclick = function () { page--; draw(); }; next.onclick = function () { page++; draw(); };
      pg.append(prev, " " + (page + 1) + " / " + pages + " ", next); foot.append(info, pg);
    }
    search.oninput = function () { q = search.value.toLowerCase(); page = 0; draw(); };
    copy.onclick = function () { var t = csv(view()); if (navigator.clipboard) navigator.clipboard.writeText(t).then(function () { UI.toast("Copied", "ok"); }, function () { UI.toast("Copy was blocked here", "error"); }); };
    dl.onclick = function () { var a = h("a"); a.href = URL.createObjectURL(new Blob([csv(view())], { type: "text/csv" })); a.download = (o.filename || "data") + ".csv"; a.click(); };
    draw(); return { setRows: function (r) { all = r; page = 0; draw(); } };
  };
""",
    "chart": r"""  // UI.chart(element, { type: "bar" | "line", title: "Sales", labels: ["Jan", ...], values: [1, 2, ...] })  a small SVG chart
  UI.chart = function (el, o) {
    var W = 520, H = 220, pad = 30, vals = o.values || [], n = vals.length, max = Math.max.apply(null, vals.concat([1])), min = Math.min.apply(null, vals.concat([0])), span = max - min || 1;
    el.className = "ui-chart"; el.textContent = ""; if (o.title) el.append(h("h3", "", o.title));
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.title || "Chart" });
    s.append(svg("line", { "class": "axis", x1: pad, y1: H - pad, x2: W - 8, y2: H - pad }));
    var step = (W - pad - 8) / Math.max(n, 1), y = function (v) { return H - pad - ((v - min) / span) * (H - pad * 2); }, pts = [];
    vals.forEach(function (v, i) {
      var x = pad + step * i + step / 2;
      if (o.type === "line") pts.push(x + "," + y(v)); else s.append(svg("rect", { "class": "bar", x: x - step * 0.35, y: y(v), width: step * 0.7, height: Math.max(1, H - pad - y(v)), rx: 3 }));
      var t = svg("text", { "class": "tick", x: x, y: H - pad + 14, "text-anchor": "middle" }); t.textContent = (o.labels || [])[i] == null ? "" : o.labels[i]; s.append(t);
    });
    if (o.type === "line") { s.append(svg("polyline", { "class": "line", points: pts.join(" ") })); pts.forEach(function (p) { var c = p.split(","); s.append(svg("circle", { "class": "dot", cx: c[0], cy: c[1], r: 3.5 })); }); }
    var top = svg("text", { "class": "tick", x: pad - 4, y: pad - 12, "text-anchor": "end" }); top.textContent = String(max); s.append(top); el.append(s);
  };
""",
    "tabs": r"""  // Markup: <div class="ui-tabs" data-ui-tabs><div role="tablist"><button role="tab">One</button><button role="tab">Two</button></div><div role="tabpanel">...</div><div role="tabpanel">...</div></div>
  function tabs(root) {
    var btns = root.querySelectorAll("[role=tab]"), panels = root.querySelectorAll("[role=tabpanel]");
    function pick(i) { btns.forEach(function (b, j) { b.setAttribute("aria-selected", j === i ? "true" : "false"); }); panels.forEach(function (p, j) { p.hidden = j !== i; }); }
    btns.forEach(function (b, i) { b.onclick = function () { pick(i); }; }); pick(0);
  }
  UI.tabs = tabs;
  document.addEventListener("DOMContentLoaded", function () { document.querySelectorAll("[data-ui-tabs]").forEach(tabs); });
""",
    "modal": r"""  // UI.modal({ title: "Delete?", body: "This cannot be undone.", actions: [{ label: "Cancel" }, { label: "Delete", primary: true, onClick: function () { ... } }] })
  UI.modal = function (o) {
    var back = h("div", "ui-backdrop"), box = h("div", "ui-modal"), act = h("div", "ui-actions"); box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
    function close() { back.remove(); }
    box.append(h("h3", "", o.title || ""), typeof o.body === "string" ? h("p", "", o.body) : o.body || "");
    (o.actions || [{ label: "Close" }]).forEach(function (a) { var b = h("button", "", a.label); b.onclick = function () { close(); if (a.onClick) a.onClick(); }; act.append(b); });
    back.onclick = function (e) { if (e.target === back) close(); }; box.append(act); back.append(box); document.body.append(back); var f = box.querySelector("button"); if (f) f.focus(); return { close: close };
  };
""",
    "toast": r"""  // UI.toast("Saved", "ok")  kind: "ok" | "error" | "" (a short message that goes away by itself)
  UI.toast = function (text, kind) {
    var box = document.querySelector(".ui-toasts") || document.body.appendChild(h("div", "ui-toasts")); box.setAttribute("role", "status");
    var t = h("div", "ui-toast " + (kind || ""), text); box.append(t); setTimeout(function () { t.remove(); }, 3500);
  };
""",
}
HELPERS = r"""(function (root) {
  "use strict";
  var UI = root.UI = root.UI || {};
  function h(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null && text !== "") e.textContent = text; return e; }
  function svg(tag, attrs) { var e = document.createElementNS("http" + "://www.w3.org/2000/svg", tag); Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); }); return e; }
  UI.h = h;
"""
USAGE = {
    "navbar": '<header class="ui-nav"><b>My app</b><nav><a href="index.html" aria-current="page">Home</a><a href="about.html">About</a></nav></header>',
    "hero": '<section class="ui-hero"><h1>Headline</h1><p>One sentence about what this does.</p><div class="ui-actions"><button>Get started</button></div></section>',
    "stats": '<div class="ui-stats"><div class="ui-stat"><div class="label">Revenue</div><div class="value">$12.4k</div><div class="delta up">+8%</div></div></div>',
    "table": '<div id="orders"></div>   then in your script:  UI.table(document.getElementById("orders"), { columns: [{ key: "name", label: "Name" }], rows: [{ name: "A" }] });',
    "chart": '<div id="sales"></div>   then in your script:  UI.chart(document.getElementById("sales"), { type: "bar", title: "Sales", labels: ["Jan", "Feb"], values: [4, 9] });',
    "tabs": '<div class="ui-tabs" data-ui-tabs><div role="tablist"><button role="tab">One</button><button role="tab">Two</button></div><div role="tabpanel">First</div><div role="tabpanel">Second</div></div>',
    "modal": 'UI.modal({ title: "Delete it?", body: "This cannot be undone.", actions: [{ label: "Cancel" }, { label: "Delete", onClick: function () {} }] });',
    "toast": 'UI.toast("Saved", "ok");',
    "form": '<div class="ui-field"><label for="email">Email</label><input id="email" type="email"><span class="hint">We never share it.</span></div>',
    "empty": '<div class="ui-empty"><b>Nothing here yet</b>Add your first item to see it here.</div>',
}
COMPONENTS = {
    "navbar": "Top bar with the name and links", "hero": "Big headline with a short line and buttons", "stats": "Cards with a number, a label and a change",
    "table": "Searchable, sortable, paged table with CSV copy and download", "chart": "Bar or line chart (SVG, no library)", "tabs": "Tabs that switch panels",
    "modal": "A dialog with buttons", "toast": "Short messages that fade away", "form": "Labelled fields with hints and errors", "empty": "A friendly empty state",
}
UI_CSS_FILE, UI_JS_FILE = "public/ui.css", "public/ui.js"


def component_list() -> list[dict]:
    return [dict(id=k, description=v, usage=USAGE[k], needs_js=k in UI_JS) for k, v in COMPONENTS.items()]


def bundle(names: list[str]) -> tuple[str, str]:
    """(css, js) for the chosen components, in the library's fixed order. Unknown names raise ValueError."""
    bad = [n for n in names if n not in COMPONENTS]
    if bad:
        raise ValueError(f"unknown component '{bad[0]}': choose from {', '.join(COMPONENTS)}")
    want = [k for k in COMPONENTS if k in names]
    css = "/* Agent Studio components: " + ", ".join(want) + " (edit freely) */\n" + "\n".join(UI_CSS[k] for k in want if k in UI_CSS) + "\n"
    js_parts = [UI_JS[k] for k in want if k in UI_JS]
    js = "// Agent Studio components: " + ", ".join(want) + " (edit freely)\n" + HELPERS + "".join(js_parts) + "})(window);\n"
    return css, js


def merge_names(existing_css: str, names: list[str]) -> list[str]:
    """The components already in ui.css plus the new ones (so adding one does not drop another)."""
    m = re.match(r"/\* Agent Studio components: ([\w, ]+) \(edit freely\) \*/", existing_css or "")
    have = [x.strip() for x in m.group(1).split(",")] if m else []
    out = [k for k in COMPONENTS if k in set(have) | set(names)]
    if "table" in out and "toast" not in out:
        out.append("toast")
    return [k for k in COMPONENTS if k in out]


def wire_html(html: str) -> str:
    """Make sure a page loads ui.css and ui.js (added once: the stylesheet at the end of <head>, the script at the end of <body>)."""
    if "ui.css" not in html:
        tag = '<link rel="stylesheet" href="ui.css">\n'
        i = html.lower().find("</head>")
        html = html[:i] + tag + html[i:] if i >= 0 else tag + html
    if "ui.js" not in html:
        tag = '<script src="ui.js"></script>\n'
        i = html.lower().rfind("</body>")
        html = html[:i] + tag + html[i:] if i >= 0 else html + tag
    return html
