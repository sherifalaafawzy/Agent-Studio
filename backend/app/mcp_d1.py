"""Local "decision model" as an MCP server (stdio): LiquidAI d1-2B, one forward pass, no generated tokens.

d1 is NOT a chat model. It reads a *state* (text / JSON / pictures) and answers named typed questions with calibrated
numbers: yes/no probabilities, single/multi choice, ordered scores, extracted text/numbers/dates, boxes. Use it for routing,
triage, moderation, classification, extraction, judging and reranking - milliseconds on a GPU, no API cost, fully local.

This server runs inside the ISOLATED environment created by the Install step (data/envs/d1), never in the backend venv.
Launch:  <env python> -m app.mcp_d1      (env: D1_HOME=<env folder>, PYTHONPATH=<backend>)
The model loads lazily on the first call and then stays warm. Weights load OFFLINE from the pinned, reviewed revision.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import d1_spec

server = MCPServer("agent-studio-d1", instructions=(
    "A small local DECISION model (LiquidAI d1-2B), not a chat model. Give it a state (text or JSON, optionally images) and "
    "named typed questions; it answers all of them in one forward pass with calibrated probabilities. Good for routing, triage, "
    "moderation, classification, extraction, judging. Poor at open-ended generation and knowledge-heavy questions - use the "
    "main LLM for those. Tools: decide (any questions), classify, yes_no, extract, score, status."))

TYPES = ("noul", "choice", "score", "text", "number", "date", "box", "boxes")
NEEDS_IMAGE = ("box", "boxes")
EXTRACT_TYPES = ("text", "number", "date")
MAX_QUESTIONS = 32
MAX_STATE_CHARS = 200_000            # ~50-60K tokens; the model gets less reliable past about 64K tokens
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 25_000_000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


class Loaded:
    """A loaded model plus how it was placed."""

    def __init__(self, model: Any, device: str, dtype: str):
        self.model, self.device, self.dtype = model, device, dtype
        self.load_seconds = 0.0


_loader = None                       # tests inject a fake: callable() -> Loaded
_loaded: Loaded | None = None
_lock = threading.RLock()            # one forward pass at a time; also guards the lazy load


def _fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def pick_device(torch: Any) -> tuple[str, str]:
    """cuda, then mps, else cpu; bfloat16 on accelerators, float32 on cpu. D1_DEVICE=cpu|cuda|mps forces one."""
    forced = os.environ.get("D1_DEVICE", "").strip().lower()
    if forced in ("cpu", "cuda", "mps"):
        device = forced
    elif torch.cuda.is_available():
        device = "cuda"
    elif getattr(getattr(torch, "backends", None), "mps", None) is not None and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    return device, ("float32" if device == "cpu" else "bfloat16")


def _load_real() -> Loaded:
    h = d1_spec.home()
    sp = d1_spec.spec(d1_spec.active_model())
    if not d1_spec.is_ready(h, sp.id):
        raise RuntimeError(f"the decision model {sp.label} is not installed - open Settings > Auto or MCP servers > Decision model and click Install")
    os.environ["HF_HOME"] = str(d1_spec.hf_home(h))
    os.environ["HF_HUB_OFFLINE"] = "1"                 # the weights were fetched at install time; never reach out now
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from transformers import AutoModel

    device, dtype = pick_device(torch)
    # trust_remote_code runs the repo's Python: it is pinned to the reviewed commit (d1_spec.REVIEW_NOTES), never "main".
    model = AutoModel.from_pretrained(sp.repo, revision=sp.revision, trust_remote_code=True, dtype=getattr(torch, dtype))
    model = model.to(device)
    model.eval()
    return Loaded(model, device, dtype)


def _get() -> tuple[Loaded, bool]:
    """The warm model (loading it on first use) and whether this call loaded it."""
    global _loaded
    with _lock:
        if _loaded is not None:
            return _loaded, False
        t0 = time.perf_counter()
        loaded = (_loader or _load_real)()
        loaded.load_seconds = round(time.perf_counter() - t0, 2)
        _loaded = loaded
        return loaded, True


def _plain(x: Any) -> Any:
    """JSON-safe copy of a model answer (tuples -> lists, tensors/numpy scalars -> numbers, NaN -> None)."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if callable(getattr(x, "item", None)):
        return _plain(x.item())
    return str(x)


