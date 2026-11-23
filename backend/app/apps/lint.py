"""A quick look over an app's files, without running anything: broken references, code the preview would block, unbalanced brackets.

This is what the builder reads (with the errors the preview itself reported) to fix its own mistakes. It never executes the app's code. The bracket check is a
rough scan, so it only ever *warns*: a real syntax error shows up for certain as an error from the running page.
"""
from __future__ import annotations

import posixpath
import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)
URL_IN_CODE = re.compile(r"""(?:fetch\(|\.open\(\s*["'][A-Z]+["']\s*,|new\s+WebSocket\(|new\s+EventSource\()\s*["'`](https?://[^"'`\s]+)""")
KEYWORD_BEFORE = re.compile(r"(?<![\w$])(?:return|typeof|case|in|of)\s*$")
PAIRS = {")": "(", "]": "[", "}": "{"}
REGEX_AFTER = set("(,=:[!&|?{;+-*%<>~^") | {""}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str]] = []                   # (kind, url)
        self.ids: list[str] = []
        self.inline_script = 0
        self.handlers: list[str] = []
        self.stack: list[str] = []
        self.stray: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            if a.get("src"):
                self.refs.append(("script", a["src"]))
            else:
                self._in_script = True
                if a.get("type", "").lower() not in ("application/json", "application/ld+json", "text/template", "importmap"):
                    self.inline_script += 1
        elif tag == "link" and a.get("href") and a.get("rel", "").lower() in ("stylesheet", "icon", "shortcut icon", "manifest", "modulepreload"):
            self.refs.append(("link", a["href"]))
        elif tag in ("img", "source", "audio", "video") and a.get("src"):
            self.refs.append((tag, a["src"]))
        elif tag == "a" and a.get("href") and not a["href"].startswith("#"):
            self.refs.append(("link to", a["href"]))
        if a.get("id"):
            self.ids.append(a["id"])
        for k in a:
            if k.startswith("on") and len(k) > 2:
                self.handlers.append(f"<{tag} {k}=…>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag in VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stray.append(self.stack.pop())
            self.stack.pop()
        else:
            self.stray.append(f"/{tag}")


def local_target(page: str, url: str) -> str | None:
    """The app file a page's reference points at, or None for anything that is not a local file (another website, #anchor, data:, mailto:)."""
    u = url.strip().split("#", 1)[0].split("?", 1)[0]
    if not u or REMOTE.match(u):
        return None
    base = posixpath.dirname(page)
    p = posixpath.normpath(posixpath.join(base, u)) if not u.startswith("/") else posixpath.normpath("public" + u)
    return p


def bracket_problem(text: str) -> str | None:
    """A rough check that (), [] and {} are balanced in JavaScript, skipping strings, comments, template literals and regular expressions. None = fine."""
    stack: list[tuple[str, int]] = []
    i, n, line, prev, in_tpl = 0, len(text), 1, "", False
    while i < n:
        c = text[i]
        if in_tpl:                                                # inside `...`: only a backslash, the closing backtick and ${ matter
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                line += 1
            if c == "`":
                in_tpl, prev = False, "`"
            elif c == "$" and text[i + 1:i + 2] == "{":
                stack.append(("${", line))
                in_tpl, prev = False, "{"
                i += 2
                continue
            i += 1
            continue
        if c == "\n":
            line += 1
        elif c == "/" and text[i + 1:i + 2] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and text[i + 1:i + 2] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
            continue
        elif c in "'\"":
            i += 1
            while i < n and text[i] != c and text[i] != "\n":
                i += 2 if text[i] == "\\" else 1
            prev = c
        elif c == "`":
            in_tpl = True
        elif c == "/" and (prev in REGEX_AFTER or KEYWORD_BEFORE.search(text[max(0, i - 12):i])):
            i, in_cls = i + 1, False
            while i < n and text[i] != "\n" and (in_cls or text[i] != "/"):
                if text[i] == "\\":
                    i += 1
                elif text[i] == "[":
                    in_cls = True
                elif text[i] == "]":
                    in_cls = False
                i += 1
            prev = "/"
        elif c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if c == "}" and stack and stack[-1][0] == "${":      # the end of a ${ } inside a template: back into the string
                stack.pop()
                in_tpl = True
                i += 1
                continue
            if not stack or stack[-1][0] != PAIRS[c]:
                return f"an unexpected '{c}' on line {line}"
            stack.pop()
        if not c.isspace() and c not in "'\"`" and not (c == "/" and prev == "/"):
            prev = c
        i += 1
    if stack:
        o, ln = stack[-1]
        return f"a '{o}' opened on line {ln} is never closed"
    return None


def css_problem(text: str) -> str | None:
    t = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    t = re.sub(r"""(["'])(?:\\.|(?!\1).)*\1""", "", t)
    d = t.count("{") - t.count("}")
    return None if d == 0 else (f"{d} '{{' never closed" if d > 0 else f"{-d} extra '}}'")


COMMENTS = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|/\*.*?\*/|//[^\n]*""", re.S)


def strip_comments(text: str) -> str:
    """The JavaScript with its comments removed (strings are left alone, so a // inside a URL is not taken for a comment)."""
    return COMMENTS.sub(lambda m: m.group(1) or "", text)


def lint(files: dict[str, bytes], manifest: dict | None = None) -> list[dict]:
    """Problems found by reading the files: [{level: error|warn, path, text}]."""
    out: list[dict] = []

    def add(level: str, path: str, text: str) -> None:
        out.append(dict(level=level, path=path, text=text))
    manifest = manifest or {}
    net = (manifest.get("permissions") or {}).get("network", "none")
    allow = (manifest.get("permissions") or {}).get("allow", [])
    text_of = {p: d.decode("utf-8", "replace") for p, d in files.items() if p.startswith("public/") and p.endswith((".html", ".js", ".css", ".mjs"))}
    for path, text in sorted(text_of.items()):
        if path.endswith(".html"):
            pg = _Page()
            try:
                pg.feed(text)
                pg.close()
            except Exception as e:                                # noqa: BLE001 - the parser is forgiving; anything it still rejects is reported, not raised
                add("warn", path, f"could not read the page structure: {e}")
                continue
            for kind, url in pg.refs:
                t = local_target(path, url)
                if t is not None and t not in files and t + "/index.html" not in files:
                    add("error", path, f"{kind} '{url}' points at a file that does not exist ({t})")
            if pg.inline_script:
                add("error", path, "it has an inline <script>: the preview blocks those. Move the code into a file under public/ and load it with <script src>")
            if pg.handlers:
                add("error", path, f"inline event handlers ({pg.handlers[0]}) are blocked by the preview: attach events in a script file with addEventListener")
            dup = sorted({i for i in pg.ids if pg.ids.count(i) > 1})
            if dup:
                add("warn", path, f"the id '{dup[0]}' is used more than once")
            if pg.stray or pg.stack:
                bad = (pg.stray or pg.stack)[0]
                add("warn", path, f"the tags look unbalanced (check <{bad.lstrip('/')}>)")
        elif path.endswith((".js", ".mjs")):
            p = bracket_problem(text)
            if p:
                add("warn", path, f"{p}: this may be a syntax error")
            for m in URL_IN_CODE.finditer(text):
                origin = re.match(r"https?://[^/]+", m.group(1)).group(0)
                if net == "none" or (net == "allowlist" and origin not in allow):
                    add("warn", path, f"it calls {origin}, which the preview blocks. Allow it in app.json permissions (network) or remove the call")
            if path.rsplit("/", 1)[-1] in ("agent-client.js", "data-client.js", "ui.js"):          # the helper files themselves
                continue
            code = strip_comments(text)                                                       # a mention in a comment does not count
            if "AgentClient" in code and not manifest.get("agents"):
                add("warn", path, "it calls an agent but app.json lists no agents: connect one first")
            if "DataClient" in code and not manifest.get("data"):
                add("warn", path, "it reads a data source but app.json lists none: connect one first")
        else:
            p = css_problem(text)
            if p:
                add("warn", path, f"the stylesheet looks unbalanced: {p}")
    if "public/index.html" in files and not text_of.get("public/index.html", "").strip():
        add("error", "public/index.html", "the page is empty")
    return out
