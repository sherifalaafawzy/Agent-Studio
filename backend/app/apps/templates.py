"""The standard app templates. Every app starts from one of these, so an app built by any agent has the same layout and runs the same way:

    app.json            the manifest (see manifest.py)
    public/             the static front end (index.html, app.js, style.css, agent-client.js)
    server/             only for node / python apps: a small server with NO third-party packages (Node's `http`, Python's `http.server`)
    Dockerfile          how the app is packaged (a public base image, non-root)
    README.md .gitignore .env.example

A static app calls your agents from the browser through Agent Studio (`AgentClient.ask`); a server app calls them from its own server with the
per-app API key Agent Studio puts in its environment. Neither template contains a secret, a remote script or a CDN link.
"""
from __future__ import annotations

import html
import json

from . import design
from .manifest import default_manifest

TEMPLATES = {
    "static-chat": dict(label="Chat front-end", type="static", description="A single-page chat with one of your agents (local or cloud). No server: runs in the browser through Agent Studio."),
    "static-form": dict(label="Form and result", type="static", description="A form that sends what you type to an agent and shows its answer. No server."),
    "static-dashboard": dict(label="Dashboard", type="static", description="Number cards, a chart and a table from a data file or a data source, with a button that asks your agent for a summary. No server."),
    "static-table": dict(label="Data table", type="static", description="A searchable, sortable table with CSV copy and download, from a data file or a data source, plus an optional agent explanation. No server."),
    "node-api": dict(label="Node server", type="node", description="A small Node server (no packages) that serves a page and asks your agent from the server side. Runs in a container."),
    "python-api": dict(label="Python server", type="python", description="A small Python server (standard library only) that serves a page and asks your agent from the server side. Runs in a container."),
}

AGENT_CLIENT_JS = r"""// Calls your Agent Studio agents from the browser.
// Inside Agent Studio the address and a short-lived token are provided for you (window.AGENT_STUDIO = { base, token }).
// Usage:  const text = await AgentClient.ask("agent-id", "Hello", { onDelta: (piece, all) => show(all) });
(function (root) {
  function fail(message, status) { var e = new Error(message); e.status = status || 0; return e; }
  async function ask(agent, input, opts) {
    opts = opts || {};
    var c = root.AGENT_STUDIO || {};
    var base = String(c.base || "").replace(/\/$/, "");
    if (!base) throw fail("This app is not connected to Agent Studio. Open it from the Apps page in Agent Studio.");
    if (!agent) throw fail("No agent chosen. Set one in app.json (agents) or pass its id to ask().");
    var messages = typeof input === "string" ? [{ role: "user", content: input }] : input;
    var res;
    try {
      res = await fetch(base + "/v1/chat/completions", {
        method: "POST", signal: opts.signal,
        headers: { "Content-Type": "application/json", "X-App-Token": c.token || "" },
        body: JSON.stringify({ model: agent, messages: messages, stream: !!opts.onDelta })
      });
    } catch (e) { throw fail("Could not reach Agent Studio. Is it running?", 0); }
    if (!res.ok) {
      var msg = "Agent Studio refused the request (" + res.status + ").";
      try { var j = await res.json(); msg = (j.error && j.error.message) || msg; } catch (e) { /* keep the default text */ }
      throw fail(msg, res.status);
    }
    if (!opts.onDelta) {
      var body = await res.json();
      return (body.choices && body.choices[0] && body.choices[0].message && body.choices[0].message.content) || "";
    }
    var reader = res.body.getReader(), dec = new TextDecoder(), buf = "", text = "";
    for (;;) {
      var r = await reader.read();
      if (r.done) break;
      buf += dec.decode(r.value, { stream: true });
      var i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        var line = buf.slice(0, i).split("\n").find(function (l) { return l.indexOf("data: ") === 0; });
        buf = buf.slice(i + 2);
        if (!line || line === "data: [DONE]") continue;
        var d = JSON.parse(line.slice(6));
        if (d.error) throw fail(d.error.message || "The agent failed.", 500);
        var delta = d.choices && d.choices[0] && d.choices[0].delta && d.choices[0].delta.content;
        if (delta) { text += delta; opts.onDelta(delta, text); }
      }
    }
    return text;
  }
  root.AgentClient = { ask: ask };
})(window);
"""

