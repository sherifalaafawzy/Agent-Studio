"""What gets deployed: the agent's frozen definition (no secrets), a hash of it, the files of a container bundle and the commands that
build and push it. Everything here is pure - nothing talks to Docker or a cloud."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..agents import AgentDef

RUNNER = Path(__file__).with_name("container_runner.py")
PORT = 8080
BASE_IMAGE = "python:3.12-slim"
PINS = {"fastapi": "0.165.0", "uvicorn": "0.54.0", "litellm": "1.108.3"}          # the same pins as the backend (all older than 14 days)

DOCKERFILE = f"""FROM {BASE_IMAGE}
ENV PYTHONUNBUFFERED=1 PORT={PORT}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY runner.py agent.json ./
RUN useradd -r -u 10001 agent
USER 10001
EXPOSE {PORT}
CMD ["python", "runner.py"]
"""

# What each target needs from an image (checked against the vendors' docs; see targets.py).
PLATFORMS = {"docker": "linux/amd64", "agentcore": "linux/arm64", "foundry": "linux/amd64", "vertex": "linux/amd64"}


def agent_spec(a: AgentDef, model: str) -> dict:
    """The part of an agent that a runtime needs. `model` is the LiteLLM model string; keys are never part of it."""
    return {"id": a.id, "name": a.name, "description": a.description, "instruction": a.instruction, "model": model, "max_tokens": 2048}


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spec_diff(old: dict | None, new: dict) -> list[dict]:
    """What differs between what is deployed and what the agent is now (long text is shortened)."""
    def short(v: object) -> str:
        s = str(v)
        return s if len(s) <= 160 else s[:157] + "..."
    if old is None:
        return []
    return [{"field": k, "before": short(old.get(k, "")), "after": short(new.get(k, ""))} for k in sorted({*old, *new}) if old.get(k) != new.get(k)]


def write_bundle(folder: Path, spec: dict) -> list[str]:
    """Write the files `docker build` needs into `folder` and return their names."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agent.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (folder / "runner.py").write_text(RUNNER.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "requirements.txt").write_text("".join(f"{k}=={v}\n" for k, v in PINS.items()), encoding="utf-8")
    (folder / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    return ["Dockerfile", "agent.json", "requirements.txt", "runner.py"]


def default_image(template: str, agent_id: str, digest: str) -> str:
    """`{agent}` and `{tag}` in the registry template become the agent id and a short fingerprint of its definition."""
    return template.replace("{agent}", agent_id).replace("{tag}", digest[:12])


def image_ok(image: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-/:@]{2,300}", image or "")) and not image.startswith("-")


def commands(target: str, image: str, folder: str, cfg: dict) -> list[str]:
    """The exact shell commands that build the image and put it where the platform can pull it (shown to the user, never run by us for remote targets)."""
    plat = PLATFORMS.get(target, "linux/amd64")
    build = f'docker build --platform {plat} -t {image} "{folder}"'
    host = image.split("/")[0]
    if target == "agentcore":
        region = cfg.get("region") or "<region>"
        return [f"aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {host}", build, f"docker push {image}"]
    if target == "foundry":
        return [f"az acr login --name {host.split('.')[0]}", build, f"docker push {image}"]
    if target == "vertex":
        return [f"gcloud auth configure-docker {host}", build, f"docker push {image}"]
    return [build]
