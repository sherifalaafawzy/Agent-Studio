"""Computation for the lab: a safe calculator, units and constants, molar masses, simple sequence statistics, numeric calculus, and (through the sandbox) your own Python.

Everything here runs on this PC. The calculator and the unit/constant tools are pure Python with no network and no `eval`. Code you or an agent write runs ONLY in the
sandbox (a hardened container with no network by default); it is screened first and every run leaves a reproducibility record (code hash, inputs, seed, output hash).
Numbers are floats: results are as precise as double precision and are labelled "computed here", never presented as measurements.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import math
import re
import time

from . import safety

# ---------------------------------------------------------------- constants (CODATA 2022 values from NIST; a vendored table, no network)
CONSTANTS_SOURCE = "CODATA 2022 recommended values (NIST). Exact where the SI fixes them; check the NIST page before publication use."
CONSTANTS: dict[str, dict] = {
    "c": dict(name="speed of light in vacuum", value=299792458.0, unit="m/s", exact=True),
    "h": dict(name="Planck constant", value=6.62607015e-34, unit="J s", exact=True),
    "hbar": dict(name="reduced Planck constant", value=1.054571817646156e-34, unit="J s", exact=True),
    "e": dict(name="elementary charge", value=1.602176634e-19, unit="C", exact=True),
    "k_B": dict(name="Boltzmann constant", value=1.380649e-23, unit="J/K", exact=True),
    "N_A": dict(name="Avogadro constant", value=6.02214076e23, unit="1/mol", exact=True),
    "R": dict(name="molar gas constant", value=8.31446261815324, unit="J/(mol K)", exact=True),
    "sigma": dict(name="Stefan-Boltzmann constant", value=5.670374419184429e-8, unit="W/(m^2 K^4)", exact=True),
    "g_n": dict(name="standard gravity", value=9.80665, unit="m/s^2", exact=True),
    "atm": dict(name="standard atmosphere", value=101325.0, unit="Pa", exact=True),
    "G": dict(name="Newtonian constant of gravitation", value=6.67430e-11, unit="m^3/(kg s^2)", exact=False, uncertainty=1.5e-15),
    "m_e": dict(name="electron mass", value=9.1093837139e-31, unit="kg", exact=False, uncertainty=2.8e-40),
    "m_p": dict(name="proton mass", value=1.67262192595e-27, unit="kg", exact=False, uncertainty=5.2e-37),
    "u": dict(name="atomic mass constant", value=1.66053906892e-27, unit="kg", exact=False, uncertainty=5.2e-37),
    "eps0": dict(name="vacuum electric permittivity", value=8.8541878188e-12, unit="F/m", exact=False, uncertainty=1.4e-21),
    "mu0": dict(name="vacuum magnetic permeability", value=1.25663706127e-6, unit="N/A^2", exact=False, uncertainty=2.0e-16),
    "alpha": dict(name="fine-structure constant", value=7.2973525643e-3, unit="", exact=False, uncertainty=1.1e-12),
}


def constant(symbol: str) -> dict:
    s = CONSTANTS.get(symbol) or next((v for k, v in CONSTANTS.items() if k.lower() == symbol.lower()), None)
    if s is None:
        raise ValueError(f"unknown constant '{symbol}'; known: {', '.join(CONSTANTS)}")
    return {"symbol": symbol if symbol in CONSTANTS else next(k for k, v in CONSTANTS.items() if v is s), **s, "source": CONSTANTS_SOURCE}


# ---------------------------------------------------------------- units
DIMS = ("m", "kg", "s", "A", "K", "mol", "cd")
Q = tuple[float, tuple[int, ...]]                      # (factor to SI, exponents of DIMS)


def _d(**k) -> tuple[int, ...]:
    return tuple(k.get(n, 0) for n in DIMS)


def _mul(a: Q, b: Q) -> Q:
    return (a[0] * b[0], tuple(x + y for x, y in zip(a[1], b[1])))


def _pow(a: Q, n: int) -> Q:
    return (a[0] ** n, tuple(x * n for x in a[1]))


_N, _J, _W = (1.0, _d(kg=1, m=1, s=-2)), (1.0, _d(kg=1, m=2, s=-2)), (1.0, _d(kg=1, m=2, s=-3))
UNITS: dict[str, Q] = {
    "m": (1.0, _d(m=1)), "kg": (1.0, _d(kg=1)), "g": (1e-3, _d(kg=1)), "s": (1.0, _d(s=1)), "A": (1.0, _d(A=1)), "K": (1.0, _d(K=1)), "mol": (1.0, _d(mol=1)), "cd": (1.0, _d(cd=1)),
    "N": _N, "J": _J, "W": _W, "Pa": (1.0, _d(kg=1, m=-1, s=-2)), "Hz": (1.0, _d(s=-1)), "C": (1.0, _d(A=1, s=1)), "V": (1.0, _d(kg=1, m=2, s=-3, A=-1)),
    "F": (1.0, _d(kg=-1, m=-2, s=4, A=2)), "ohm": (1.0, _d(kg=1, m=2, s=-3, A=-2)), "T": (1.0, _d(kg=1, s=-2, A=-1)), "L": (1e-3, _d(m=3)),
    "eV": (1.602176634e-19, _d(kg=1, m=2, s=-2)), "cal": (4.184, _d(kg=1, m=2, s=-2)), "atm": (101325.0, _d(kg=1, m=-1, s=-2)), "bar": (1e5, _d(kg=1, m=-1, s=-2)),
    "mmHg": (133.322387415, _d(kg=1, m=-1, s=-2)), "min": (60.0, _d(s=1)), "h": (3600.0, _d(s=1)), "day": (86400.0, _d(s=1)), "Da": (1.66053906892e-27, _d(kg=1)), "u": (1.66053906892e-27, _d(kg=1)),
    "angstrom": (1e-10, _d(m=1)), "in": (0.0254, _d(m=1)), "ft": (0.3048, _d(m=1)), "mi": (1609.344, _d(m=1)), "lb": (0.45359237, _d(kg=1)), "au": (149597870700.0, _d(m=1)), "ly": (9460730472580800.0, _d(m=1)),
}
PREFIX = {"y": 1e-24, "z": 1e-21, "a": 1e-18, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "c": 1e-2, "d": 1e-1, "da": 1e1, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
NO_PREFIX = {"min", "h", "day", "in", "ft", "mi", "lb", "au", "ly", "mmHg", "cal", "atm", "bar", "u", "Da", "angstrom", "cd"}
TEMPS = ("degC", "degF", "K")


def unit(name: str) -> Q:
    if name in UNITS:
        return UNITS[name]
    for p in sorted(PREFIX, key=len, reverse=True):
        rest = name[len(p):]
        if name.startswith(p) and rest in UNITS and rest not in NO_PREFIX:
            f, d = UNITS[rest]
            return (f * PREFIX[p], d)
    raise ValueError(f"unknown unit '{name}'")


_TOK = re.compile(r"\s*(?:([A-Za-zµ]+)|(-?\d+(?:\.\d+)?)|(.))")


def parse_units(text: str) -> Q:
    """'kg*m^2/s^2', 'm/s', 'J/(mol*K)', '1/s' -> (factor to SI, dimension exponents)."""
    text = text.strip()
    toks, pos = [], 0
    while pos < len(text):
        m = _TOK.match(text, pos)                       # always matches: the text is stripped, so a character follows any spaces
        pos = m.end()
        toks.append(m.group(1) or m.group(2) or m.group(3))
    if len(text) > 80 or not toks:
        raise ValueError("write a unit like m/s, kg*m^2/s^2 or J/(mol*K)")
    i = 0

    def peek():
        return toks[i] if i < len(toks) else None

    def atom() -> Q:
        nonlocal i
        t = peek()
        if t is None:
            raise ValueError("the unit ends too early")
        i += 1
        if t == "(":
            q = expr()
            if peek() != ")":
                raise ValueError("a bracket is not closed in the unit")
            i += 1
        elif re.fullmatch(r"-?\d+(\.\d+)?", t):
            q = (float(t), _d())
        elif t.isalpha() or t == "µ" or t[0].isalpha():
            q = unit(t)
        else:
            raise ValueError(f"unexpected '{t}' in the unit")
        if peek() == "^":
            i += 1
            e = peek()
            if e is None or not re.fullmatch(r"-?\d+", e):
                raise ValueError("a power in a unit must be a whole number")
            i += 1
            q = _pow(q, int(e))
        return q

    def expr() -> Q:
        nonlocal i
        q = atom()
        while peek() in ("*", "/"):
            op = toks[i]
            i += 1
            r = atom()
            q = _mul(q, r) if op == "*" else _mul(q, _pow(r, -1))
        return q

    q = expr()
    if i != len(toks):
        raise ValueError(f"unexpected '{toks[i]}' in the unit")
    return q


def _to_k(v: float, u: str) -> float:
    return v + 273.15 if u == "degC" else (v - 32) * 5 / 9 + 273.15 if u == "degF" else v


def _from_k(v: float, u: str) -> float:
    return v - 273.15 if u == "degC" else (v - 273.15) * 9 / 5 + 32 if u == "degF" else v


def convert(value: float, src: str, dst: str) -> dict:
    """Convert between units of the same kind (dimension-checked). Temperatures (degC, degF, K) are handled as scales."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("the value must be a finite number")
    src, dst = src.strip(), dst.strip()
    if src in TEMPS and dst in TEMPS:
        out = _from_k(_to_k(float(value), src), dst)
        return {"value": value, "from": src, "to": dst, "result": out, "note": "temperature scales"}
    a, b = parse_units(src), parse_units(dst)
    if a[1] != b[1]:
        raise ValueError(f"cannot convert {src} to {dst}: they measure different things")
    return {"value": value, "from": src, "to": dst, "result": value * a[0] / b[0], "factor": a[0] / b[0]}