STYLE_CSS = """:root { --bg: #fbfaf8; --fg: #1f1d1a; --muted: #6b665e; --card: #ffffff; --line: #e3dfd8; --accent: #c4552d; --accent-fg: #ffffff; }
@media (prefers-color-scheme: dark) { :root { --bg: #1a1816; --fg: #f1ede6; --muted: #a39d92; --card: #24211e; --line: #3a3631; --accent: #e0724a; --accent-fg: #1a1816; } }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.5 var(--font, system-ui, sans-serif); background: var(--bg); color: var(--fg); }
main { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius, 12px); padding: 16px; }
#log { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; min-height: 120px; }
.msg { padding: 10px 12px; border-radius: 10px; white-space: pre-wrap; overflow-wrap: anywhere; }
.me { background: var(--accent); color: var(--accent-fg); align-self: flex-end; max-width: 85%; }
.bot { background: var(--bg); border: 1px solid var(--line); align-self: flex-start; max-width: 92%; }
.err { color: #b3261e; }
form { display: flex; gap: 8px; }
textarea, input[type=text] { flex: 1; font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
textarea { min-height: 96px; width: 100%; resize: vertical; }
button { font: inherit; background: var(--accent); color: var(--accent-fg); border: 0; border-radius: calc(var(--radius, 12px) / 1.5); padding: 10px 16px; cursor: pointer; }
button:disabled { opacity: .55; cursor: default; }
button:focus-visible, textarea:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
#out { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 14px; }
.wide { max-width: 1040px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; margin: 12px 0; }
.grid2 > * { min-width: 0; }
.insight { white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 10px; }
"""

CHAT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}}</title>
<link rel="stylesheet" href="style.css">
</head>
<body data-agent="{{AGENT}}">
<main>
  <h1>{{NAME}}</h1>
  <p class="sub">{{DESC}}</p>
  <div class="card">
    <div id="log" role="log" aria-live="polite"></div>
    <form id="form">
      <input id="text" type="text" placeholder="Type a message" aria-label="Message" autocomplete="off" required>
      <button id="send" type="submit">Send</button>
    </form>
  </div>
</main>
<script src="agent-client.js"></script>
<script src="app.js"></script>
</body>
</html>
"""

CHAT_JS = """"use strict";
// A chat with one of your agents. The agent id comes from <body data-agent="...">.
var agent = document.body.dataset.agent;
var log = document.getElementById("log"), form = document.getElementById("form"), text = document.getElementById("text"), send = document.getElementById("send");
var turns = [];                                            // not "history": that name is the browser's own window.history and cannot be replaced
function add(cls, content) { var d = document.createElement("div"); d.className = "msg " + cls; d.textContent = content; log.appendChild(d); d.scrollIntoView({ block: "end" }); return d; }
if (!agent) add("bot err", "No agent is set for this app yet. Ask the builder to add one, or set data-agent on <body>.");
form.addEventListener("submit", async function (ev) {
  ev.preventDefault();
  var q = text.value.trim();
  if (!q || !agent) return;
  text.value = ""; send.disabled = true;
  add("me", q);
  turns.push({ role: "user", content: q });
  var bubble = add("bot", "…");
  try {
    var answer = await AgentClient.ask(agent, turns, { onDelta: function (piece, all) { bubble.textContent = all; } });
    bubble.textContent = answer || "(no answer)";
    turns.push({ role: "assistant", content: answer });
  } catch (e) { bubble.textContent = e.message; bubble.classList.add("err"); turns.pop(); }
  send.disabled = false; text.focus();
});
"""

FORM_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}}</title>
<link rel="stylesheet" href="style.css">
</head>
<body data-agent="{{AGENT}}">
<main>
  <h1>{{NAME}}</h1>
  <p class="sub">{{DESC}}</p>
  <form id="form" class="card" style="flex-direction:column">
    <label for="input">Your input</label>
    <textarea id="input" required></textarea>
    <div><button id="go" type="submit">Run</button></div>
    <div id="out" role="status" aria-live="polite"></div>
  </form>
</main>
<script src="agent-client.js"></script>
<script src="app.js"></script>
</body>
</html>
"""