def _check_questions(questions: Any, has_images: bool) -> str | None:
    allowed = d1_spec.spec(d1_spec.active_model()).types
    if not isinstance(questions, dict) or not questions:
        return "questions must be a non-empty object: {name: {type, instructions, ...}}"
    if len(questions) > MAX_QUESTIONS:
        return f"at most {MAX_QUESTIONS} questions per call"
    for name, q in questions.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            return "question names must be non-empty strings up to 64 characters"
        if not isinstance(q, dict) or q.get("type") not in TYPES:
            return f"question '{name}': type must be one of {', '.join(TYPES)}"
        if q["type"] not in allowed:
            return f"question '{name}': {d1_spec.spec(d1_spec.active_model()).label} answers only {', '.join(allowed)} questions, not '{q['type']}'"
        if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            return f"question '{name}': instructions (the question text) is required"
        if len(q["instructions"]) > 2000:
            return f"question '{name}': instructions are too long (max 2000 characters)"
        t = q["type"]
        if t == "choice" and not (isinstance(q.get("criteria"), dict) and len(q["criteria"]) >= 2):
            return f"question '{name}': a choice needs criteria {{option: description}} with at least 2 options"
        if t == "score" and not (isinstance(q.get("criteria"), list) and 2 <= len(q["criteria"]) <= 10):
            return f"question '{name}': a score needs criteria = 2 to 10 level labels, lowest first"
        if t in NEEDS_IMAGE and not has_images:
            return f"question '{name}': {t} questions need image_paths"
    return None


def _check_state(state: Any, has_images: bool) -> str | None:
    if state is None:
        return None if has_images else "state is required (text or JSON), or pass image_paths"
    try:
        size = len(state) if isinstance(state, str) else len(json.dumps(state))
    except (TypeError, ValueError):
        return "state must be text or JSON-serialisable"
    return f"state is too large ({size} characters, max {MAX_STATE_CHARS}); split or summarise it" if size > MAX_STATE_CHARS else None


def _open_images(paths: Any) -> tuple[list | None, str | None]:
    if not paths:
        return None, None
    if not isinstance(paths, list) or len(paths) > MAX_IMAGES or not all(isinstance(p, str) for p in paths):
        return None, f"image_paths must be a list of at most {MAX_IMAGES} file paths"
    try:
        from PIL import Image
    except ImportError:
        return None, "pillow is not available in the decision-model environment"
    out = []
    for p in paths:
        f = Path(p).expanduser()
        if f.suffix.lower() not in IMAGE_EXT or not f.is_file():
            return None, f"not an image file ({', '.join(IMAGE_EXT)}): {p}"
        if f.stat().st_size > MAX_IMAGE_BYTES:
            return None, f"image too large (max {MAX_IMAGE_BYTES // 1_000_000} MB): {p}"
        try:
            img = Image.open(f)
            img.load()
            out.append(img.convert("RGB"))
        except Exception as e:  # noqa: BLE001 - a corrupt image is a user error
            return None, f"cannot read image {p}: {type(e).__name__}"
    return out, None


def run(state: Any, questions: Any, image_paths: Any = None, set_size: int | None = None) -> dict:
    """Validate, run one forward pass, return {ok, answers, usage, meta}. Never raises."""
    has_images = bool(image_paths)
    err = _check_state(state, has_images) or _check_questions(questions, has_images)
    if err:
        return _fail(err)
    if set_size is not None and not (isinstance(set_size, int) and not isinstance(set_size, bool) and 1 <= set_size <= 64):
        return _fail("set_size must be an integer from 1 to 64")
    media, err = _open_images(image_paths)
    if err:
        return _fail(err)
    try:
        loaded, fresh = _get()
    except Exception as e:  # noqa: BLE001
        return _fail(f"decision model unavailable: {type(e).__name__}: {str(e)[:300]}")
    sp = d1_spec.spec(d1_spec.active_model())
    kwargs: dict[str, Any] = {sp.image_kw: media}
    if set_size is not None:
        if not sp.set_size:
            return _fail(f"{sp.label} does not support set_size")
        kwargs["set_size"] = set_size
    t0 = time.perf_counter()
    try:
        with _lock:
            out = loaded.model.system_one(state, questions, **kwargs)
    except Exception as e:  # noqa: BLE001 - the model rejects malformed questions with ValueError etc.
        return _fail(f"decision failed: {type(e).__name__}: {str(e)[:300]}")
    meta = {"device": loaded.device, "dtype": loaded.dtype, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "revision": sp.revision[:12], "model": sp.label, "loaded_now": fresh}
    if fresh:
        meta["load_seconds"] = loaded.load_seconds
    return {"ok": True, "answers": _plain(out.get("answers", {})), "usage": _plain(out.get("usage", {})), "meta": meta}


def _single(state: Any, question: dict, name: str = "q") -> dict:
    r = run(state, {name: question})
    if not r["ok"]:
        return r
    return {"ok": True, "answer": r["answers"].get(name, {}), "meta": r["meta"]}


