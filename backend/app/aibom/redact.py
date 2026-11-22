"""What may leave this machine, at each redaction level.

public   - safe to hand to anyone. Structure, names, versions, licences, models, tool lists. No instruction text, no
           addresses of private servers, no cloud account/project ids, no per-document names.
internal - for your own organisation. Adds private hostnames, account/project ids, document names (hashes), which
           credentials are set (never their values) and per-user skills.
full     - everything internal has, plus the agents' instruction text (secret-looking strings are still masked).

At EVERY level: no API keys, tokens, passwords or other secret values, no environment-variable values, no chat or document
contents, and no absolute paths or user names (paths are rewritten to <workspace>/, <data>/, <home>/ ...).
"""
from __future__ import annotations

import getpass
import hashlib
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

LEVELS = ("public", "internal", "full")
LEVEL_INFO = {
    "public": "Safe to share with anyone. Names, versions, licences, models, tools and the dependency graph. No instruction text, "
              "no private server addresses, no cloud account ids, no document names.",
    "internal": "For your own organisation. Adds private hostnames, cloud account and project ids, document names and sizes (as hashes), "
                "which credentials are set (never their values), and your own skills.",
    "full": "Everything in internal plus each agent's instruction text. Secret-looking strings are masked even here. Only for "
            "places you fully trust.",
}
ORDER = {lvl: i for i, lvl in enumerate(LEVELS)}

SECRET_NAME = re.compile(r"(key|token|secret|passw(or)?d|passwd|credential|auth|bearer|cookie|session|dsn|connection[-_]?string)", re.I)
# Strings that look like credentials whatever they are called.
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|sk_(live|test)_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,}"
    r"|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|xox[abprs]-[A-Za-z0-9-]{10,}|AQ\.[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{16,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@)")                       # user:password@host
MASK = "<redacted>"

_WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|,;]*")
_UNC_PATH = re.compile(r"\\\\[^\s\\\"']+\\[^\s\"'<>|,;]*")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9:/.~-])/(?:Users|home|root|var|tmp|opt|etc|mnt|srv|workspace)(?:/[^\s\"'<>|,;]*)?")
_HOME_TILDE = re.compile(r"(?<![A-Za-z0-9])~[\\/][^\s\"'<>|,;]*")


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def host_class(host: str) -> str:
    """"loopback" | "private" | "public". A name without a dot, or ending .local/.internal/.lan/.home/.corp, is private."""
    h = (host or "").strip("[]").lower().rstrip(".")
    if not h:
        return "private"
    if h == "localhost" or h.endswith(".localhost"):
        return "loopback"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if "." not in h or h.endswith((".local", ".internal", ".lan", ".home", ".corp", ".intranet", ".test", ".invalid", ".example")) \
                or h in ("host.docker.internal", "kubernetes.docker.internal"):
            return "private"
        return "public"
    if ip.is_loopback:
        return "loopback"
    return "private" if (ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast) else "public"


class Redactor:
    """Rewrites values for one redaction level. `roots` maps a path to the label shown in its place."""

    def __init__(self, level: str, roots: dict[str, str] | None = None, users: tuple[str, ...] | None = None):
        if level not in LEVELS:
            raise ValueError(f"unknown redaction level '{level}' (use one of: {', '.join(LEVELS)})")
        self.level = level
        self.rank = ORDER[level]
        self.roots = sorted(((self._norm(p), label) for p, label in (roots or {}).items() if p), key=lambda t: -len(t[0]))
        if users is None:
            users = tuple(u for u in {_safe(getpass.getuser), Path.home().name, os.environ.get("USERNAME", ""), os.environ.get("USER", "")} if u)
        self.users = tuple(u for u in users if len(u) >= 3 and u.lower() not in ("user", "root", "admin"))

    # ------------------------------------------------------------------ level checks
    def at_least(self, level: str) -> bool:
        return self.rank >= ORDER[level]

    @property
    def internal(self) -> bool:
        return self.rank >= 1

    @property
    def full(self) -> bool:
        return self.rank >= 2

    # ------------------------------------------------------------------ text
    @staticmethod
    def _norm(p: str) -> str:
        return str(p).replace("\\", "/").rstrip("/").lower()

    def path(self, value: str) -> str:
        """An absolute path becomes <label>/relative, or <path>/<basename> when it is under no known root."""
        text = str(value)
        norm = text.replace("\\", "/")
        low = norm.lower()
        for root, label in self.roots:
            if low == root or low.startswith(root + "/"):
                return f"<{label}>" + norm[len(root):]
        return norm

    def text(self, value: str) -> str:
        """Mask credentials, user names and absolute paths inside free text."""
        out = SECRET_VALUE.sub(MASK, str(value))
        out = _UNC_PATH.sub(lambda m: self._abs(m.group(0)), out)
        out = _WIN_PATH.sub(lambda m: self._abs(m.group(0)), out)
        out = _POSIX_PATH.sub(lambda m: self._abs(m.group(0)), out)
        out = _HOME_TILDE.sub(lambda m: "<home>/" + m.group(0)[2:].replace("\\", "/"), out)
        for u in self.users:
            out = re.sub(re.escape(u), "<user>", out, flags=re.I)
        return out

    def _abs(self, p: str) -> str:
        mapped = self.path(p)
        if mapped != p.replace("\\", "/"):
            return mapped
        return "<path>/" + (p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "")

    # ------------------------------------------------------------------ urls
    def url(self, value: str | None) -> str | None:
        """Credentials, query and fragment are always dropped. An address on a private/loopback host is omitted (None) below internal."""
        if not value:
            return None
        try:
            parts = urlsplit(value.strip())
            host = parts.hostname or ""
        except ValueError:
            return None
        if not parts.scheme or not host:
            return None
        klass = host_class(host)
        if klass != "public" and not self.internal:
            return None
        port = f":{parts.port}" if parts.port else ""
        path = self.text(parts.path) if parts.path not in ("", "/") else ""
        return f"{parts.scheme}://{host}{port}{path}"

    def endpoint_class(self, value: str | None) -> str:
        try:
            host = urlsplit((value or "").strip()).hostname or ""
        except ValueError:
            host = ""
        return host_class(host) if host else "none"

    # ------------------------------------------------------------------ command lines
    def args(self, args: list[str], trusted: bool) -> list[str]:
        """Arguments of a command. A value that follows a secret-looking flag, or looks like a credential, is masked.
        Arguments of servers the user added themselves (`trusted` False) are kept only at internal and above."""
        if not trusted and not self.internal:
            return []
        out: list[str] = []
        mask_next = False
        for a in args:
            a = str(a)
            if mask_next:
                out.append(MASK)
                mask_next = False
                continue
            if a.startswith("-") and "=" not in a and SECRET_NAME.search(a):
                out.append(a)
                mask_next = True
                continue
            if "=" in a and SECRET_NAME.search(a.split("=", 1)[0]):
                out.append(a.split("=", 1)[0] + "=" + MASK)
                continue
            out.append(self.text(a))
        return out

    def command(self, command: str | None) -> str | None:
        if not command:
            return None
        c = str(command)
        if "/" in c.replace("\\", "/"):                       # an absolute interpreter path: keep just the program name
            c = c.replace("\\", "/").rsplit("/", 1)[-1]
        return self.text(c)

    # ------------------------------------------------------------------ prompts
    def prompt(self, text: str) -> dict:
        """An agent instruction: its hash and length always; the text itself only at `full`."""
        d = {"sha256": sha256_hex(text), "length": len(text)}
        if self.full:
            d["text"] = self.text(text)
        return d


def _safe(fn) -> str:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - no login name available
        return ""


def default_roots(workspace: Path | None, data: Path | None, repo: Path | None) -> dict[str, str]:
    roots: dict[str, str] = {}
    if workspace:
        roots[str(workspace)] = "workspace"
    if data:
        roots[str(data)] = "data"
    if repo:
        roots[str(repo)] = "agent-studio"
    roots[str(Path.home())] = "home"
    return roots