FORM_JS = """"use strict";
// Sends the text to one of your agents and shows the answer. The agent id comes from <body data-agent="...">.
var agent = document.body.dataset.agent;
var form = document.getElementById("form"), input = document.getElementById("input"), out = document.getElementById("out"), go = document.getElementById("go");
if (!agent) out.textContent = "No agent is set for this app yet. Ask the builder to add one, or set data-agent on <body>.";
form.addEventListener("submit", async function (ev) {
  ev.preventDefault();
  if (!agent) return;
  go.disabled = true; out.className = ""; out.textContent = "Working…";
  try { out.textContent = (await AgentClient.ask(agent, input.value, { onDelta: function (_p, all) { out.textContent = all; } })) || "(no answer)"; }
  catch (e) { out.textContent = e.message; out.className = "err"; }
  go.disabled = false;
});
"""


DATA_CLIENT_JS = r"""// Reads one of your Agent Studio data sources from the browser (read only, limited rows). Connect the source first (it must be listed in app.json under "data").
// Usage:  const r = await DataClient.query("sales", "SELECT region, SUM(total) AS total FROM orders GROUP BY region");   // r.columns, r.rows
//         const rows = await DataClient.objects("sales", "SELECT * FROM orders LIMIT 20");                                // [{ col: value, ... }]
(function (root) {
  function fail(message, status) { var e = new Error(message); e.status = status || 0; return e; }
  async function query(source, sql, maxRows) {
    var c = root.AGENT_STUDIO || {};
    var base = String(c.base || "").replace(/_agent$/, "_data").replace(/\/$/, "");
    if (!base) throw fail("This app is not connected to Agent Studio. Open it from the Apps page in Agent Studio.");
    if (!source) throw fail("No data source chosen. Connect one in Agent Studio (Apps > Connect) and pass its id.");
    var res;
    try {
      res = await fetch(base + "/" + encodeURIComponent(source), { method: "POST", headers: { "Content-Type": "application/json", "X-App-Token": c.token || "" },
        body: JSON.stringify({ sql: sql, max_rows: maxRows || 200 }) });
    } catch (e) { throw fail("Could not reach Agent Studio. Is it running?", 0); }
    var body = await res.json().catch(function () { return {}; });
    if (!res.ok) throw fail((body.error && body.error.message) || "Agent Studio refused the request (" + res.status + ").", res.status);
    return body;
  }
  async function objects(source, sql, maxRows) {
    var r = await query(source, sql, maxRows);
    return r.rows.map(function (row) { var o = {}; r.columns.forEach(function (c, i) { o[c] = row[i]; }); return o; });
  }
  root.DataClient = { query: query, objects: objects };
})(window);
"""

SAMPLE_DATA = {
    "title": "Sales by month",
    "stats": [{"label": "Revenue", "value": "$48.2k", "delta": "+8%", "up": True}, {"label": "Orders", "value": "1,284", "delta": "+3%", "up": True},
              {"label": "Refunds", "value": "37", "delta": "-12%", "up": True}, {"label": "Churn", "value": "2.1%", "delta": "+0.3%", "up": False}],
    "columns": [{"key": "month", "label": "Month"}, {"key": "orders", "label": "Orders"}, {"key": "revenue", "label": "Revenue ($)"}],
    "rows": [{"month": m, "orders": o, "revenue": r} for m, o, r in (("Jan", 86, 3100), ("Feb", 94, 3500), ("Mar", 120, 4300), ("Apr", 133, 4900), ("May", 150, 5600), ("Jun", 171, 6400),
                                                                     ("Jul", 160, 6000), ("Aug", 188, 7100), ("Sep", 204, 7800), ("Oct", 222, 8400))],
}