@server.tool(description=(
    "Ask the local decision model several named questions about one state in a single forward pass (milliseconds, no tokens). "
    "state: text, or any JSON value (dict/list), or null when only images are given. questions: {name: question} where a "
    "question is one of: "
    '{"type":"noul","instructions":"Is this a refund request?"} -> {"noul": P(yes)}; '
    '{"type":"choice","instructions":"Which team?","criteria":{"billing":"charges, refunds","tech":"app faults"},"multiple":false} '
    "-> {choice, confidence, probabilities} (multiple:true -> {choices, probabilities}); "
    '{"type":"score","instructions":"How urgent?","criteria":["low","medium","high"]} -> {score, confidence, probabilities, legend}; '
    '{"type":"text"|"number"|"date","instructions":"Who issued the invoice?"} -> {text|number|date, span, found} '
    '(add "range":[0,10],"step":1 to a number to estimate a count/score); '
    '{"type":"box"|"boxes","instructions":"Every cat.","max":4} (needs image_paths). '
    "Values with found < 0.5 come back null. image_paths: up to 8 local image files. Probabilities are calibrated but "
    "you should pick thresholds on your own data."))
def decide(state: Any, questions: dict, image_paths: list[str] | None = None, set_size: int | None = None) -> dict:
    return run(state, questions, image_paths, set_size)


@server.tool(description=(
    "Classify text into one of the given options (or several with multiple=true). options: {label: description} or a list of "
    "labels; descriptions make it more accurate. Returns the label(s), confidence and per-option probabilities."))
def classify(text: Any, options: Any, question: str = "Which category fits best?", multiple: bool = False) -> dict:
    if isinstance(options, list) and all(isinstance(o, str) and o.strip() for o in options):
        options = {o: o for o in options}
    if not isinstance(options, dict) or len(options) < 2:
        return _fail("options must be {label: description} or a list of labels, with at least 2 options")
    q: dict[str, Any] = {"type": "choice", "instructions": question, "criteria": {str(k): str(v) for k, v in options.items()}}
    if multiple:
        q["multiple"] = True
    r = _single(text, q)
    return r if not r["ok"] else {"ok": True, **r["answer"], "meta": r["meta"]}


@server.tool(description="Answer a yes/no question about the text. Returns probability (P(yes), calibrated), answer (true when >= 0.5).")
def yes_no(text: Any, question: str) -> dict:
    r = _single(text, {"type": "noul", "instructions": question})
    if not r["ok"]:
        return r
    p = r["answer"].get("noul")
    return {"ok": True, "probability": p, "answer": (p >= 0.5) if isinstance(p, (int, float)) else None, "meta": r["meta"]}


@server.tool(description=(
    "Extract fields from text. fields: {name: instruction} (extracted as text) or {name: {type: text|number|date, instructions, "
    "range?, step?}}. Returns for each field value (null when not found), found (0-1), span, and the raw model answer. Dates are ISO 8601."))
def extract(text: Any, fields: dict) -> dict:
    if not isinstance(fields, dict) or not fields:
        return _fail("fields must be a non-empty object: {name: instruction | {type, instructions}}")
    questions: dict[str, Any] = {}
    for name, spec in fields.items():
        if isinstance(spec, str):
            spec = {"type": "text", "instructions": spec}
        elif isinstance(spec, dict):
            spec = {"instructions": str(name), **spec}
            spec.setdefault("type", "text")
        if not isinstance(spec, dict) or spec.get("type") not in EXTRACT_TYPES:
            return _fail(f"field '{name}': type must be one of {', '.join(EXTRACT_TYPES)}")
        questions[str(name)] = spec
    r = run(text, questions)
    if not r["ok"]:
        return r
    out = {}
    for name, a in r["answers"].items():
        a = a if isinstance(a, dict) else {}
        out[name] = {"type": questions[name]["type"], "value": a.get(questions[name]["type"]), "found": a.get("found"),
                     "span": a.get("span"), "raw": a}
    return {"ok": True, "fields": out, "meta": r["meta"]}


@server.tool(description=(
    "Rate text on an ordered scale. levels: 2-10 labels, lowest first (e.g. [\"low\",\"medium\",\"high\"]). Returns the median "
    "level label, numeric score, confidence and probabilities per level."))
def score(text: Any, question: str, levels: list[str]) -> dict:
    r = _single(text, {"type": "score", "instructions": question, "criteria": levels})
    if not r["ok"]:
        return r
    a = r["answer"]
    legend = a.get("legend") or {}
    s = a.get("score")
    label = legend.get(str(int(s))) if isinstance(s, (int, float)) else None
    return {"ok": True, "level": label, **a, "meta": r["meta"]}


@server.tool(description="Report whether the decision model is installed, loaded and warm, which device it uses, and its licence terms. Does not load the model.")
def status() -> dict:
    h = d1_spec.home()
    sp = d1_spec.spec(d1_spec.active_model())
    return {"ok": True, "installed": d1_spec.is_ready(h, sp.id), "loaded": _loaded is not None,
            "device": _loaded.device if _loaded else None, "dtype": _loaded.dtype if _loaded else None,
            "model": sp.repo, "revision": sp.revision, "question_types": list(sp.types),
            "license": {"name": d1_spec.LICENSE_NAME, "url": sp.license_url, "summary": d1_spec.LICENSE_SUMMARY}}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
