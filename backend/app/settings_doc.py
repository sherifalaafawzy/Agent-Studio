"""Writes docs/SETTINGS.md from the settings registry, so the documentation cannot drift from the code.

    python -m app.settings_doc          # print it
    python -m app.settings_doc --write  # update docs/SETTINGS.md
"""
from __future__ import annotations

import json
import sys

from .artifacts.service import DEFAULT_CONFIG as ARTIFACT_DEFAULTS
from .auto import DEFAULT_CONFIG as AUTO_DEFAULTS
from .ard.settings import DEFAULTS as ARD_DEFAULTS
from .workflows.config import DEFAULTS as WORKFLOW_DEFAULTS
from .db import REPO_ROOT
from .settings_registry import SETTINGS, TABS

TAB_TITLES = {"providers": "Model providers", "local": "Local models", "auto": "Auto", "harness": "Agents & sandbox", "external": "External agents", "voice": "Voice",
              "telemetry": "Telemetry", "assistant": "Assistant", "discovery": "Discovery & AI-BOM", "memory": "Memory", "security": "Security", "artifacts": "Artifacts", "access": "API & A2A", "runtimes": "Runtimes", "data": "Data sources", "backup": "Backup & history"}


def _allowed(s) -> str:
    if s.kind in ("int", "float"):
        return f"{s.min:g} to {s.max:g}"
    if s.kind == "enum":
        return ", ".join(s.choices)
    if s.kind == "bool":
        return "on / off"
    if s.kind in ("str", "url", "path"):
        return f"text, at most {s.max_len} characters" + (" (http:// or https://)" if s.kind == "url" else " (absolute path)" if s.kind == "path" else "")
    if s.kind == "list":
        return "list" + (f" (at most {s.max_len})" if s.max_len else "")
    return {"secret": "write-only", "blob": "managed by its own page", "counter": "bookkeeping"}.get(s.kind, s.kind)


def _default(s) -> str:
    if s.secret:
        return "(none)"
    d = s.default
    return "empty" if d in ("", [], {}) else ("on" if d is True else "off" if d is False else json.dumps(d))


def render() -> str:
    out = ["# Settings reference", "",
           "Generated from `backend/app/settings_registry.py` (`python -m app.settings_doc --write`). A test fails if this file is out of date, "
           "if a setting used by the code is missing from the registry, or if a setting has no control in the UI.", "",
           "Every change is recorded in **Settings → Backup & history** (secret values are never recorded). "
           "Export/import covers everything below except secrets and bookkeeping; secrets (API keys, tokens, passwords) are never exported or imported.", ""]
    for tab in TABS:
        rows = [s for s in SETTINGS if s.tab == tab]
        if not rows:
            continue
        out += [f"## {TAB_TITLES[tab]}", "", "| Setting | Where | Default | Allowed | What it does | Enforced |", "|---|---|---|---|---|---|"]
        for s in rows:
            out.append(f"| `{s.key}`{' (secret)' if s.secret else ''} — {s.label} | {s.where or tab} | {_default(s)} | {_allowed(s)} | {s.effect} | {s.enforced} |")
        out.append("")
    bookkeeping = [s for s in SETTINGS if s.internal]
    out += ["## Bookkeeping (not editable)", ""] + [f"- `{s.key}` — {s.effect}" for s in bookkeeping] + [""]
    out += ["## Settings kept elsewhere", "",
            "These live in their own tables or objects and have their own validation; the Settings page shows them in the tab named.", "",
            "- **Providers** (tab *Model providers*): enabled, spend cap (USD) and window, max output tokens per call (16 to 1,000,000), default / pro / flash model, "
            "authentication mode and credentials (write-only), API base, extra headers (write-only), Ollama context window, credits.",
            "- **External agents** (tab *External agents*): command, arguments, environment (write-only), enabled, max turns per session (1 to 1000), monthly turn / minute / reported-cost caps.",
            "- **MCP servers, skills, agents, knowledge bases**: managed on their own pages.",
            "- **Catalog** (page *Catalog*): public MCP servers are added with one click and keep their credentials write-only; servers that can change things need a confirm tick, "
            "and servers with a read-only switch start read-only. **Cloud accounts** (Catalog > ML tools): provider credentials are write-only and *Allow create/delete* is off by default. "
            "**ML frameworks** (Ludwig, PyTorch, Keras) are installed one at a time after you accept the size and licence.",
            "- **Runtimes** (tab *Runtimes*): which targets are on, their settings, the image address template and the model keys a deployed agent runs with (write-only). Cloud credentials are write-only; "
            "deploying, switching traffic, rolling back and deleting always ask for a confirmation. Per agent: *Where it runs* in the agent editor.",
            "- **Sandbox** (tab *Agents & sandbox*): kind (Docker container, or the experimental micro VM, off until switched on and never imported), memory, CPUs, processes and the read-only root option; "
            "the network switch is `sandbox_network`. Per agent: *Sandbox* in the agent editor.",
            "- **Data sources** (tab *Data sources*): databases, warehouses and Salesforce orgs, read-only, with row / size / time limits and optional masking of personal data; credentials are write-only and "
            "are not exported or imported. **Data products** (page *Discovery*): names, owners and sensitivity labels, shown in the AI-BOM and discovery catalog. Per agent: the sources it may read.",
            "- **Workflows** (page *Workflows* > Settings) fields and defaults: `" + "`, `".join(f"{k}={json.dumps(v)}" for k, v in WORKFLOW_DEFAULTS.items()) + "`. "
            "Webhooks are off until switched on and are never switched on by an import; credentials for the HTTP request box are write-only and never exported.",
            "- **Auto** (tab *Auto*) fields and defaults: `" + "`, `".join(f"{k}={json.dumps(v)}" for k, v in AUTO_DEFAULTS.items()) + "`.",
            "- **Artifacts** (tab *Artifacts*) fields and defaults: `" + "`, `".join(f"{k}={json.dumps(v)}" for k, v in ARTIFACT_DEFAULTS.items() if k not in ("storages", "ignore")) + "`. "
            "Storages: this PC (always), Amazon S3, S3-compatible, Google Cloud Storage, Azure Blob (SAS), WebDAV; credentials are write-only.",
            "- **Discovery** (tab *Discovery & AI-BOM*) fields and defaults: `" + "`, `".join(f"{k}={json.dumps(v)}" for k, v in ARD_DEFAULTS.items()) + "`.", ""]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    text = render()
    if "--write" in argv:
        path = REPO_ROOT / "docs" / "SETTINGS.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":     # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