DASH_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}}</title>
<link rel="stylesheet" href="style.css">
<link rel="stylesheet" href="ui.css">
</head>
<body data-agent="{{AGENT}}">
<main class="wide">
  <h1>{{NAME}}</h1>
  <p class="sub">{{DESC}}</p>
  <div id="stats" class="ui-stats" aria-live="polite"></div>
  <div class="grid2"><div id="chart"></div><div id="table"></div></div>
  <div class="card">
    <button id="ask" type="button">Summarise with my agent</button>
    <div id="insight" class="insight" role="status" aria-live="polite"></div>
  </div>
</main>
<script src="ui.js"></script>
<script src="agent-client.js"></script>
<script src="data-client.js"></script>
<script src="app.js"></script>
</body>
</html>
"""

DASH_JS = """"use strict";
// A dashboard: number cards, a chart and a table. It reads data.json (sample data: replace it with yours, or read a connected data source with DataClient, see below).
var agent = document.body.dataset.agent;
var data = null;
function el(id) { return document.getElementById(id); }
function stat(s) {
  var d = document.createElement("div"); d.className = "ui-stat";
  [["label", s.label], ["value", s.value]].forEach(function (p) { var x = document.createElement("div"); x.className = p[0]; x.textContent = p[1]; d.appendChild(x); });
  if (s.delta) { var x = document.createElement("div"); x.className = "delta " + (s.up ? "up" : "down"); x.textContent = s.delta; d.appendChild(x); }
  return d;
}
function render(d) {
  data = d;
  el("stats").textContent = "";
  (d.stats || []).forEach(function (s) { el("stats").appendChild(stat(s)); });
  var first = d.columns[1] ? d.columns[1].key : null;
  UI.chart(el("chart"), { type: "bar", title: d.title, labels: d.rows.map(function (r) { return r[d.columns[0].key]; }), values: d.rows.map(function (r) { return Number(r[first]) || 0; }) });
  UI.table(el("table"), { columns: d.columns, rows: d.rows, pageSize: 6, filename: "data" });
}
// To use a data source instead of data.json (connect it in Agent Studio first), load the rows like this and call render with the same shape:
//   DataClient.objects("my-source", "SELECT month, orders, revenue FROM sales").then(function (rows) { render({ title: "Sales", columns: [...], rows: rows }); });
fetch("data.json").then(function (r) { return r.json(); }).then(render).catch(function (e) { el("insight").textContent = "Could not load data.json: " + e.message; });
el("ask").addEventListener("click", async function () {
  if (!agent) { el("insight").textContent = "No agent is set for this app yet. Ask the builder to add one."; return; }
  if (!data) return;
  el("ask").disabled = true; el("insight").textContent = "Working…";
  try { el("insight").textContent = (await AgentClient.ask(agent, "Summarise this data in 3 short bullet points and point out anything unusual:\\n" + JSON.stringify(data.rows.slice(0, 60)), { onDelta: function (_p, all) { el("insight").textContent = all; } })) || "(no answer)"; }
  catch (e) { el("insight").textContent = e.message; }
  el("ask").disabled = false;
});
"""

TABLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}}</title>
<link rel="stylesheet" href="style.css">
<link rel="stylesheet" href="ui.css">
</head>
<body data-agent="{{AGENT}}">
<main class="wide">
  <h1>{{NAME}}</h1>
  <p class="sub">{{DESC}}</p>
  <div id="table"></div>
  <div class="card" style="margin-top:12px">
    <button id="ask" type="button">Explain this data with my agent</button>
    <div id="insight" class="insight" role="status" aria-live="polite"></div>
  </div>
</main>
<script src="ui.js"></script>
<script src="agent-client.js"></script>
<script src="data-client.js"></script>
<script src="app.js"></script>
</body>
</html>
"""