# ---------------------------------------------------------------- a safe calculator
FUNCS = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
         "exp": math.exp, "log": math.log, "log10": math.log10, "log2": math.log2, "sqrt": math.sqrt, "abs": abs, "min": min, "max": max, "floor": math.floor, "ceil": math.ceil, "round": round,
         "factorial": math.factorial, "gcd": math.gcd, "comb": math.comb, "perm": math.perm, "hypot": math.hypot, "radians": math.radians, "degrees": math.degrees, "pow": pow}
NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}
MAX_EXPR, MAX_NODES, MAX_POW = 1000, 300, 10_000
BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}


class CalcError(ValueError):
    """The expression cannot be evaluated; the message says why."""


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CalcError("only numbers are allowed")
    return v


def calc(expression: str, variables: dict | None = None) -> float | int:
    """Evaluate a maths expression: + - * / // % **, brackets, the functions in FUNCS, pi, e, tau and your variables. No attribute access, no calls to anything else."""
    if not isinstance(expression, str) or not expression.strip() or len(expression) > MAX_EXPR:
        raise CalcError(f"give an expression of 1 to {MAX_EXPR} characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise CalcError(f"that is not a valid expression ({e.msg})") from e
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalcError("the expression is too long")
    env = {**NAMES, **{k: float(v) for k, v in (variables or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return _num(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise CalcError(f"unknown name '{n.id}'")
            return env[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = _num(ev(n.operand))
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            a, b = _num(ev(n.left)), _num(ev(n.right))
            if isinstance(n.op, ast.Pow):
                if abs(b) > MAX_POW or (isinstance(a, int) and isinstance(b, int) and abs(a) > 1 and b * math.log10(abs(a)) > 300):
                    raise CalcError("that power is too large")
                return a ** b
            f = BIN.get(type(n.op))
            if f is None:
                raise CalcError("that operator is not allowed")
            return f(a, b)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS and not n.keywords:
            args = [_num(ev(a)) for a in n.args]
            if n.func.id == "factorial" and (not args or args[0] > 1000):
                raise CalcError("factorial is limited to 1000")
            return FUNCS[n.func.id](*args)
        raise CalcError("only numbers, names, + - * / ** and the listed functions are allowed")
    try:
        out = ev(tree)
    except CalcError:
        raise
    except ZeroDivisionError as e:
        raise CalcError("division by zero") from e
    except (ValueError, TypeError, OverflowError) as e:
        raise CalcError(f"cannot compute that ({e})") from e
    if isinstance(out, float) and not math.isfinite(out):
        raise CalcError("the result is not a finite number")
    return out


def numeric(op: str, expression: str, var: str = "x", a: float = 0.0, b: float = 1.0, variables: dict | None = None, n: int = 2000) -> dict:
    """Numeric calculus on one variable: integrate (Simpson), derivative (at a), root (bisection on [a, b]), table (n points of the expression over [a, b])."""
    if not re.fullmatch(r"[A-Za-z_]\w{0,20}", var or ""):
        raise CalcError("the variable name must be a short word")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (a, b)):
        raise CalcError("a and b must be finite numbers")

    def f(x: float) -> float:
        return float(calc(expression, {**(variables or {}), var: x}))
    if op == "integrate":
        k = max(2, min(int(n), 20000))
        k += k % 2
        h = (b - a) / k
        s = f(a) + f(b) + sum((4 if i % 2 else 2) * f(a + i * h) for i in range(1, k))
        return {"op": op, "result": s * h / 3, "method": f"composite Simpson, {k} intervals", "interval": [a, b]}
    if op == "derivative":
        h = 1e-5 * max(1.0, abs(a))
        return {"op": op, "result": (f(a + h) - f(a - h)) / (2 * h), "method": "central difference", "at": a, "step": h}
    if op == "root":
        fa, fb = f(a), f(b)
        if fa == 0:
            return {"op": op, "result": a, "method": "bisection", "iterations": 0}
        if fb == 0:
            return {"op": op, "result": b, "method": "bisection", "iterations": 0}
        if fa * fb > 0:
            raise CalcError("the function has the same sign at both ends, so there may be no root between them")
        lo, hi, it = a, b, 0
        while it < 200 and abs(hi - lo) > 1e-13 * max(1.0, abs(lo)):
            mid = (lo + hi) / 2
            fm = f(mid)
            if fm == 0:
                lo = hi = mid
                break
            if fa * fm < 0:
                hi = mid
            else:
                lo, fa = mid, fm
            it += 1
        return {"op": op, "result": (lo + hi) / 2, "method": "bisection", "iterations": it}
    if op == "table":
        k = max(2, min(int(n), 500))
        xs = [a + (b - a) * i / (k - 1) for i in range(k)]
        return {"op": op, "x": xs, "y": [f(x) for x in xs], "method": f"{k} evenly spaced points"}
    raise CalcError("op must be integrate, derivative, root or table")


# ---------------------------------------------------------------- chemistry: molar mass from a formula
ATOMIC_WEIGHTS = {"H": 1.008, "He": 4.0026, "Li": 6.94, "Be": 9.0122, "B": 10.81, "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180, "Na": 22.990, "Mg": 24.305, "Al": 26.982,
                  "Si": 28.085, "P": 30.974, "S": 32.06, "Cl": 35.45, "Ar": 39.95, "K": 39.098, "Ca": 40.078, "Ti": 47.867, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845, "Co": 58.933, "Ni": 58.693,
                  "Cu": 63.546, "Zn": 65.38, "Se": 78.971, "Br": 79.904, "Ag": 107.87, "Sn": 118.71, "I": 126.90, "Ba": 137.33, "Pt": 195.08, "Au": 196.97, "Hg": 200.59, "Pb": 207.2}
WEIGHTS_SOURCE = "Abridged IUPAC standard atomic weights (conventional values, 5 significant figures)"
_FTOK = re.compile(r"([A-Z][a-z]?)(\d*)|(\()|(\))(\d*)")


def formula_mass(formula: str) -> dict:
    """Molar mass and element percentages of a formula such as C6H12O6, Ca(OH)2 or CuSO4.5H2O."""
    f = (formula or "").replace("·", ".").replace(" ", "")
    if not f or len(f) > 200 or not re.fullmatch(r"[A-Za-z0-9().]+", f):
        raise ValueError("write a formula like C6H12O6, Ca(OH)2 or CuSO4.5H2O")
    total: dict[str, int] = {}
    for part in f.split("."):
        m = re.match(r"(\d+)(.*)", part)
        coef, body = (int(m.group(1)), m.group(2)) if m else (1, part)
        stack: list[dict[str, int]] = [{}]
        pos = 0
        while pos < len(body):
            t = _FTOK.match(body, pos)
            if not t or t.end() == pos:
                raise ValueError(f"cannot read the formula near '{body[pos:pos + 6]}'")
            pos = t.end()
            if t.group(1):
                if t.group(1) not in ATOMIC_WEIGHTS:
                    raise ValueError(f"element '{t.group(1)}' is not in the table (it covers {len(ATOMIC_WEIGHTS)} common elements)")
                stack[-1][t.group(1)] = stack[-1].get(t.group(1), 0) + int(t.group(2) or 1)
            elif t.group(3):
                stack.append({})
            else:
                if len(stack) < 2:
                    raise ValueError("a closing bracket has no opening one")
                grp = stack.pop()
                for k, v in grp.items():
                    stack[-1][k] = stack[-1].get(k, 0) + v * int(t.group(5) or 1)
        if len(stack) != 1:
            raise ValueError("a bracket is not closed")
        for k, v in stack[0].items():
            total[k] = total.get(k, 0) + v * coef
    mass = sum(ATOMIC_WEIGHTS[k] * v for k, v in total.items())
    if mass <= 0:
        raise ValueError("the formula has no atoms")
    return {"formula": formula, "atoms": total, "molar_mass_g_per_mol": round(mass, 4),
            "percent": {k: round(100 * ATOMIC_WEIGHTS[k] * v / mass, 3) for k, v in total.items()}, "source": WEIGHTS_SOURCE}


# ---------------------------------------------------------------- biology: plain sequence statistics (no design, no screening-evading tools)
CODONS = dict(zip((a + b + c for a in "TCAG" for b in "TCAG" for c in "TCAG"), "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"))
MAX_SEQ = 100_000
COMP = str.maketrans("ACGTUNacgtun", "TGCAANtgcaan")


def sequence_stats(seq: str) -> dict:
    """Length, GC content, reverse complement and the frame-1 translation of a DNA/RNA sequence you give (counting and translating only)."""
    s = re.sub(r"\s+", "", (seq or "")).upper().replace("U", "T")
    if not s or len(s) > MAX_SEQ or not re.fullmatch(r"[ACGTN]+", s):
        raise ValueError(f"give a DNA or RNA sequence of letters A, C, G, T/U and N, at most {MAX_SEQ} long")
    gc = (s.count("G") + s.count("C")) / len(s)
    prot = "".join(CODONS.get(s[i:i + 3], "X") for i in range(0, len(s) - 2, 3))
    return {"length": len(s), "gc_fraction": round(gc, 4), "counts": {b: s.count(b) for b in "ACGTN"}, "reverse_complement": s.translate(COMP)[::-1][:2000],
            "translation_frame1": prot[:2000], "note": "Frame 1 only, standard genetic code; 'X' marks a codon with an N. Counting and translating only."}


KINDS = ("calc", "convert", "constant", "formula_mass", "sequence", "integrate", "derivative", "root", "table")


def dispatch(kind: str, expression: str = "", variables: dict | None = None, a: float = 0.0, b: float = 1.0, variable: str = "x", unit_from: str = "", unit_to: str = "") -> dict:
    """One entry point for the local tools (used by both the agent tool and the REST API). Raises ValueError (CalcError is one) with a plain reason."""
    v = variables or {}
    if kind == "calc":
        res = {"result": calc(expression, v), "expression": expression}
    elif kind == "convert":
        try:
            num = float(expression)
        except (TypeError, ValueError) as e:
            raise ValueError("for convert, put the number in expression") from e
        res = convert(num, unit_from, unit_to)
    elif kind == "constant":
        res = constant(expression)
    elif kind == "formula_mass":
        res = formula_mass(expression)
    elif kind == "sequence":
        res = sequence_stats(expression)
    elif kind in ("integrate", "derivative", "root", "table"):
        res = numeric(kind, expression, variable, a, b, v)
    else:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    return {"label": "computed here", "kind": kind, **res}


# ---------------------------------------------------------------- your own code, in the sandbox, with a reproducibility record
MAX_CODE = 20_000
MAX_OUT = 20_000


def run_python(sandbox, code: str, inputs: dict | None = None, seed: int = 0, timeout_s: int = 60, level: str = "standard", now=time.time) -> dict:
    """Run Python in the sandbox (never on the host) and return the output with a record that lets anyone re-run it: the code hash, inputs, seed and output hash."""
    if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE:
        raise ValueError(f"give code of 1 to {MAX_CODE} characters")
    verdict = safety.screen(code, level)
    if verdict.action != "allow":
        return {"ok": False, "blocked": True, "safety": verdict.public()}
    inputs = inputs or {}
    try:
        packed = json.dumps(inputs, sort_keys=True)
    except (TypeError, ValueError) as e:
        raise ValueError("the inputs must be plain JSON values") from e
    if len(packed) > 100_000:
        raise ValueError("the inputs are too large")
    script = f"import json, random\nrandom.seed({int(seed)})\nINPUTS = json.loads({packed!r})\n{code}\n"
    wrapper = "try:\n import numpy; numpy.random.seed(%d)\nexcept Exception:\n pass\n" % int(seed)
    b64 = base64.b64encode((wrapper + script).encode()).decode()
    t0 = now()
    r = sandbox.exec(f"python3 -c \"import base64;exec(compile(base64.b64decode('{b64}').decode(),'science','exec'))\"", timeout_s)
    out = (r.get("stdout") or "")[:MAX_OUT]
    err = (r.get("stderr") or "")[-4000:]
    return {"ok": r.get("exit_code") == 0 and not r.get("timed_out"), "exit_code": r.get("exit_code"), "timed_out": bool(r.get("timed_out")), "stdout": out, "stderr": err,
            "record": {"code_sha256": hashlib.sha256(code.encode()).hexdigest(), "inputs": inputs, "seed": int(seed), "output_sha256": hashlib.sha256(out.encode()).hexdigest(),
                       "environment": f"{getattr(sandbox, 'kind', 'sandbox')} (network {'on' if getattr(sandbox, 'network', False) else 'off'}), python3", "ran_at": t0, "duration_s": round(now() - t0, 3)}}
