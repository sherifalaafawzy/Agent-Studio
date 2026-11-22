"""A small, safe expression language for workflow fields: `{{ $input }}`, `{{ $node["Fetch"].json.items[0].name }}`, `{{ len($prev) > 3 ? "long" : "short" }}`.

Hand-written tokenizer + recursive-descent parser + tree-walking evaluator. There is no `eval`, no attribute access on Python objects (`.name` only
reads a key of a dict), no calls except the whitelisted functions below, and hard limits (expression length, evaluation steps, result size), so a
template written by a person - or an agent - cannot run code, read secrets (the scope never contains credentials) or hang a run.

Variables: `$input` (the task), `$prev` (what the nodes just before produced), `$node["label or id"].text|.json`, `$item` / `$index` (inside For each),
`$run.id`, `$workflow.name`, `$vars`, `$error`, `$now`. The old `{{input}}`, `{{prev}}` and `{{node_id}}` still work (they read the same text).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

MAX_EXPR, MAX_STEPS, MAX_OUT = 600, 4000, 40_000
TOKEN = re.compile(r"""\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(?P<var>\$[A-Za-z_]\w*)|(?P<id>[A-Za-z_][\w-]*)|(?P<op>&&|\|\||==|!=|<=|>=|[-+*/%<>!?:.,()\[\]]))""")
TEMPLATE = re.compile(r"\{\{(.*?)\}\}", re.S)


class ExprError(ValueError):
    """The expression is wrong; the message names it and says where."""


def _tokens(src: str) -> list[tuple[str, str]]:
    if len(src) > MAX_EXPR:
        raise ExprError(f"the expression is longer than {MAX_EXPR} characters")
    out, i = [], 0
    while i < len(src):
        if not src[i:].strip():
            break
        m = TOKEN.match(src, i)
        if not m or m.end() == i:
            raise ExprError(f"unexpected '{src[i:].strip()[:12]}' at position {i}")
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        i = m.end()
    return out


class _P:
    def __init__(self, toks: list[tuple[str, str]]):
        self.t, self.i = toks, 0

    def peek(self) -> tuple[str, str]:
        return self.t[self.i] if self.i < len(self.t) else ("end", "")

    def take(self, val: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok[0] == "end" or (val is not None and tok[1] != val):
            raise ExprError(f"expected '{val}'" if val else "the expression ends too early")
        self.i += 1
        return tok

    def accept(self, *vals: str) -> str | None:
        k, v = self.peek()
        if k == "op" and v in vals:
            self.i += 1
            return v
        return None

    def parse(self):
        node = self.ternary()
        if self.i != len(self.t):
            raise ExprError(f"unexpected '{self.peek()[1]}'")
        return node

    def ternary(self):
        c = self.binary(0)
        if self.accept("?"):
            a = self.ternary()
            self.take(":")
            return ("cond", c, a, self.ternary())
        return c

    LEVELS = [("||",), ("&&",), ("==", "!="), ("<", ">", "<=", ">="), ("+", "-"), ("*", "/", "%")]

    def binary(self, lvl: int):
        if lvl == len(self.LEVELS):
            return self.unary()
        left = self.binary(lvl + 1)
        while (op := self.accept(*self.LEVELS[lvl])):
            left = ("bin", op, left, self.binary(lvl + 1))
        return left

    def unary(self):
        if self.accept("!"):
            return ("not", self.unary())
        if self.accept("-"):
            return ("neg", self.unary())
        return self.postfix()

    def postfix(self):
        n = self.primary()
        while True:
            if self.accept("."):
                k, v = self.take()
                if k not in ("id", "var"):
                    raise ExprError("a name must follow '.'")
                n = ("get", n, ("lit", v.lstrip("$")))
            elif self.accept("["):
                idx = self.ternary()
                self.take("]")
                n = ("get", n, idx)
            else:
                return n

    def primary(self):
        k, v = self.take()
        if k == "num":
            return ("lit", float(v) if "." in v else int(v))
        if k == "str":
            return ("lit", bytes(v[1:-1], "utf-8").decode("unicode_escape") if "\\" in v else v[1:-1])
        if k == "var":
            return ("var", v)
        if k == "id":
            if v in ("true", "false", "null"):
                return ("lit", {"true": True, "false": False, "null": None}[v])
            if self.accept("("):
                args = []
                if not self.accept(")"):
                    args.append(self.ternary())
                    while self.accept(","):
                        args.append(self.ternary())
                    self.take(")")
                return ("call", v, args)
            return ("name", v)
        if v == "(":
            n = self.ternary()
            self.take(")")
            return n
        if v == "[":
            items = []
            if not self.accept("]"):
                items.append(self.ternary())
                while self.accept(","):
                    items.append(self.ternary())
                self.take("]")
            return ("list", items)
        raise ExprError(f"unexpected '{v}'")


def _num(x: Any) -> float:
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        return x
    try:
        return float(str(x).strip())
    except ValueError:
        raise ExprError(f"'{str(x)[:30]}' is not a number")


def _text(x: Any) -> str:
    return x if isinstance(x, str) else "" if x is None else json.dumps(x, default=str, ensure_ascii=False) if isinstance(x, (dict, list)) else str(x)


def _json(x: Any) -> Any:
    if isinstance(x, str):
        try:
            return json.loads(x)
        except ValueError:
            raise ExprError("that text is not valid JSON")
    return x


FUNCS = {
    "len": lambda x: len(x) if isinstance(x, (str, list, dict)) else 0, "lower": lambda x: _text(x).lower(), "upper": lambda x: _text(x).upper(), "trim": lambda x: _text(x).strip(),
    "contains": lambda a, b: (b in a) if isinstance(a, (list, dict)) else _text(b) in _text(a), "startsWith": lambda a, b: _text(a).startswith(_text(b)),
    "endsWith": lambda a, b: _text(a).endswith(_text(b)), "replace": lambda a, b, c: _text(a).replace(_text(b), _text(c)), "split": lambda a, b=",": _text(a).split(_text(b)),
    "join": lambda a, b=",": _text(b).join(_text(x) for x in (a if isinstance(a, list) else [a])), "first": lambda a: (a[0] if a else None) if isinstance(a, list) else None,
    "last": lambda a: (a[-1] if a else None) if isinstance(a, list) else None, "round": lambda a, n=0: round(_num(a), int(_num(n))), "int": lambda a: int(_num(a)), "float": lambda a: _num(a),
    "str": _text, "json": lambda a: json.dumps(a, default=str, ensure_ascii=False), "parse": _json, "default": lambda a, b: b if a in (None, "", [], {}) else a, "keys": lambda a: list(a) if isinstance(a, dict) else [],
    "not_empty": lambda a: a not in (None, "", [], {}), "now": lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "format_date": lambda a, f="%Y-%m-%d": dt.datetime.fromisoformat(_text(a).replace("Z", "+00:00")).strftime(_text(f)),
    "add_days": lambda a, n: (dt.datetime.fromisoformat(_text(a).replace("Z", "+00:00")) + dt.timedelta(days=_num(n))).isoformat(timespec="seconds"),
    "min": lambda *a: min(_num(x) for x in (a[0] if len(a) == 1 and isinstance(a[0], list) else a)), "max": lambda *a: max(_num(x) for x in (a[0] if len(a) == 1 and isinstance(a[0], list) else a)),
    "sum": lambda a: sum(_num(x) for x in a) if isinstance(a, list) else 0, "pick": lambda a, *k: {x: a.get(x) for x in k} if isinstance(a, dict) else {},
}


class _Eval:
    def __init__(self, scope: dict):
        self.scope, self.steps = scope, 0

    def ev(self, n) -> Any:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise ExprError("the expression is too complex")
        op = n[0]
        if op == "lit":
            return n[1]
        if op == "var":
            if n[1] not in self.scope:
                raise ExprError(f"unknown variable {n[1]}")
            return self.scope[n[1]]
        if op == "name":
            if n[1] not in self.scope and self.scope.get("$legacy"):
                return ""                                                     # an old template naming a box that did not run reads as empty, as it always did
            if n[1] not in self.scope:
                raise ExprError(f"unknown name '{n[1]}' (variables start with $, like $input)")
            return self.scope[n[1]]
        if op == "list":
            return [self.ev(x) for x in n[1]]
        if op == "get":
            base, key = self.ev(n[1]), self.ev(n[2])
            if isinstance(base, dict):
                return base.get(key if isinstance(key, str) else _text(key))
            if isinstance(base, list) and isinstance(key, (int, float)) and not isinstance(key, bool):
                return base[int(key)] if -len(base) <= int(key) < len(base) else None
            return None
        if op == "call":
            f = FUNCS.get(n[1])
            if f is None:
                raise ExprError(f"unknown function '{n[1]}'")
            try:
                return f(*[self.ev(a) for a in n[2]])
            except ExprError:
                raise
            except (TypeError, ValueError, IndexError, KeyError, OverflowError) as e:
                raise ExprError(f"{n[1]}(): {str(e)[:80]}")
        if op == "cond":
            return self.ev(n[2]) if self.truthy(self.ev(n[1])) else self.ev(n[3])
        if op == "not":
            return not self.truthy(self.ev(n[1]))
        if op == "neg":
            return -_num(self.ev(n[1]))
        return self.binop(n)

    @staticmethod
    def truthy(x: Any) -> bool:
        return x not in (None, False, "", 0, [], {}) if not isinstance(x, str) else x.strip().lower() not in ("", "false", "0", "no")

    def binop(self, n) -> Any:
        o = n[1]
        if o == "&&":
            a = self.ev(n[2])
            return self.ev(n[3]) if self.truthy(a) else a
        if o == "||":
            a = self.ev(n[2])
            return a if self.truthy(a) else self.ev(n[3])
        a, b = self.ev(n[2]), self.ev(n[3])
        if o in ("==", "!="):
            eq = a == b or (isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, str) and _try_num(b) == a) or (isinstance(b, (int, float)) and not isinstance(b, bool) and isinstance(a, str) and _try_num(a) == b)
            return eq if o == "==" else not eq
        if o == "+" and (isinstance(a, str) or isinstance(b, str)):
            return _text(a) + _text(b)
        if o in ("<", ">", "<=", ">="):
            if isinstance(a, str) and isinstance(b, str) and _try_num(a) is None:
                x, y = a, b
            else:
                x, y = _num(a), _num(b)
            return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[o]       # type: ignore[operator]
        x, y = _num(a), _num(b)
        if o == "+":
            return x + y
        if o == "-":
            return x - y
        if o == "*":
            return x * y
        if y == 0:
            raise ExprError("division by zero")
        return x / y if o == "/" else x % y


def _try_num(s: str):
    try:
        return float(s.strip())
    except ValueError:
        return None


def evaluate(src: str, scope: dict) -> Any:
    """Evaluate one expression (the text between `{{` and `}}`)."""
    if not (src or "").strip():
        raise ExprError("the expression is empty")
    return _Eval(scope).ev(_P(_tokens(src)).parse())


def check(src: str) -> None:
    """Raise ExprError when the expression cannot be parsed (used when saving, so mistakes show before a run)."""
    if (src or "").strip():
        _P(_tokens(src)).parse()


def render(template: str, scope: dict) -> str:
    """Fill every `{{ … }}` in a text. An expression that fails raises ExprError naming it."""
    def sub(m: re.Match) -> str:
        try:
            return _text(evaluate(m.group(1), scope))
        except ExprError as e:
            raise ExprError(f"{{{{{m.group(1).strip()[:60]}}}}}: {e}")
    return TEMPLATE.sub(sub, template or "")[:MAX_OUT]


def render_value(template: str, scope: dict) -> Any:
    """Like `render`, but a field that is exactly one `{{ expr }}` keeps its type (number, list, object)."""
    m = TEMPLATE.fullmatch((template or "").strip())
    if m:
        try:
            return evaluate(m.group(1), scope)
        except ExprError as e:
            raise ExprError(f"{{{{{m.group(1).strip()[:60]}}}}}: {e}")
    return render(template, scope)


def template_errors(template: str) -> list[str]:
    """Syntax problems of every `{{ … }}` in a text (nothing is evaluated)."""
    out = []
    for m in TEMPLATE.finditer(template or ""):
        try:
            check(m.group(1))
        except ExprError as e:
            out.append(f"{{{{{m.group(1).strip()[:40]}}}}}: {e}")
    return out