TABLE_JS = """"use strict";
// A data table that reads data.json (sample data: replace it with yours, or read a connected data source with DataClient, see below).
var agent = document.body.dataset.agent;
var data = null;
function el(id) { return document.getElementById(id); }
function render(d) { data = d; UI.table(el("table"), { columns: d.columns, rows: d.rows, pageSize: 10, filename: "table" }); }
// To use a data source instead of data.json (connect it in Agent Studio first):
//   DataClient.objects("my-source", "SELECT * FROM customers LIMIT 200").then(function (rows) { render({ columns: Object.keys(rows[0] || {}).map(function (k) { return { key: k, label: k }; }), rows: rows }); });
fetch("data.json").then(function (r) { return r.json(); }).then(render).catch(function (e) { el("insight").textContent = "Could not load data.json: " + e.message; });
el("ask").addEventListener("click", async function () {
  if (!agent) { el("insight").textContent = "No agent is set for this app yet. Ask the builder to add one."; return; }
  if (!data) return;
  el("ask").disabled = true; el("insight").textContent = "Working…";
  try { el("insight").textContent = (await AgentClient.ask(agent, "Explain what this table shows and point out anything unusual:\\n" + JSON.stringify(data.rows.slice(0, 60)), { onDelta: function (_p, all) { el("insight").textContent = all; } })) || "(no answer)"; }
  catch (e) { el("insight").textContent = e.message; }
  el("ask").disabled = false;
});
"""


SERVER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{NAME}}</title>
<link rel="stylesheet" href="style.css">
</head>
<body data-agent="{{AGENT}}">
<main>
  <h1>{{NAME}}</h1>
  <p class="sub">{{DESC}}</p>
  <form id="form" class="card" style="flex-direction:column">
    <label for="input">Your input</label>
    <textarea id="input" required></textarea>
    <div><button id="go" type="submit">Run</button></div>
    <div id="out" role="status" aria-live="polite"></div>
  </form>
</main>
<script src="app.js"></script>
</body>
</html>
"""

SERVER_JS = """"use strict";
// Sends the text to this app's own server (/api/ask), which asks your agent. The agent id comes from <body data-agent="...">.
var agent = document.body.dataset.agent;
var form = document.getElementById("form"), input = document.getElementById("input"), out = document.getElementById("out"), go = document.getElementById("go");
if (!agent) out.textContent = "No agent is set for this app yet. Ask the builder to add one, or set data-agent on <body>.";
form.addEventListener("submit", async function (ev) {
  ev.preventDefault();
  if (!agent) return;
  go.disabled = true; out.className = ""; out.textContent = "Working…";
  try {
    var res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent: agent, message: input.value }) });
    var body = await res.json();
    if (!res.ok) throw new Error(body.error || "The server could not reach the agent.");
    out.textContent = body.text || "(no answer)";
  } catch (e) { out.textContent = e.message; out.className = "err"; }
  go.disabled = false;
});
"""

NODE_CLIENT = r""""use strict";
// Asks one of your Agent Studio agents. Agent Studio puts AGENT_STUDIO_BASE_URL and AGENT_STUDIO_API_KEY in this app's environment.
async function ask(agent, message) {
  const base = String(process.env.AGENT_STUDIO_BASE_URL || "").replace(/\/$/, "");
  const key = process.env.AGENT_STUDIO_API_KEY || "";
  if (!base || !key) throw new Error("AGENT_STUDIO_BASE_URL and AGENT_STUDIO_API_KEY are not set");
  let res;
  try {
    res = await fetch(base + "/chat/completions", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + key }, body: JSON.stringify({ model: agent, messages: [{ role: "user", content: message }] }) });
  } catch (e) { throw new Error("Could not reach Agent Studio"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body.error && body.error.message) || "Agent Studio refused the request (" + res.status + ")");
  return (body.choices && body.choices[0] && body.choices[0].message && body.choices[0].message.content) || "";
}
module.exports = { ask };
"""

NODE_SERVER = r""""use strict";
const http = require("http"), fs = require("fs"), path = require("path");
const { ask } = require("./agent-client.js");
const PORT = Number(process.env.PORT || 8080);
const PUBLIC = path.resolve(__dirname, "..", "public");
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon" };
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'";
function send(res, code, body, type) {
  res.writeHead(code, { "Content-Type": type || "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": CSP });
  res.end(body);
}
http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (req.method === "POST" && url.pathname === "/api/ask") {
    let raw = "";
    req.on("data", (c) => { raw += c; if (raw.length > 100000) req.destroy(); });
    req.on("end", async () => {
      try {
        const b = JSON.parse(raw || "{}");
        send(res, 200, JSON.stringify({ text: await ask(String(b.agent || ""), String(b.message || "")) }), "application/json");
      } catch (e) { send(res, 502, JSON.stringify({ error: String((e && e.message) || e) }), "application/json"); }
    });
    return;
  }
  let rel;
  try { rel = url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname); } catch (e) { return send(res, 400, "Bad request"); }
  const file = path.resolve(PUBLIC, "." + rel);
  if (!file.startsWith(PUBLIC + path.sep)) return send(res, 403, "Forbidden");
  fs.readFile(file, (err, data) => (err ? send(res, 404, "Not found") : send(res, 200, data, TYPES[path.extname(file)] || "application/octet-stream")));
}).listen(PORT, "0.0.0.0", () => console.log("Listening on " + PORT));
"""

PY_CLIENT = '''"""Asks one of your Agent Studio agents. Agent Studio puts AGENT_STUDIO_BASE_URL and AGENT_STUDIO_API_KEY in this app's environment."""
import json
import os
import urllib.error
import urllib.request


