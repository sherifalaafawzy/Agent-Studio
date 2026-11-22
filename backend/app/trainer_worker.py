"""Training worker: runs INSIDE a framework's isolated environment as `python -m app.trainer_worker <job dir>`.

Standard library only at module level (the environment has the ML framework, not the backend's packages). It reads
`spec.json`, prepares the CSV with plain Python (so the same code serves PyTorch and Keras), trains, evaluates on a held-out
split and writes `status.json`, `metrics.json` and the model artifact next to the spec. Ludwig prepares and splits its own data.

The three `fit_*` / `predict_*` pairs at the bottom are the only code that needs a real ML framework; they are thin and are
the one part of this module that is not unit-tested (no framework is installed in the test environment).
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
import time
from pathlib import Path

MAX_LEVELS = 40
MAX_ROWS = 500_000


# --------------------------------------------------------------------------------------------------------------------- io
def read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(p: Path, d: dict) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)


def set_status(d: Path, **kw) -> None:
    st = read_json(d / "status.json")
    if st.get("state") == "cancelled" and kw.get("state") not in (None, "cancelled"):
        return                                   # the user cancelled: never overwrite that
    write_json(d / "status.json", {**st, **kw, "updated": time.time()})


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------------------- data preparation
def read_csv(path: str) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        header = [c.strip() for c in next(rd)]
        rows = []
        for i, r in enumerate(rd):
            if i >= MAX_ROWS:
                break
            if len(r) == len(header):
                rows.append(r)
    if not rows:
        raise ValueError("the CSV has no usable rows (every row must have the same number of columns as the header)")
    return header, rows


def is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def build_prep(header: list[str], rows: list[list[str]], target: str, task: str) -> dict:
    """Learn how to encode the data: numeric columns are standardised, text columns one-hot encoded (rare levels -> 'other')."""
    ti = header.index(target)
    feats = []
    for i, name in enumerate(header):
        if i == ti:
            continue
        vals = [r[i].strip() for r in rows]
        present = [v for v in vals if v != ""]
        if present and all(is_number(v) for v in present):
            nums = [float(v) for v in present]
            mean = sum(nums) / len(nums)
            std = math.sqrt(sum((x - mean) ** 2 for x in nums) / len(nums)) or 1.0
            feats.append(dict(name=name, index=i, kind="num", mean=mean, std=std))
        else:
            counts: dict[str, int] = {}
            for v in vals:
                counts[v or "missing"] = counts.get(v or "missing", 0) + 1
            levels = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_LEVELS]]
            feats.append(dict(name=name, index=i, kind="cat", levels=levels))
    prep = dict(features=feats, target=target, task=task, columns=header)
    if task == "classification":
        classes = sorted({r[ti].strip() for r in rows if r[ti].strip() != ""})
        if len(classes) < 2:
            raise ValueError(f"the target column '{target}' needs at least two different values for classification")
        if len(classes) > 100:
            raise ValueError(f"the target column '{target}' has {len(classes)} different values - that looks like a regression target")
        prep["classes"] = classes
    return prep


def feature_names(prep: dict) -> list[str]:
    out = []
    for f in prep["features"]:
        out += [f["name"]] if f["kind"] == "num" else [f"{f['name']}={lv}" for lv in f["levels"]] + [f"{f['name']}=other"]
    return out


def encode(prep: dict, row: list[str]) -> list[float]:
    x: list[float] = []
    for f in prep["features"]:
        v = row[f["index"]].strip() if f["index"] < len(row) else ""
        if f["kind"] == "num":
            x.append(((float(v) if v != "" and is_number(v) else f["mean"]) - f["mean"]) / f["std"])
        else:
            v = v or "missing"
            hot = [1.0 if v == lv else 0.0 for lv in f["levels"]]
            x += hot + [0.0 if any(hot) else 1.0]
    return x


def transform(prep: dict, header: list[str], rows: list[list[str]], with_target: bool) -> tuple[list[list[float]], list]:
    """Encode rows (the CSV's columns may be in a different order than at training time)."""
    pos = {c: i for i, c in enumerate(header)}
    order = [pos.get(c) for c in prep["columns"]]
    X, y = [], []
    for r in rows:
        aligned = [r[i].strip() if i is not None else "" for i in order]
        if with_target:
            t = aligned[prep["columns"].index(prep["target"])]
            if t == "":
                continue
            if prep["task"] == "classification":
                if t not in prep["classes"]:
                    continue
                y.append(prep["classes"].index(t))
            else:
                if not is_number(t):
                    continue
                y.append(float(t))
        X.append(encode(prep, aligned))
    return X, y


def split_indices(n: int, test_size: float, seed: int) -> tuple[list[int], list[int]]:
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    k = min(max(int(round(n * test_size)), 1), n - 1) if n > 1 else 0
    return idx[k:], idx[:k]


def classification_metrics(y: list[int], p: list[int], classes: list[str]) -> dict:
    n = len(y)
    acc = sum(1 for a, b in zip(y, p) if a == b) / n
    f1s, per = [], {}
    for c, name in enumerate(classes):
        tp = sum(1 for a, b in zip(y, p) if a == c and b == c)
        fp = sum(1 for a, b in zip(y, p) if a != c and b == c)
        fn = sum(1 for a, b in zip(y, p) if a == c and b != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if tp + fn:
            f1s.append(f1)
        per[name] = dict(precision=round(prec, 4), recall=round(rec, 4), f1=round(f1, 4), support=tp + fn)
    majority = max(sum(1 for a in y if a == c) for c in range(len(classes))) / n
    return dict(n=n, accuracy=round(acc, 4), macro_f1=round(sum(f1s) / len(f1s), 4) if f1s else 0.0, majority_class_baseline=round(majority, 4), per_class=per)


def regression_metrics(y: list[float], p: list[float]) -> dict:
    n = len(y)
    mean = sum(y) / n
    mae = sum(abs(a - b) for a, b in zip(y, p)) / n
    mse = sum((a - b) ** 2 for a, b in zip(y, p)) / n
    var = sum((a - mean) ** 2 for a in y) / n
    return dict(n=n, mae=round(mae, 6), rmse=round(math.sqrt(mse), 6), r2=round(1 - mse / var, 4) if var else 0.0,
                mean_baseline_mae=round(sum(abs(a - mean) for a in y) / n, 6))


def score(prep: dict, y: list, p: list) -> dict:
    return classification_metrics(y, p, prep["classes"]) if prep["task"] == "classification" else regression_metrics(y, p)


# ------------------------------------------------------------------------------------------------------------------- the job
def ludwig_config(spec: dict, prep: dict) -> dict:
    if spec.get("config"):
        return spec["config"]
    clf = spec["task"] == "classification"
    return {"input_features": [{"name": f["name"], "type": "number" if f["kind"] == "num" else "category"} for f in prep["features"]],
            "output_features": [{"name": spec["target"], "type": "category" if clf else "number"}],
            "trainer": {"epochs": spec["epochs"], "learning_rate": spec["learning_rate"], "batch_size": spec["batch_size"]}}


def run_train(d: Path, spec: dict) -> dict:
    header, rows = read_csv(spec["dataset"])
    if spec["target"] not in header:
        raise ValueError(f"target column '{spec['target']}' not found")
    prep = build_prep(header, rows, spec["target"], spec["task"])
    out = d / "model"
    out.mkdir(exist_ok=True)
    write_json(d / "prep.json", prep)
    log(f"{len(rows)} rows, {len(prep['features'])} input columns, target '{spec['target']}' ({spec['task']})")
    started = time.time()

    def progress(epoch: int, epochs: int, loss: float, val: float | None) -> bool:
        set_status(d, state="running", epoch=epoch, progress=round(epoch / epochs, 3))
        log(f"epoch {epoch}/{epochs} loss={loss:.5f}" + (f" val={val:.5f}" if val is not None else ""))
        return time.time() - started < spec["max_minutes"] * 60 and read_json(d / "status.json").get("state") != "cancelled"

    if spec["framework"] == "ludwig":
        metrics = fit_ludwig(spec, ludwig_config(spec, prep), out, log)
        return dict(framework="ludwig", **metrics)
    X, y = transform(prep, header, rows, True)
    if len(X) < 10:
        raise ValueError(f"only {len(X)} usable rows - need at least 10")
    tr, va = split_indices(len(X), spec["test_size"], spec["seed"])
    fit, predict = ADAPTERS[spec["framework"]]
    artifact = fit([X[i] for i in tr], [y[i] for i in tr], [X[i] for i in va], [y[i] for i in va], spec, prep, out, progress)
    preds = predict(out, [X[i] for i in va], prep)
    m = score(prep, [y[i] for i in va], preds)
    m.update(framework=spec["framework"], train_rows=len(tr), features=len(X[0]), seconds=round(time.time() - started, 1), artifact=artifact)
    return m


def run_evaluate(d: Path, spec: dict) -> dict:
    src = d.parent / spec["model_job"]
    prep, sspec = read_json(src / "prep.json"), read_json(src / "spec.json")
    if spec["framework"] == "ludwig":
        return dict(framework="ludwig", **eval_ludwig(src / "model", spec["dataset"], log))
    header, rows = read_csv(spec["dataset"])
    X, y = transform(prep, header, rows, True)
    if not X:
        raise ValueError("no rows in the evaluation file have a usable target value")
    m = score(prep, y, ADAPTERS[sspec["framework"]][1](src / "model", X, prep))
    return dict(m, framework=spec["framework"], evaluated_model=spec["model_job"])


def main(argv: list[str]) -> int:
    d = Path(argv[0])
    spec = read_json(d / "spec.json")
    set_status(d, state="running", worker_pid=os.getpid())
    try:
        metrics = run_evaluate(d, spec) if spec.get("mode") == "evaluate" else run_train(d, spec)
        write_json(d / "metrics.json", metrics)
        set_status(d, state="done", progress=1.0, artifact=metrics.get("artifact"))
        log("done: " + json.dumps({k: v for k, v in metrics.items() if not isinstance(v, dict)}))
        return 0
    except Exception as e:  # noqa: BLE001 - the message is what the user sees
        log(f"FAILED: {type(e).__name__}: {e}")
        set_status(d, state="failed", error=f"{type(e).__name__}: {str(e)[:400]}")
        return 1


# ----------------------------------------------------------------------------------- framework adapters (need the real library)
def fit_torch(Xtr, ytr, Xva, yva, spec, prep, out: Path, progress) -> str:  # pragma: no cover
    import torch
    from torch import nn
    torch.manual_seed(spec["seed"])
    clf = prep["task"] == "classification"
    dim, hidden, n_out = len(Xtr[0]), spec["hidden"], len(prep.get("classes", [])) if clf else 1
    layers, prev = [], dim
    for h in hidden:
        layers += [nn.Linear(prev, h), nn.ReLU()]
        prev = h
    model = nn.Sequential(*layers, nn.Linear(prev, n_out))
    lossf = nn.CrossEntropyLoss() if clf else nn.MSELoss()
    ydt = torch.long if clf else torch.float32
    xt, yt = torch.tensor(Xtr, dtype=torch.float32), torch.tensor(ytr, dtype=ydt)
    xv, yv = torch.tensor(Xva, dtype=torch.float32), torch.tensor(yva, dtype=ydt)
    opt = torch.optim.Adam(model.parameters(), lr=spec["learning_rate"])
    bs = max(1, spec["batch_size"])
    for ep in range(1, spec["epochs"] + 1):
        model.train()
        perm, total = torch.randperm(len(xt)), 0.0
        for i in range(0, len(xt), bs):
            b = perm[i:i + bs]
            opt.zero_grad()
            pred = model(xt[b])
            loss = lossf(pred if clf else pred.squeeze(1), yt[b])
            loss.backward()
            opt.step()
            total += float(loss) * len(b)
        model.eval()
        with torch.no_grad():
            pv = model(xv)
            vloss = float(lossf(pv if clf else pv.squeeze(1), yv))
        if not progress(ep, spec["epochs"], total / len(xt), vloss):
            break
    torch.save(model.state_dict(), out / "model.pt")
    write_json(out / "arch.json", dict(dim=dim, hidden=hidden, n_out=n_out))
    return "model.pt"


def predict_torch(out: Path, X, prep) -> list:  # pragma: no cover
    import torch
    from torch import nn
    a = read_json(out / "arch.json")
    layers, prev = [], a["dim"]
    for h in a["hidden"]:
        layers += [nn.Linear(prev, h), nn.ReLU()]
        prev = h
    model = nn.Sequential(*layers, nn.Linear(prev, a["n_out"]))
    model.load_state_dict(torch.load(out / "model.pt", weights_only=True))
    model.eval()
    with torch.no_grad():
        p = model(torch.tensor(X, dtype=torch.float32))
    return p.argmax(1).tolist() if prep["task"] == "classification" else p.squeeze(1).tolist()


def fit_keras(Xtr, ytr, Xva, yva, spec, prep, out: Path, progress) -> str:  # pragma: no cover
    import numpy as np
    import tensorflow as tf
    tf.keras.utils.set_random_seed(spec["seed"])
    clf = prep["task"] == "classification"
    layers = [tf.keras.layers.Input(shape=(len(Xtr[0]),))] + [tf.keras.layers.Dense(h, activation="relu") for h in spec["hidden"]]
    layers.append(tf.keras.layers.Dense(len(prep["classes"]), activation="softmax") if clf else tf.keras.layers.Dense(1))
    model = tf.keras.Sequential(layers)
    model.compile(optimizer=tf.keras.optimizers.Adam(spec["learning_rate"]), loss="sparse_categorical_crossentropy" if clf else "mse")

    class Report(tf.keras.callbacks.Callback):
        def on_epoch_end(self, epoch, logs=None):
            logs = logs or {}
            if not progress(epoch + 1, spec["epochs"], float(logs.get("loss", 0.0)), float(logs.get("val_loss", 0.0))):
                self.model.stop_training = True

    model.fit(np.array(Xtr, dtype="float32"), np.array(ytr), validation_data=(np.array(Xva, dtype="float32"), np.array(yva)),
              epochs=spec["epochs"], batch_size=max(1, spec["batch_size"]), verbose=0, callbacks=[Report()])
    model.save(out / "model.keras")
    return "model.keras"


def predict_keras(out: Path, X, prep) -> list:  # pragma: no cover
    import numpy as np
    import tensorflow as tf
    p = tf.keras.models.load_model(out / "model.keras").predict(np.array(X, dtype="float32"), verbose=0)
    return p.argmax(1).tolist() if prep["task"] == "classification" else p[:, 0].tolist()


def fit_ludwig(spec: dict, config: dict, out: Path, logf) -> dict:  # pragma: no cover
    from ludwig.api import LudwigModel
    model = LudwigModel(config, logging_level=30)
    model.train(dataset=spec["dataset"], output_directory=str(out / "results"), skip_save_processed_input=True, random_seed=spec["seed"])
    stats, _, _ = model.evaluate(dataset=spec["dataset"], split="test")
    model.save(str(out / "saved"))
    return dict(flatten_ludwig(stats), artifact="saved")


def eval_ludwig(model_dir: Path, dataset: str, logf) -> dict:  # pragma: no cover
    from ludwig.api import LudwigModel
    model = LudwigModel.load(str(model_dir / "saved"))
    stats, _, _ = model.evaluate(dataset=dataset)
    return flatten_ludwig(stats)


def flatten_ludwig(stats: dict) -> dict:
    out = {}
    for feat, vals in (stats or {}).items():
        if isinstance(vals, dict):
            for k, v in vals.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[f"{feat}.{k}"] = round(float(v), 6)
    return out


ADAPTERS = {"pytorch": (fit_torch, predict_torch), "keras": (fit_keras, predict_keras)}

if __name__ == "__main__":     # pragma: no cover
    sys.exit(main(sys.argv[1:]))
