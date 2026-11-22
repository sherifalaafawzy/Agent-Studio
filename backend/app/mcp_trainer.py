"""Model trainer as an MCP server (stdio): train and evaluate models with Ludwig, PyTorch or Keras on a CSV in the workspace.

Runs in the backend's Python (it only orchestrates); the heavy frameworks live in isolated environments that the user installs
from Catalog > ML tools. Training runs as a background job, so `train` returns at once with a job id: poll `job`, stop with `cancel`.
Launch:  python -m app.mcp_trainer     (env: TRAINER_WORKSPACE=<workspace>, PYTHONPATH=<backend>)
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import trainer as T

server = MCPServer("agent-studio-trainer", instructions=(
    "Train and evaluate machine-learning models on a CSV file in the workspace with Ludwig, PyTorch or Keras. Call `status` first: "
    "a framework must be installed by the user (Catalog > ML tools) before it can be used. `train` starts a background job and "
    "returns a job id; poll `job` until state is done or failed, `cancel` stops it, `evaluate` scores a finished model on another CSV. "
    "Always report the baseline metrics next to the model's, and never tune on the evaluation file."))

_trainer: T.Trainer | None = None


def trainer() -> T.Trainer:
    global _trainer
    if _trainer is None:
        _trainer = T.Trainer()
    return _trainer


def workspace() -> Path | None:
    w = os.environ.get("TRAINER_WORKSPACE", "").strip()
    return Path(w) if w else None


def _guard(fn, *a, **kw) -> dict:
    try:
        return {"ok": True, **fn(*a, **kw)}
    except T.TrainerError as e:
        return {"ok": False, "error": str(e)}
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": f"bad input: {e}"}


@server.tool(description="Which training frameworks (ludwig, pytorch, keras) are installed, and the recent jobs. Installing is done by the user in the app, not here.")
def status() -> dict:
    s = trainer().status()
    return {"ok": True, "frameworks": [{k: f[k] for k in ("id", "label", "state", "version", "licence")} for f in s["frameworks"]],
            "jobs_running": s["jobs_running"], "recent_jobs": [{k: j[k] for k in ("id", "state", "framework", "task", "target", "name")} for j in trainer().jobs(10)]}


@server.tool(description="Example settings for `train`: classification and regression templates, and an optional Ludwig config.")
def templates() -> dict:
    return {"ok": True, "templates": T.TEMPLATES, "notes": [
        "dataset must be a .csv inside the workspace, with a header row. target is the column to predict.",
        "Rows with an empty target are skipped. Text columns are one-hot encoded, numeric columns standardised.",
        "Start with epochs 5-10 to check the pipeline, then raise it. max_minutes stops a long job."]}


@server.tool(description=(
    "Start training in the background. framework: ludwig | pytorch | keras (must be installed). dataset: path to a .csv in the workspace. "
    "target: the column to predict. task: classification | regression. Optional: epochs (default 20), hidden (layer sizes, default [64,32]; "
    "pytorch/keras), learning_rate, batch_size, test_size (held-out share, default 0.2), seed, max_minutes (default 30), name, "
    "config (Ludwig only: a full Ludwig config object). Returns a job id at once - then poll `job`. Uses this PC's CPU/GPU for the duration."))
def train(framework: str, dataset: str, target: str, task: str = "classification", epochs: int = 20, hidden: list[int] | None = None,
          learning_rate: float = 0.001, batch_size: int = 32, test_size: float = 0.2, seed: int = 42, max_minutes: float = T.DEFAULT_MINUTES,
          name: str = "", config: dict | None = None) -> dict:
    spec = dict(framework=framework, dataset=dataset, target=target, task=task, epochs=epochs, hidden=hidden, learning_rate=learning_rate,
                batch_size=batch_size, test_size=test_size, seed=seed, max_minutes=max_minutes, name=name, config=config)
    return _guard(lambda: {"job": trainer().start_train(spec, workspace())})


@server.tool(description="State of one job: queued/running/done/failed/cancelled, epoch progress, the last log lines (`tail`, default 20), and the metrics once done.")
def job(job_id: str, tail: int = 20) -> dict:
    return _guard(lambda: {"job": trainer().job(job_id, min(max(int(tail), 0), 200))})


@server.tool(description="List recent jobs (newest first) with their state and metrics.")
def jobs(limit: int = 10) -> dict:
    return {"ok": True, "jobs": trainer().jobs(min(max(int(limit), 1), 50))}


@server.tool(description="Stop a running job (kills its process). The partial results stay on disk.")
def cancel(job_id: str) -> dict:
    return _guard(lambda: {"job": trainer().cancel(job_id)})


@server.tool(description=(
    "Score a FINISHED training job's model on another CSV in the workspace (it must contain the target column). Starts a short background "
    "job: poll `job` with the returned id for the metrics. Use a file the model was not trained or tuned on."))
def evaluate(job_id: str, dataset: str) -> dict:
    return _guard(lambda: {"job": trainer().start_evaluate(job_id, dataset, workspace())})


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