def ask(agent, message, timeout=120):
    base = os.environ.get("AGENT_STUDIO_BASE_URL", "").rstrip("/")
    key = os.environ.get("AGENT_STUDIO_API_KEY", "")
    if not base or not key:
        raise RuntimeError("AGENT_STUDIO_BASE_URL and AGENT_STUDIO_API_KEY are not set")
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps({"model": agent, "messages": [{"role": "user", "content": message}]}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e)["error"]["message"]
        except Exception:
            msg = "Agent Studio refused the request (%s)" % e.code
        raise RuntimeError(msg)
    except urllib.error.URLError as e:
        raise RuntimeError("Could not reach Agent Studio: %s" % e.reason)
    return ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
'''

PY_SERVER = '''import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from agent_client import ask

PUBLIC = (Path(__file__).resolve().parent.parent / "public").resolve()
PORT = int(os.environ.get("PORT", "8080"))
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlparse(self.path).path != "/api/ask":
            return self._send(404, "Not found")
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 100000)
            b = json.loads(self.rfile.read(n) or b"{}")
            text = ask(str(b.get("agent") or ""), str(b.get("message") or ""))
            self._send(200, json.dumps({"text": text}), "application/json")
        except Exception as e:  # noqa: BLE001 - reported to the page as text
            self._send(502, json.dumps({"error": str(e)}), "application/json")

    def do_GET(self):
        rel = unquote(urlparse(self.path).path)
        rel = "index.html" if rel == "/" else rel.lstrip("/")
        f = (PUBLIC / rel).resolve()
        if PUBLIC not in f.parents:
            return self._send(403, "Forbidden")
        if not f.is_file():
            return self._send(404, "Not found")
        self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
