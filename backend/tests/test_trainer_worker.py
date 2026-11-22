"""The training worker (app/trainer_worker.py): data preparation, metrics, the job flow, with fake framework adapters
(no ML framework is installed in the test environment; the three real fit/predict pairs are the untested part)."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest

from app import trainer_worker as W


def csv_file(tmp_path: Path, name: str = "d.csv", rows: int = 40) -> Path:
    lines = ["age,plan,income,churned"]
    for i in range(rows):
        lines.append(f"{20 + i % 30},{'a' if i % 3 else 'b'},{'' if i % 7 == 0 else 1000 + i * 10},{i % 2}")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ------------------------------------------------------------------------------------------------------------ preparation
def test_read_csv_and_errors(tmp_path):
    h, rows = W.read_csv(str(csv_file(tmp_path)))
    assert h == ["age", "plan", "income", "churned"] and len(rows) == 40
    (tmp_path / "ragged.csv").write_text("a,b\n1,2\n3\n4,5\n", encoding="utf-8-sig")
    assert W.read_csv(str(tmp_path / "ragged.csv"))[1] == [["1", "2"], ["4", "5"]]
    (tmp_path / "empty.csv").write_text("a,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no usable rows"):
        W.read_csv(str(tmp_path / "empty.csv"))


def test_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "MAX_ROWS", 3)
    assert len(W.read_csv(str(csv_file(tmp_path)))[1]) == 3


def test_build_prep_learns_encoders():
    header = ["n", "c", "y"]
    rows = [["1", "x", "a"], ["3", "y", "b"], ["", "x", "a"], ["2", "", "b"]]
    prep = W.build_prep(header, rows, "y", "classification")
    num, cat = prep["features"]
    assert num["kind"] == "num" and num["mean"] == 2.0 and round(num["std"], 4) == 0.8165
    assert cat["kind"] == "cat" and cat["levels"][0] == "x" and set(cat["levels"]) == {"x", "y", "missing"}
    assert prep["classes"] == ["a", "b"] and W.feature_names(prep) == ["n", "c=x", "c=missing", "c=y", "c=other"]
    assert W.encode(prep, ["", "", "a"])[0] == 0.0                                   # missing number -> the mean -> 0 after standardising
    assert W.encode(prep, ["2", "zzz", "a"])[-1] == 1.0                              # unseen level -> 'other'
    flat = W.build_prep(["c", "y"], [["same", "1"], ["same", "2"]], "y", "regression")
    assert flat["features"][0]["kind"] == "cat" and "classes" not in flat
    const = W.build_prep(["n", "y"], [["5", "1"], ["5", "2"]], "y", "regression")
    assert const["features"][0]["std"] == 1.0                                        # constant column: no division by zero


def test_classification_target_checks():
    with pytest.raises(ValueError, match="at least two different values"):
        W.build_prep(["a", "y"], [["1", "x"], ["2", "x"]], "y", "classification")
    many = [[str(i), f"c{i}"] for i in range(101)]
    with pytest.raises(ValueError, match="looks like a regression target"):
        W.build_prep(["a", "y"], many, "y", "classification")


def test_transform_aligns_columns_and_skips_unusable_targets():
    header = ["x", "y"]
    rows = [["1", "a"], ["2", "b"], ["3", ""], ["4", "zzz"]]
    prep = W.build_prep(header, rows[:2], "y", "classification")
    X, y = W.transform(prep, ["y", "x"], [["a", "1"], ["b", "2"], ["", "3"], ["zzz", "4"]], True)          # columns swapped in the file
    assert y == [0, 1] and len(X) == 2
    X2, y2 = W.transform(prep, ["x"], [["1"]], False)                                                          # no target column at all
    assert len(X2) == 1 and y2 == []
    reg = W.build_prep(["x", "y"], [["1", "1.5"], ["2", "2.5"]], "y", "regression")
    Xr, yr = W.transform(reg, ["x", "y"], [["1", "1.5"], ["2", "n/a"], ["3", ""]], True)
    assert yr == [1.5] and len(Xr) == 1


def test_split_is_deterministic_and_never_empty():
    a, b = W.split_indices(10, 0.2, 7), W.split_indices(10, 0.2, 7)
    assert a == b and len(a[1]) == 2 and sorted(a[0] + a[1]) == list(range(10))
    assert len(W.split_indices(10, 0.001, 1)[1]) == 1
    assert len(W.split_indices(10, 0.999, 1)[0]) >= 1
    assert W.split_indices(1, 0.2, 1) == ([0], [])


def test_metrics_by_hand():
    m = W.classification_metrics([0, 0, 1, 1], [0, 1, 1, 1], ["a", "b"])
    assert m["accuracy"] == 0.75 and m["n"] == 4 and m["majority_class_baseline"] == 0.5
    assert m["per_class"]["a"] == dict(precision=1.0, recall=0.5, f1=0.6667, support=2)
    assert m["macro_f1"] == 0.7333
    empty = W.classification_metrics([0, 0], [1, 1], ["a", "b"])
    assert empty["accuracy"] == 0.0 and empty["per_class"]["b"]["support"] == 0 and empty["per_class"]["a"]["precision"] == 0.0
    r = W.regression_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 5.0])
    assert r["mae"] == round(2 / 3, 6) and math.isclose(r["rmse"], math.sqrt(4 / 3), abs_tol=1e-5) and r["r2"] == round(1 - (4 / 3) / (2 / 3), 4)
    assert r["mean_baseline_mae"] == round((1 + 0 + 1) / 3, 6)
    assert W.regression_metrics([2.0, 2.0], [1.0, 3.0])["r2"] == 0.0                 # constant target: no variance to explain


def test_flatten_ludwig_keeps_only_numbers():
    assert W.flatten_ludwig({"y": {"accuracy": 0.9, "loss": 1, "name": "x", "flag": True}, "combined": 3, "z": None}) == {"y.accuracy": 0.9, "y.loss": 1.0}
    assert W.flatten_ludwig(None) == {}


def test_ludwig_config_uses_the_users_or_builds_one():
    prep = W.build_prep(["n", "c", "y"], [["1", "x", "a"], ["2", "y", "b"]], "y", "classification")
    spec = dict(task="classification", target="y", epochs=3, learning_rate=0.01, batch_size=8)
    cfg = W.ludwig_config(spec, prep)
    assert cfg["input_features"] == [{"name": "n", "type": "number"}, {"name": "c", "type": "category"}]
    assert cfg["output_features"] == [{"name": "y", "type": "category"}] and cfg["trainer"] == {"epochs": 3, "learning_rate": 0.01, "batch_size": 8}
    assert W.ludwig_config(dict(spec, task="regression"), prep)["output_features"][0]["type"] == "number"
    mine = {"input_features": [], "output_features": []}
    assert W.ludwig_config(dict(spec, config=mine), prep) is mine


# -------------------------------------------------------------------------------------------------------------- job flow
class FakeAdapter:
    """fit reports two epochs, predict returns class 0 / the number 0."""

    def __init__(self):
        self.progress_results = []
        self.fit_args = None

    def fit(self, Xtr, ytr, Xva, yva, spec, prep, out, progress):
        self.fit_args = (len(Xtr), len(Xva), spec["epochs"])
        self.progress_results = [progress(1, 2, 0.5, 0.4), progress(2, 2, 0.4, None)]
        (out / "model.fake").write_text("x", encoding="utf-8")
        return "model.fake"

    def predict(self, out, X, prep):
        return [0] * len(X) if prep["task"] == "classification" else [0.0] * len(X)


@pytest.fixture
def fake(monkeypatch):
    a = FakeAdapter()
    monkeypatch.setitem(W.ADAPTERS, "pytorch", (a.fit, a.predict))
    return a


def make_job(tmp_path, **kw) -> Path:
    d = tmp_path / "jobs" / "abcdef0123"
    d.mkdir(parents=True)
    spec = dict(id="abcdef0123", mode="train", framework="pytorch", task="classification", dataset=str(csv_file(tmp_path)), target="churned", epochs=2,
                max_minutes=5, hidden=[8], learning_rate=0.01, batch_size=8, test_size=0.25, seed=1, config=None)
    spec.update(kw)
    (d / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (d / "status.json").write_text(json.dumps({"state": "starting"}), encoding="utf-8")
    return d


def status(d: Path) -> dict:
    return json.loads((d / "status.json").read_text())


def test_training_job_end_to_end(tmp_path, fake, capsys):
    d = make_job(tmp_path)
    assert W.main([str(d)]) == 0
    st, m = status(d), json.loads((d / "metrics.json").read_text())
    assert st["state"] == "done" and st["progress"] == 1.0 and st["artifact"] == "model.fake" and "worker_pid" in st
    assert m["framework"] == "pytorch" and m["accuracy"] > 0 and m["train_rows"] == 30 and m["features"] > 0 and "majority_class_baseline" in m and m["artifact"] == "model.fake"
    assert fake.fit_args == (30, 10, 2) and fake.progress_results == [True, True]
    assert (d / "prep.json").exists() and (d / "model" / "model.fake").exists()
    out = capsys.readouterr().out
    assert "epoch 1/2 loss=0.50000 val=0.40000" in out and "epoch 2/2 loss=0.40000" in out and "done:" in out and "40 rows" in out


def test_regression_job(tmp_path, fake):
    d = make_job(tmp_path, task="regression", target="age")
    assert W.main([str(d)]) == 0
    m = json.loads((d / "metrics.json").read_text())
    assert "mae" in m and "rmse" in m and "mean_baseline_mae" in m


def test_progress_stops_the_job_at_the_time_limit_and_when_cancelled(tmp_path, fake):
    d = make_job(tmp_path, max_minutes=0)
    W.main([str(d)])
    assert fake.progress_results == [False, False]
    d2 = tmp_path / "j2"
    d2.mkdir()
    (d2 / "spec.json").write_text((tmp_path / "jobs" / "abcdef0123" / "spec.json").read_text(), encoding="utf-8")
    s = json.loads((d2 / "spec.json").read_text())
    s["max_minutes"] = 5
    (d2 / "spec.json").write_text(json.dumps(s), encoding="utf-8")
    (d2 / "status.json").write_text(json.dumps({"state": "cancelled"}), encoding="utf-8")
    W.run_train(d2, s)
    assert fake.progress_results == [False, False]                                     # cancelled: asked to stop


def test_a_cancelled_job_stays_cancelled(tmp_path, fake):
    d = make_job(tmp_path)
    (d / "status.json").write_text(json.dumps({"state": "cancelled", "error": "cancelled by the user"}), encoding="utf-8")
    W.main([str(d)])
    assert status(d)["state"] == "cancelled"


def test_failures_become_a_failed_status_with_the_reason(tmp_path, fake, capsys):
    d = make_job(tmp_path, target="nope")
    assert W.main([str(d)]) == 1
    assert status(d)["state"] == "failed" and "target column 'nope' not found" in status(d)["error"]
    assert "FAILED: ValueError" in capsys.readouterr().out
    d2 = make_job(tmp_path.joinpath("b"), dataset=str(csv_file(tmp_path, "tiny.csv", rows=6)))
    assert W.main([str(d2)]) == 1 and "only" in status(d2)["error"] and "need at least 10" in status(d2)["error"]


def test_ludwig_job_uses_its_own_adapter(tmp_path, monkeypatch):
    seen = {}

    def fit(spec, config, out, logf):
        seen.update(config=config, out=out)
        return {"y.accuracy": 0.8, "artifact": "saved"}
    monkeypatch.setattr(W, "fit_ludwig", fit)
    d = make_job(tmp_path, framework="ludwig")
    assert W.main([str(d)]) == 0
    m = json.loads((d / "metrics.json").read_text())
    assert m == {"framework": "ludwig", "y.accuracy": 0.8, "artifact": "saved"} and seen["config"]["output_features"][0]["name"] == "churned"
    assert status(d)["artifact"] == "saved"


def test_evaluate_job_scores_the_trained_model_on_a_new_file(tmp_path, fake):
    train = make_job(tmp_path)
    W.main([str(train)])
    ev = tmp_path / "jobs" / "eeeeeeeeee"
    ev.mkdir()
    spec = dict(mode="evaluate", framework="pytorch", task="classification", target="churned", model_job="abcdef0123", dataset=str(csv_file(tmp_path, "new.csv", 20)), max_minutes=5)
    (ev / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (ev / "status.json").write_text("{}", encoding="utf-8")
    assert W.main([str(ev)]) == 0
    m = json.loads((ev / "metrics.json").read_text())
    assert m["evaluated_model"] == "abcdef0123" and m["n"] == 20 and m["accuracy"] == 0.5


def test_evaluate_with_no_usable_rows_fails(tmp_path, fake):
    train = make_job(tmp_path)
    W.main([str(train)])
    bad = tmp_path / "noy.csv"
    bad.write_text("age,plan,income,churned\n30,a,1000,maybe\n", encoding="utf-8")
    ev = tmp_path / "jobs" / "eeeeeeeeee"
    ev.mkdir()
    (ev / "spec.json").write_text(json.dumps(dict(mode="evaluate", framework="pytorch", model_job="abcdef0123", dataset=str(bad))), encoding="utf-8")
    assert W.main([str(ev)]) == 1 and "no rows in the evaluation file" in status(ev)["error"]


def test_evaluate_ludwig(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "eval_ludwig", lambda model_dir, dataset, logf: {"y.accuracy": 0.7})
    ev = tmp_path / "jobs" / "eeeeeeeeee"
    ev.mkdir(parents=True)
    (ev / "spec.json").write_text(json.dumps(dict(mode="evaluate", framework="ludwig", model_job="abcdef0123", dataset="x.csv")), encoding="utf-8")
    assert W.main([str(ev)]) == 0
    assert json.loads((ev / "metrics.json").read_text()) == {"framework": "ludwig", "y.accuracy": 0.7}


def test_set_status_and_json_helpers(tmp_path):
    d = tmp_path
    assert W.read_json(d / "none.json") == {}
    (d / "list.json").write_text("[1]", encoding="utf-8")
    assert W.read_json(d / "list.json") == {}
    (d / "bad.json").write_text("{", encoding="utf-8")
    assert W.read_json(d / "bad.json") == {}
    W.set_status(d, state="running", epoch=1)
    assert json.loads((d / "status.json").read_text())["epoch"] == 1
    W.set_status(d, state="cancelled")
    W.set_status(d, state="running")                       # refused after cancel
    W.set_status(d, extra="ok")                            # no state change is allowed
    s = json.loads((d / "status.json").read_text())
    assert s["state"] == "cancelled" and s["extra"] == "ok" and time.time() - s["updated"] < 60


def test_the_real_adapters_are_registered_for_pytorch_and_keras():
    assert set(W.ADAPTERS) == {"pytorch", "keras"}
    assert all(callable(f) for pair in W.ADAPTERS.values() for f in pair)
