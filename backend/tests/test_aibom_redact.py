"""Redaction levels: what may leave the machine."""
from __future__ import annotations

import getpass
from pathlib import Path

import pytest

from app.aibom import redact as R


@pytest.mark.parametrize("host,expected", [
    ("", "private"), ("localhost", "loopback"), ("api.localhost", "loopback"), ("127.0.0.1", "loopback"), ("::1", "loopback"), ("[::1]", "loopback"),
    ("10.0.0.5", "private"), ("192.168.1.1", "private"), ("172.16.4.4", "private"), ("169.254.1.1", "private"), ("0.0.0.0", "private"),
    ("224.0.0.1", "private"), ("8.8.8.8", "public"), ("intranet", "private"), ("db.internal", "private"), ("nas.local", "private"),
    ("host.docker.internal", "private"), ("api.openai.com", "public"), ("mcp.example-vendor.io.", "public"), ("thing.corp", "private"),
])
def test_host_class(host, expected):
    assert R.host_class(host) == expected


def red(level="public", users=("Sherif",)):
    return R.Redactor(level, {"C:\\ws\\project": "workspace", "C:/data": "data", "": "ignored", "C:/Users/Sherif": "home"}, users)


def test_levels_and_helpers():
    assert R.LEVELS == ("public", "internal", "full") and set(R.LEVEL_INFO) == set(R.LEVELS)
    pub, internal, full = red("public"), red("internal"), red("full")
    assert (pub.internal, pub.full, internal.internal, internal.full, full.internal, full.full) == (False, False, True, False, True, True)
    assert pub.at_least("public") and not pub.at_least("internal") and full.at_least("internal")
    with pytest.raises(ValueError, match="unknown redaction level"):
        R.Redactor("secret")
    assert R.sha256_hex("a") == R.sha256_hex(b"a") and len(R.sha256_hex("a")) == 64


