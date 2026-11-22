"""The built-in rules guard: patterns for personal data, secrets and well-known prompt-injection phrasing, plus your own
patterns and blocked words. Runs on this PC, costs nothing, and is a safety net - not a security boundary (a determined
attacker can rephrase), so it is best used together with a model guard."""
from __future__ import annotations

import re

from .base import OK, Env, Field, Guard, GuardError, Kind, Verdict

DETECTORS = ("pii", "secrets", "injection")

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")

SECRET_PATTERNS = {
    "an AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "a GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "a private key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"),
    "a Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "a Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "an API key (sk-...)": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}

INJECTION = re.compile(
    r"ignore (?:all |any |the |your )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)"
    r"|disregard (?:all |any |the |your )?(?:previous|prior|above|earlier)? ?(?:instructions|rules|guidelines)"
    r"|forget (?:all |everything )?(?:your |the )?(?:previous |prior )?(?:instructions|rules)"
    r"|(?:reveal|show|print|repeat|leak) (?:me )?(?:your |the )?(?:system|hidden|initial) (?:prompt|instructions|message)"
    r"|you are now (?:in )?(?:developer|dan|jailbreak|unrestricted)"
    r"|do anything now|developer mode enabled|<\|im_start\|>|<\|system\|>|\[\[?system\]?\]|begin system prompt",
    re.I)


def luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2:
            n *= 2
            n -= 9 if n > 9 else 0
        total += n
    return total % 10 == 0


def lines(text: str | list | None) -> list[str]:
    if isinstance(text, list):
        return [str(x).strip() for x in text if str(x).strip()]
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def compile_custom(patterns: str | list | None) -> list[re.Pattern]:
    out = []
    for p in lines(patterns):
        try:
            out.append(re.compile(p, re.I))
        except re.error as e:
            raise GuardError(f"'{p[:40]}' is not a valid pattern ({e.msg})") from e
    if len(out) > 50:
        raise GuardError("at most 50 custom patterns")
    return out


class RulesGuard(Guard):
    kind = "rules"

    def __init__(self, detect: list[str], custom: list[re.Pattern], blocklist: list[str]):
        self.detect, self.custom, self.blocklist = detect, custom, [w.lower() for w in blocklist]

    async def check(self, text: str, stage: str) -> Verdict:
        hit = self.find(text, stage)
        return Verdict(True, hit[0], hit[1]) if hit else OK

    def find(self, text: str, stage: str) -> tuple[str, str] | None:
        if "pii" in self.detect:
            if _EMAIL.search(text):
                return "pii", "contains an email address"
            if _SSN.search(text):
                return "pii", "contains something shaped like a social security number"
            if any(luhn_ok(m.group()) for m in _CARD.finditer(text)):
                return "pii", "contains something shaped like a payment card number"
            if _IBAN.search(text):
                return "pii", "contains something shaped like a bank account number (IBAN)"
        if "secrets" in self.detect:
            for label, rx in SECRET_PATTERNS.items():
                if rx.search(text):
                    return "secret", f"contains what looks like {label}"
        if "injection" in self.detect and stage != "output" and INJECTION.search(text):
            return "injection", "contains wording that tries to override the assistant's instructions"
        low = text.lower()
        if any(w in low for w in self.blocklist):
            return "blocked_word", "contains a word on your blocked list"
        if any(rx.search(text) for rx in self.custom):
            return "custom", "matches one of your own patterns"
        return None


def _make(params: dict, secrets: dict, env: Env) -> Guard:
    detect = params.get("detect", list(DETECTORS))
    if not isinstance(detect, list) or [d for d in detect if d not in DETECTORS]:
        raise GuardError(f"detect must be a list of: {', '.join(DETECTORS)}")
    return RulesGuard(detect, compile_custom(params.get("custom_patterns")), lines(params.get("blocklist")))


KIND = Kind(
    id="rules", label="Built-in rules", cost="free", leaves_pc=False, where="stays on this PC",
    summary="Fast pattern checks for personal data, secrets and obvious prompt-injection wording, plus your own patterns and blocked words.",
    fields=(
        Field("detect", "Look for", "choice", default=list(DETECTORS), choices=DETECTORS, help="Personal data, secrets (keys, tokens) and prompt-injection phrasing."),
        Field("blocklist", "Blocked words", "textarea", placeholder="one per line", help="Any text containing one of these words is flagged."),
        Field("custom_patterns", "Your own patterns", "textarea", placeholder="one regular expression per line", help="Case-insensitive regular expressions."),
    ),
    make=_make)
