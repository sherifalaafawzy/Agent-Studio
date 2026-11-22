"""Shared pieces of the isolated-environment installers (red-team tools, trainer frameworks, agent frameworks).

The rules every installer follows:

* **Ready-made wheels only** (`--only-binary=:all:`). Building a package from source runs its setup code on this PC, so it is off.
* **Nothing younger than 14 days** (`--uploaded-prior-to <today - 14 days>`).
* **One narrow exception - `SOURCE_BUILDS`.** A few small packages that a tool needs publish no usable wheel at all (found by resolving
  garak 0.17.1, PyRIT 1.1.0 and Ludwig 0.17.10 with pip's `--dry-run --report` on 2026-11-22: ecoji, langdetect and gpustat were the only
  ones). Each one was read before it was listed: pure Python, a plain `setup.py`, published long ago. They are installed at an exact
  pinned version with `--no-binary <name>`, and only for the tool that needs them. Nothing else is ever built from source.
* A failed step is explained in plain words (what could not be found, why, what to do) instead of showing pip's raw last lines.
"""
from __future__ import annotations

import os
import platform
import re
import sys

# name -> what we checked. A package may be added here only after reading its sdist and confirming it is >= 14 days old.
SOURCE_BUILDS: dict[str, dict] = {
    "ecoji": dict(version="0.1.1", published="2024-07-13", publisher="mecforlove", licence="Apache",
                  why="PyPI has only a source archive (no wheel); pure Python, no dependencies, plain setup.py"),
    "langdetect": dict(version="1.0.9", published="2021-05-07", publisher="Michal Danilak", licence="MIT",
                       why="its only wheel is tagged Python 2, so pip cannot use it on Python 3; pure Python, depends only on six, plain setup.py"),
    "gpustat": dict(version="1.1.1", published="2023-08-22", publisher="Jongwook Choi", licence="MIT",
                    why="PyPI has only a source archive (no wheel); pure Python; its build needs setuptools_scm, which pip fetches as a wheel"),
}


def base_python() -> str:
    """The Python the isolated environments are created with: this one, unless AGENT_STUDIO_ENV_PYTHON names another python.exe."""
    return (os.environ.get("AGENT_STUDIO_ENV_PYTHON") or "").strip() or sys.executable


def python_label() -> str | None:
    """The version of the base Python when it is this one; None when the user picked another (we do not start it just to ask)."""
    return None if (os.environ.get("AGENT_STUDIO_ENV_PYTHON") or "").strip() else platform.python_version()


def install_cmd(py: str, cutoff: str, packages, source_builds=(), extra=()) -> list[str]:
    """`pip install` with the 14-day cutoff, wheels only, and a source build allowed only for the named vetted packages (pinned)."""
    for n in source_builds:
        if n not in SOURCE_BUILDS:
            raise ValueError(f"'{n}' is not on the vetted source-build list")
    cmd = [py, "-m", "pip", "install", "--no-input", "--only-binary=:all:"]
    for n in source_builds:
        cmd += ["--no-binary", n]
    return [*cmd, "--uploaded-prior-to", cutoff, *extra, *packages, *(f"{n}=={SOURCE_BUILDS[n]['version']}" for n in source_builds)]


def source_note(source_builds) -> str:
    """One plain sentence for the install plan; empty when nothing is built from source."""
    names = list(source_builds)
    if not names:
        return ""
    return (f"Builds {', '.join(names)} from source: {'it publishes' if len(names) == 1 else 'they publish'} no ready-made package. "
            f"{'It is a small pure-Python package' if len(names) == 1 else 'They are small pure-Python packages'} we checked, at a fixed version; "
            "its setup script runs on this PC during the install. Nothing else is built from source.")


_NO_DIST = re.compile(r"(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+([A-Za-z0-9_.-]+)")
_BUILD_FAIL = re.compile(r"Failed building wheel|subprocess-exited-with-error|Getting requirements to build wheel|metadata-generation-failed|Failed to build", re.I)
_NETWORK = re.compile(r"Could not fetch URL|Connection (?:aborted|reset|refused)|Max retries exceeded|getaddrinfo|Name or service not known|"
                      r"SSLError|ProxyError|ReadTimeout|ConnectTimeout|ETIMEDOUT|ENOTFOUND|ECONNRESET|EAI_AGAIN|network is unreachable", re.I)
_ENGINE = re.compile(r"EBADENGINE|Unsupported engine", re.I)
_NO_NPM_VERSION = re.compile(r"ETARGET|notarget|No matching version found")


def _tail(lines: list[str], n: int = 2) -> str:
    return " | ".join(ln[:160] for ln in lines[-n:])


def explain_failure(label: str, rc: int, lines: list[str], python: str | None = None) -> str:
    """The message for a failed install step: plain words first, then what the tool itself said."""
    lines = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("[notice]")]
    text = "\n".join(lines)
    head = f"Step '{label}' failed (exit code {rc}). "
    py = f"Python {python}" if python else "the Python you chose"
    pick = ("To use a different Python, set the environment variable AGENT_STUDIO_ENV_PYTHON to the full path of a python.exe of a version the tool supports, "
            "restart Agent Studio and try again.")
    nd = _NO_DIST.search(text)
    if rc == 127:
        why = "The program could not be started. If you set AGENT_STUDIO_ENV_PYTHON, check that it is the full path of a python.exe."
    elif nd:
        pkg = nd.group(1)
        skipped = (f" pip also skipped some versions because they need a different Python than {py}; if the tool's note names another Python version, "
                   "use that one.") if "different python version" in text.lower() else ""
        why = (f"pip found no ready-made package for '{pkg}' that works with {py} on this PC. Agent Studio installs ready-made packages only, because "
               f"building from source runs a package's setup code, and '{pkg}' is not on its short list of checked packages that may be built.{skipped} "
               f"If you need this tool, report it so '{pkg}' can be checked. {pick}")
    elif _BUILD_FAIL.search(text):
        why = ("Building one of the small packages from source failed. The lines below say which; the usual causes are a missing C/C++ compiler or no "
               "access to the package index for the build tools. Nothing was installed.")
    elif _NO_NPM_VERSION.search(text):
        why = "npm found no version old enough: Agent Studio only installs versions published at least 14 days ago."
    elif _ENGINE.search(text):
        why = "This tool needs a newer Node.js than the one installed. Install the Node.js version named in the tool's note and try again."
    elif _NETWORK.search(text):
        why = "The package index could not be reached (network, proxy or certificate). Check the connection and try again; nothing was installed."
    else:
        why = "The tool's own message is below."
    tail = _tail(lines)
    return head + why + (f" ({tail})" if tail else "")