def test_default_users_come_from_the_environment_and_a_failing_lookup_is_survivable(monkeypatch):
    monkeypatch.setenv("USERNAME", "alexandra")
    monkeypatch.setattr(getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no login")))
    r = R.Redactor("public")
    assert "alexandra" in r.users and r.text("by Alexandra") == "by <user>"
    monkeypatch.setenv("USERNAME", "ab")                                 # too short / generic names are not worth masking
    monkeypatch.setenv("USER", "root")
    monkeypatch.setattr(getpass, "getuser", lambda: "admin")
    assert "ab" not in R.Redactor("public").users and "root" not in R.Redactor("public").users and "admin" not in R.Redactor("public").users


def test_paths_are_mapped_to_labels_or_basenames():
    r = red()
    assert r.path("C:\\ws\\project\\src\\a.py") == "<workspace>/src/a.py" and r.path("c:/DATA") == "<data>" and r.path("/x/y") == "/x/y"
    assert r.text("open C:\\ws\\project\\src\\a.py now") == "open <workspace>/src/a.py now"
    assert r.text("see C:\\Other\\Place\\file.txt.") == "see <path>/file.txt."
    assert r.text("unc \\\\server\\share\\dir\\f.txt end") == "unc <path>/f.txt end"
    assert r.text("in /Users/bob/work/x.py and /home/bob/.ssh/id_rsa") == "in <path>/x.py and <path>/id_rsa"
    assert r.text("or /tmp/") == "or <path>/tmp"
    assert r.text("~/notes/todo.md") == "<home>/notes/todo.md"
    assert r.text("hello SHERIF and sherif") == "hello <user> and <user>"
    assert r.text("C:/Users/Sherif/docs") == "<home>/docs"
    assert r.text("plain text stays") == "plain text stays" and r.text("https://example.com/a/b") == "https://example.com/a/b"


@pytest.mark.parametrize("secret", [
    "sk-proj-abcdefghijklmnop1234", "sk_live_abcdefghijklmnop1234", "ghp_abcdefghijklmnopqrstuvwxyz0123", "github_pat_abcdefghijklmnopqrstuvwxyz",
    "AIzaSyA-abcdefghijklmnopqrstuvwxyz01234", "AKIAIOSFODNN7EXAMPLE", "ASIAIOSFODNN7EXAMPLE", "xoxb-1234567890-abcdef", "AQ.Ab8RN6IexampleVertexKey1234567",
    "glpat-abcdefghijklmnop1234", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fwpMeJf36", "postgres://user:hunter2@db:5432/x",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "-----BEGIN PRIVATE KEY-----\nMIIabc-truncated",
])
def test_credential_shaped_strings_are_masked_in_text(secret):
    out = red().text(f"value {secret} end")
    assert R.MASK in out and secret not in out and "hunter2" not in out and "MIIabc" not in out


def test_urls_drop_credentials_and_query_and_hide_private_hosts_below_internal():
    pub, internal = red("public"), red("internal")
    assert pub.url(None) is None and pub.url("") is None and pub.url("not a url") is None and pub.url("http://[bad") is None and pub.url("file:///x") is None
    assert pub.url("https://user:pw@api.example-vendor.io:8443/v1/mcp?token=abc#frag") == "https://api.example-vendor.io:8443/v1/mcp"
    assert pub.url("https://api.example-vendor.io/") == "https://api.example-vendor.io" and pub.url("http://10.1.2.3:9000/mcp") is None
    assert pub.url("http://localhost:11434") is None and internal.url("http://10.1.2.3:9000/mcp?k=v") == "http://10.1.2.3:9000/mcp"
    assert internal.url("https://h.example-vendor.io/C:\\Users\\Sherif\\x") == "https://h.example-vendor.io/<home>/x" or "Sherif" not in internal.url("https://h.example-vendor.io/Sherif/x")
    assert pub.endpoint_class("http://10.0.0.1/x") == "private" and pub.endpoint_class("https://api.openai.com") == "public"
    assert pub.endpoint_class("") == "none" and pub.endpoint_class(None) == "none" and pub.endpoint_class("http://[bad") == "none" and pub.endpoint_class("nohost") == "none"


def test_command_line_arguments():
    pub, internal = red("public"), red("internal")
    args = ["-m", "srv", "--api-key", "hunter2", "--data", "C:\\ws\\project\\data", "token=abc", "--password=x", "--name", "ok", "sk-proj-abcdefghijklmnop1234"]
    got = internal.args(args, trusted=False)
    assert got == ["-m", "srv", "--api-key", R.MASK, "--data", "<workspace>/data", "token=" + R.MASK, "--password=" + R.MASK, "--name", "ok", R.MASK]
    assert pub.args(args, trusted=False) == [] and pub.args(["-y", "pkg@1.0.0"], trusted=True) == ["-y", "pkg@1.0.0"]
    assert pub.args(["--token", "abc"], trusted=True) == ["--token", R.MASK]
    assert pub.command(None) is None and pub.command("") is None and pub.command("C:\\Users\\Sherif\\tools\\python.exe") == "python.exe" and pub.command("npx") == "npx"
    assert pub.command("/usr/bin/uvx") == "uvx"


def test_prompts_are_hashed_and_only_shown_at_full():
    text = "Use key ghp_abcdefghijklmnopqrstuvwxyz0123456789AB for C:/Users/Sherif/x"
    for lvl in ("public", "internal"):
        d = red(lvl).prompt(text)
        assert set(d) == {"sha256", "length"} and d["length"] == len(text) and d["sha256"] == R.sha256_hex(text)
    d = red("full").prompt(text)
    assert "ghp_" not in d["text"] and "Sherif" not in d["text"] and d["text"].startswith("Use key <redacted>")


def test_default_roots():
    got = R.default_roots(Path("/ws"), Path("/data"), Path("/repo"))
    assert set(got.values()) == {"workspace", "data", "agent-studio", "home"}
    assert set(R.default_roots(None, None, None).values()) == {"home"}