'''

DOCKERFILES = {
    "static": "FROM nginx:1.27-alpine\nCOPY public/ /usr/share/nginx/html/\nEXPOSE 80\n",
    "node": "FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nENV PORT=8080\nEXPOSE 8080\nUSER node\nCMD [\"node\", \"server/index.js\"]\n",
    "python": "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nENV PORT=8080 PYTHONUNBUFFERED=1\nEXPOSE 8080\nUSER 1000\nCMD [\"python\", \"server/main.py\"]\n",
}
GITIGNORE = ".env\n.env.*\n!.env.example\nnode_modules/\n__pycache__/\n*.log\n.DS_Store\n"

README = """# {name}

{description}

Built in Agent Studio from the "{label}" template.

## Layout
- `app.json`: the manifest (name, which agents it calls, what it may reach, how it starts).
- `public/`: the page people see.
{server_line}- `Dockerfile`: how to package it.
- `.env.example`: the environment variables it needs (names only: put the values in Agent Studio or your own environment, never in the files).

## How it talks to your agents
{how}

## Run it
{run}

## Rules this app follows
- No secrets in any file. No scripts or styles loaded from other websites.
- It calls only the agents listed in `app.json`.
"""


def _how(t: str) -> str:
    if t == "static":
        return ("The page calls `AgentClient.ask(agentId, text)` (`public/agent-client.js`). Inside Agent Studio the address and a short-lived token are supplied. "
                "To host the page somewhere else you need your own small server that holds an Agent Studio API key and forwards the request: never put a key in the page.")
    return ("`server/` asks the agent with `AGENT_STUDIO_BASE_URL` and `AGENT_STUDIO_API_KEY` from its environment (Agent Studio fills them in when it runs the app). "
            "That key is limited to the agents listed in `app.json`.")


def _run(t: str) -> str:
    if t == "static":
        return "Open it from the Apps page in Agent Studio, or serve the `public/` folder with any static web server. To package it: `docker build -t my-app .` then `docker run -p 8080:80 my-app`."
    cmd = "node server/index.js" if t == "node" else "python server/main.py"
    return f"In Agent Studio press Run (it starts in a container). By hand: set the two variables above and run `{cmd}`. To package it: `docker build -t my-app .` then `docker run -p 8080:8080 my-app`."


def env_example(manifest: dict) -> str:
    lines = ["# Names only. Put the real values in Agent Studio (Apps > Settings) or in your own environment.", "# AGENT_STUDIO_BASE_URL=http://127.0.0.1:8765/v1", "# AGENT_STUDIO_API_KEY="]
    for e in manifest.get("env", []):
        lines += ([f"# {e['description']}"] if e.get("description") else []) + [f"{e['name']}="]
    return "\n".join(lines) + "\n"


def render(template: str, name: str, description: str = "", agent_id: str = "") -> dict[str, str]:
    """The files of a new app, path -> text."""
    if template not in TEMPLATES:
        raise KeyError(template)
    t = TEMPLATES[template]["type"]
    m = default_manifest(name, t, description)
    if agent_id:
        m["agents"] = [{"id": agent_id, "purpose": "The agent this app talks to"}]
    sub = {"{{NAME}}": html.escape(m["name"]), "{{DESC}}": html.escape(m["description"] or "Built with Agent Studio."), "{{AGENT}}": html.escape(agent_id, quote=True)}

    def fill(s: str) -> str:
        for k, v in sub.items():
            s = s.replace(k, v)
        return s
    files: dict[str, str] = {"app.json": json.dumps(m, indent=2) + "\n", "public/style.css": design.apply_theme(STYLE_CSS)}
    if template == "static-chat":
        files.update({"public/index.html": fill(CHAT_HTML), "public/app.js": CHAT_JS, "public/agent-client.js": AGENT_CLIENT_JS})
    elif template == "static-form":
        files.update({"public/index.html": fill(FORM_HTML), "public/app.js": FORM_JS, "public/agent-client.js": AGENT_CLIENT_JS})
    elif template in ("static-dashboard", "static-table"):
        dash = template == "static-dashboard"
        ui_css, ui_js = design.bundle(design.merge_names("", ["stats", "chart", "table"] if dash else ["table"]))
        data = dict(SAMPLE_DATA) if dash else {k: v for k, v in SAMPLE_DATA.items() if k != "stats"}
        files.update({"public/index.html": fill(DASH_HTML if dash else TABLE_HTML), "public/app.js": DASH_JS if dash else TABLE_JS, "public/agent-client.js": AGENT_CLIENT_JS,
                      "public/data-client.js": DATA_CLIENT_JS, "public/ui.css": ui_css, "public/ui.js": ui_js, "public/data.json": json.dumps(data, indent=2) + "\n"})
    else:
        files.update({"public/index.html": fill(SERVER_HTML), "public/app.js": SERVER_JS})
        if t == "node":
            files.update({"server/index.js": NODE_SERVER, "server/agent-client.js": NODE_CLIENT})
        else:
            files.update({"server/main.py": PY_SERVER, "server/agent_client.py": PY_CLIENT})
    files.update({
        "Dockerfile": DOCKERFILES[t], ".gitignore": GITIGNORE, ".env.example": env_example(m),
        "README.md": README.format(name=m["name"], description=m["description"] or "An app built with Agent Studio.", label=TEMPLATES[template]["label"],
                                   server_line="- `server/`: the small server (no third-party packages).\n" if t != "static" else "", how=_how(t), run=_run(t)),
    })
    return files
