"""The small script Agent Studio adds to every preview page (served as /apps-run/<id>/_studio.js, never written into the app's files).

It does two things for the builder and nothing else:
* it reports what goes wrong while the page runs (script errors, rejected promises, console errors and warnings, files that failed to load, things the
  preview's security rules blocked) to the preview server, so the builder can read them and fix its own mistakes;
* it lets you click an element in the preview ("change this"): the parent page asks for it with postMessage and gets back a description of the element.
It reads nothing from the page except that description, and sends nothing anywhere except to the preview server it came from.
"""

STUDIO_JS = r"""(function () {
  "use strict";
  var cfg = window.AGENT_STUDIO || {};
  var url = String(cfg.base || "").replace(/_agent$/, "_console");
  var sent = 0, seen = {}, MAX = 40;
  function send(kind, text, extra) {
    if (!url) return;
    var key = kind + "|" + text;
    if (kind !== "ready") { if (seen[key] || sent >= MAX) return; seen[key] = 1; sent++; }
    try {
      fetch(url, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-App-Token": cfg.token || "" },
        body: JSON.stringify(Object.assign({ kind: kind, text: String(text).slice(0, 600), page: location.pathname.split("/").slice(3).join("/") || "index.html" }, extra || {})) }).catch(function () {});
    } catch (e) { /* reporting must never break the page */ }
  }
  function short(f) { return String(f || "").split("/").slice(-2).join("/"); }
  window.addEventListener("error", function (e) {
    var t = e.target;
    if (t && t !== window && (t.src || t.href)) send("resource", "failed to load " + short(t.src || t.href));
    else send("error", e.message, { source: short(e.filename), line: e.lineno || 0, col: e.colno || 0 });
  }, true);
  window.addEventListener("unhandledrejection", function (e) { var r = e.reason; send("error", "Unhandled promise rejection: " + ((r && r.message) || r)); });
  window.addEventListener("securitypolicyviolation", function (e) { send("blocked", "blocked by the preview's security rules (" + e.violatedDirective + "): " + (e.blockedURI || "inline code")); });
  ["error", "warn"].forEach(function (level) {
    var orig = console[level];
    console[level] = function () {
      try { send("console." + level, Array.prototype.map.call(arguments, function (a) { return a && a.stack ? a.stack.split("\n")[0] : typeof a === "object" ? JSON.stringify(a) : String(a); }).join(" ")); } catch (e) { /* ignore */ }
      return orig.apply(console, arguments);
    };
  });
  window.addEventListener("load", function () { send("ready", "page loaded"); });

  // ---- "change this": click an element, get a description of it
  var box = null, on = false;
  function selector(el) {
    var parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement && parts.length < 5) {
      if (el.id) { parts.unshift("#" + el.id); break; }
      var s = el.tagName.toLowerCase(), cls = (el.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) s += "." + cls.join(".");
      var sib = el.parentNode ? Array.prototype.filter.call(el.parentNode.children, function (x) { return x.tagName === el.tagName; }) : [];
      if (sib.length > 1) s += ":nth-of-type(" + (sib.indexOf(el) + 1) + ")";
      parts.unshift(s); el = el.parentNode;
    }
    return parts.join(" > ");
  }
  function describe(el) {
    var cs = getComputedStyle(el), r = el.getBoundingClientRect();
    return { selector: selector(el), tag: el.tagName.toLowerCase(), text: (el.innerText || "").trim().slice(0, 160), html: el.outerHTML.slice(0, 500), label: el.getAttribute("aria-label") || "",
      page: location.pathname.split("/").slice(3).join("/") || "index.html", box: { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
      style: { color: cs.color, background: cs.backgroundColor, fontSize: cs.fontSize, fontWeight: cs.fontWeight, display: cs.display } };
  }
  function show(el) {
    if (!box) { box = document.createElement("div"); box.setAttribute("data-studio", "1"); box.style.cssText = "position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #3b82f6;background:rgba(59,130,246,.15);border-radius:3px;transition:all .05s"; document.documentElement.appendChild(box); }
    var r = el.getBoundingClientRect(); box.style.left = r.left + "px"; box.style.top = r.top + "px"; box.style.width = r.width + "px"; box.style.height = r.height + "px";
  }
  function over(e) { if (e.target && e.target !== box) show(e.target); }
  function click(e) {
    e.preventDefault(); e.stopPropagation();
    var d = describe(e.target); stop(); parent.postMessage({ asPicked: d }, "*");
  }
  function key(e) { if (e.key === "Escape") { stop(); parent.postMessage({ asPickCancelled: true }, "*"); } }
  function start() { if (on) return; on = true; document.addEventListener("mouseover", over, true); document.addEventListener("click", click, true); document.addEventListener("keydown", key, true); document.documentElement.style.cursor = "crosshair"; }
  function stop() { on = false; document.removeEventListener("mouseover", over, true); document.removeEventListener("click", click, true); document.removeEventListener("keydown", key, true);
    document.documentElement.style.cursor = ""; if (box) { box.remove(); box = null; } }
  window.addEventListener("message", function (e) { var d = e.data; if (e.source === parent && d && typeof d === "object" && "asPick" in d) { if (d.asPick) start(); else stop(); } });
})();
"""
