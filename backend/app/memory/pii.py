"""Remove personal and secret data from text BEFORE it is remembered. Deterministic patterns only (no model, no network)."""
from __future__ import annotations

import re

from ..aibom.redact import SECRET_VALUE

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?![\w])")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if flip:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
        flip = not flip
    return total % 10 == 0


def scrub(text: str) -> tuple[str, list[str]]:
    """(cleaned text, the kinds removed: secret / card / ssn / email / phone). Order matters: secrets and cards first."""
    kinds: list[str] = []

    def sub(pattern: re.Pattern, kind: str, label: str, s: str, check=None) -> str:
        def one(m: re.Match) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            if kind not in kinds:
                kinds.append(kind)
            return label
        return pattern.sub(one, s)

    out = sub(SECRET_VALUE, "secret", "[secret removed]", text)
    out = sub(_CARD, "card", "[card number removed]", out, lambda g: _luhn(re.sub(r"\D", "", g)) and 13 <= len(re.sub(r"\D", "", g)) <= 19)
    out = sub(_SSN, "ssn", "[ID number removed]", out)
    out = sub(_EMAIL, "email", "[email removed]", out)
    out = sub(_PHONE, "phone", "[phone removed]", out)
    return out, kinds
