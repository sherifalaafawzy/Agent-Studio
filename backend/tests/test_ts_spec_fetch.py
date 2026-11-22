"""The time-series catalogue (app/ts_spec.py) and the weight/source fetcher that runs inside the environment (app/ts_fetch.py).

Nothing is downloaded: the network and huggingface_hub are replaced by fakes.
"""
import io
import os
import sys
import types
import zipfile
from datetime import date

import pytest

from app import ts_fetch, ts_spec as T


def restore_env_after(monkeypatch, *names):
    for n in names:
        monkeypatch.setenv(n, "x")
        monkeypatch.delenv(n)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "ts"
    monkeypatch.setenv("TS_HOME", str(h))
    return h


def mark_ready(home, *ids, env=True):
    """Fabricate what a finished install leaves behind."""
    py = T.env_python(home)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")
    T.write_state({"env": {"state": "ready" if env else "installing", "torch": "cpu"},
                   "models": {i: {"state": "ready", "revision": T.MODELS[i].revision} for i in ids}}, home)


# ---------------------------------------------------------------- the catalogue

def test_catalogue_has_the_eight_verified_models():
    assert list(T.MODELS) == ["chronos-2", "timesfm-3", "toto-2-4m", "timestral-5m", "timestral-28m", "timestral-209m", "ci-proto", "kronos-base"]
    assert all(T.REVISION_RE.match(m.revision) for m in T.MODELS.values())
    assert T.model("chronos-2").repo == "amazon/chronos-2"
    with pytest.raises(KeyError, match="unknown model 'nope'"):
        T.model("nope")


def test_sizes_are_exact_sums_of_the_downloaded_files():
    m = T.MODELS["timestral-5m"]
    assert m.weights_bytes == 904 + 20_843_960 + 1_695
    assert m.download_bytes == m.weights_bytes + m.source.zip_bytes
    k = T.MODELS["kronos-base"]
    assert k.weights_bytes == 228 + 409_264_008 + 301 + 15_842_368                 # model + tokenizer
    assert k.repos == [("NeoQuasar/Kronos-base", k.revision, ["config.json", "model.safetensors"]),
                       ("NeoQuasar/Kronos-Tokenizer-base", k.extra.revision, ["config.json", "model.safetensors"])]
    assert T.MODELS["chronos-2"].repos == [("amazon/chronos-2", T.MODELS["chronos-2"].revision, ["config.json", "model.safetensors"])]
    assert T.MODELS["chronos-2"].download_bytes == T.MODELS["chronos-2"].weights_bytes


def test_kronos_is_the_only_candlestick_model_and_timesfm_is_non_commercial():
    assert [m.id for m in T.MODELS.values() if m.kind == "ohlcv"] == ["kronos-base"]
    assert T.MODELS["timesfm-3"].commercial == "no" and T.MODELS["timestral-209m"].commercial == "restricted"
    assert T.MODELS["chronos-2"].commercial == "yes"
    assert "Non-Commercial" in T.MODELS["timesfm-3"].license_name


def test_the_fourteen_day_rule_dates_and_cutoff():
    assert T.cutoff(date(2026, 11, 21)) == "2026-11-07T00:00:00Z"
    assert T.cutoff() == T.cutoff(date.today())
    assert T.available_from(T.MODELS["kronos-base"]) == "2026-04-27"              # source commit date + 14 days
    assert T.available_from(T.MODELS["ci-proto"]) == "2026-11-26"
    assert T.available_from(T.MODELS["chronos-2"]) == "2026-09-22"
    bare = T.Model(id="x", label="x", repo="a/b", revision="0" * 40, backend="b", files={}, params="", license_name="", license_url="",
                   license_summary=(), commercial="yes", good_for="", caveats="")
    assert T.available_from(bare) == ""


def test_ci_proto_is_blocked_until_its_package_is_old_enough_and_toto_needs_python_312():
    ci, toto = T.MODELS["ci-proto"], T.MODELS["toto-2-4m"]
    assert "from 2026-11-26" in T.blocked_reason(ci, date(2026, 11, 21), (3, 12))
    assert T.blocked_reason(ci, date(2026, 11, 26), (3, 12)) == ""
    assert T.blocked_reason(toto, date(2026, 11, 21), (3, 11)) == "needs Python 3.12+, the backend runs 3.11"
    assert T.blocked_reason(toto, date(2026, 11, 21), (3, 12)) == ""
    assert T.blocked_reason(T.MODELS["chronos-2"]) == ""                           # defaults: today, this interpreter


def test_public_view_hides_nothing_needed_and_exposes_licence_and_block():
    p = T.public(T.MODELS["ci-proto"], date(2026, 11, 21), (3, 12))
    assert p["blocked"] and p["available_from"] == "2026-11-26" and p["license"]["commercial"] == "yes" and p["skipped"].startswith("model-long.ckpt")
    k = T.public(T.MODELS["kronos-base"], date(2026, 11, 21), (3, 12))
    assert k["kind"] == "ohlcv" and k["source"]["repo"] == "shiyu-coder/Kronos" and k["quantile_levels"] == []
    assert T.public(T.MODELS["chronos-2"])["source"] is None


def test_paths_state_and_readiness(home):
    assert T.home() == home and T.hf_home() == home / "hf" and T.src_dir() == home / "src" and T.state_file() == home / "state.json"
    assert T.read_state() == {} and not T.env_ready() and not T.any_ready() and T.installed_ids() == []
    mark_ready(home, "chronos-2", "toto-2-4m")
    assert T.env_ready() and T.is_ready("chronos-2") and T.installed_ids() == ["chronos-2", "toto-2-4m"] and T.any_ready()
    assert not T.is_ready("timesfm-3") and not T.is_ready("nope")
    st = T.read_state()
    st["models"]["chronos-2"]["revision"] = "f" * 40                                # a different pinned revision is not "installed"
    T.write_state(st)
    assert not T.is_ready("chronos-2") and T.is_ready("toto-2-4m")
    mark_ready(home, "chronos-2", env=False)
    assert not T.is_ready("chronos-2")                                              # the environment itself is not ready
    T.env_python().unlink()
    mark_ready(home, "chronos-2")
    T.env_python().unlink()
    assert not T.env_ready() and not T.is_ready("chronos-2")                        # the environment's python vanished


def test_writing_state_retries_a_locked_file_but_gives_up_eventually(home, monkeypatch):
    real, tries = os.replace, []

    def flaky(src, dst):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("locked")
        real(src, dst)
    monkeypatch.setattr(T.os, "replace", flaky)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    T.write_state({"a": 1}, home)
    assert len(tries) == 3 and T.read_state(home)["a"] == 1
    monkeypatch.setattr(T.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError("still locked")))
    with pytest.raises(PermissionError, match="still locked"):
        T.write_state({"a": 2}, home)
    assert T.read_state(home)["a"] == 1


def test_state_file_garbage_reads_as_empty(home):
    home.mkdir(parents=True)
    T.state_file().write_text("{not json", encoding="utf-8")
    assert T.read_state() == {}
    T.state_file().write_text("[1, 2]", encoding="utf-8")
    assert T.read_state() == {}


def test_env_python_is_platform_shaped(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert T.env_python().as_posix().endswith("venv/bin/python")
    monkeypatch.setattr(sys, "platform", "win32")
    assert T.env_python().as_posix().endswith("venv/Scripts/python.exe")


def test_home_defaults_under_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("TS_HOME", raising=False)
    monkeypatch.setenv("AGENT_STUDIO_HOME", str(tmp_path))
    assert T.home() == tmp_path / "envs" / "timeseries"


def test_torch_choice_drops_torchvision_and_picks_the_build():
    assert T.choose_torch((13, 3), "win32")["variant"] == "cuda" and T.choose_torch((13, 3), "win32")["packages"] == ["torch==2.15.0"]
    assert T.choose_torch((12, 4), "linux")["variant"] == "cpu" and T.choose_torch(None, "win32")["index"].endswith("/cpu")
    assert T.choose_torch((13, 3), "darwin")["variant"] == "default" and T.choose_torch(None, "darwin")["index"] is None
    assert T.model_dir_name("amazon/chronos-2") == "models--amazon--chronos-2"


# ---------------------------------------------------------------- safe archive extraction

def make_zip(files: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_safe_extract_keeps_only_the_wanted_folder(tmp_path):
    z = make_zip({"top/src/pkg/a.py": b"A", "top/src/pkg/sub/b.py": b"B", "top/README.md": b"r", "top/src/": b""})
    assert ts_fetch.safe_extract(z, "top/src/", tmp_path / "out") == 2
    assert (tmp_path / "out" / "pkg" / "a.py").read_bytes() == b"A" and (tmp_path / "out" / "pkg" / "sub" / "b.py").read_bytes() == b"B"
    assert not (tmp_path / "out" / "README.md").exists()


def test_safe_extract_refuses_paths_that_leave_the_folder(tmp_path):
    z = make_zip({"top/src/../../evil.py": b"x"})
    with pytest.raises(ValueError, match="unsafe path"):
        ts_fetch.safe_extract(z, "top/src/", tmp_path / "out")
    assert not (tmp_path / "evil.py").exists()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def archive_bytes(sha: str, keep: str, extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"repo-{sha}/{keep}/pkg/__init__.py", "X = 1")
        z.writestr(f"repo-{sha}/README.md", "r")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


def test_fetch_source_downloads_the_pinned_commit_and_keeps_the_package(home, monkeypatch):
    src = T.MODELS["timestral-5m"].source
    seen = []
    monkeypatch.setattr(ts_fetch.urllib.request, "urlopen", lambda url, timeout: (seen.append(url), FakeResponse(archive_bytes(src.sha, "src")))[1])
    (home / "src" / "timestral").mkdir(parents=True)
    (home / "src" / "timestral" / "stale.txt").write_text("old")
    ticks = []
    dest = ts_fetch.fetch_source(src, home, ticks.append)
    assert seen == [f"https://github.com/mistralai/timestral/archive/{src.sha}.zip"]
    assert dest == home / "src" / "timestral" and (dest / "src" / "pkg" / "__init__.py").read_text() == "X = 1"
    assert not (dest / "stale.txt").exists() and ticks and ticks[-1] > 0
    ts_fetch.fetch_source(src, home)                                                # no progress callback, nothing stale: still fine


def test_fetch_source_rejects_a_wrong_commit_and_a_missing_folder(home, monkeypatch):
    src = T.MODELS["kronos-base"].source
    monkeypatch.setattr(ts_fetch.urllib.request, "urlopen", lambda url, timeout: FakeResponse(archive_bytes("0" * 40, "model")))
    with pytest.raises(ValueError, match="not commit"):
        ts_fetch.fetch_source(src, home)
    monkeypatch.setattr(ts_fetch.urllib.request, "urlopen", lambda url, timeout: FakeResponse(archive_bytes(src.sha, "other")))
    with pytest.raises(ValueError, match="no 'model/' folder"):
        ts_fetch.fetch_source(src, home)


def test_dir_bytes_counts_files_skips_links_and_survives_errors(tmp_path, monkeypatch):
    (tmp_path / "a").write_bytes(b"1234")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"12")
    (tmp_path / "link").write_bytes(b"123456789")
    (tmp_path / "gone").write_bytes(b"1")
    real_islink, real_size = os.path.islink, os.path.getsize
    monkeypatch.setattr(os.path, "islink", lambda p: p.endswith("link") or real_islink(p))

    def size(p):
        if p.endswith("gone"):
            raise OSError("vanished")
        return real_size(p)
    monkeypatch.setattr(os.path, "getsize", size)
    assert ts_fetch.dir_bytes(tmp_path) == 6


# ---------------------------------------------------------------- the fetch command

def fake_hub(monkeypatch, calls, home, fail=None):
    hub = types.ModuleType("huggingface_hub")

    def snapshot_download(**kw):
        calls.append(kw)
        if fail:
            raise fail
        d = home / "hf" / "hub" / T.model_dir_name(kw["repo_id"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "blob").write_bytes(b"x" * 100)
        import time
        time.sleep(0.12)                                                            # let the progress reporter tick
        return str(d / "snap")
    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)


def test_fetch_downloads_every_repo_at_its_pinned_revision(home, monkeypatch, capsys):
    restore_env_after(monkeypatch, "HF_HOME")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(ts_fetch, "POLL_SECONDS", 0.01)
    calls = []
    fake_hub(monkeypatch, calls, home)
    k = T.MODELS["kronos-base"]
    monkeypatch.setattr(ts_fetch.urllib.request, "urlopen", lambda url, timeout: FakeResponse(archive_bytes(k.source.sha, "model")))
    assert ts_fetch.main(["kronos-base"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [c["repo_id"] for c in calls] == ["NeoQuasar/Kronos-base", "NeoQuasar/Kronos-Tokenizer-base"]
    assert [c["revision"] for c in calls] == [k.revision, k.extra.revision]
    assert calls[0]["allow_patterns"] == ["config.json", "model.safetensors"] and calls[0]["cache_dir"] == str(home / "hf" / "hub")
    assert out[-1].startswith("DONE") and f"PROGRESS {k.download_bytes} {k.download_bytes}" in out
    assert any(ln.startswith("PROGRESS ") and ln != f"PROGRESS {k.download_bytes} {k.download_bytes}" for ln in out)
    assert os.environ["HF_HOME"] == str(home / "hf") and "HF_HUB_OFFLINE" not in os.environ
    assert (home / "src" / "kronos" / "model" / "pkg" / "__init__.py").is_file()


def test_fetch_of_a_model_without_source_skips_the_github_step(home, monkeypatch, capsys):
    restore_env_after(monkeypatch, "HF_HOME")
    monkeypatch.setattr(ts_fetch, "POLL_SECONDS", 0.01)
    calls = []
    fake_hub(monkeypatch, calls, home)
    monkeypatch.setattr(ts_fetch.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no network call expected"))
    assert ts_fetch.main(["chronos-2"]) == 0
    assert len(calls) == 1 and not (home / "src").exists()


def test_fetch_reports_errors_and_bad_arguments(home, monkeypatch, capsys):
    restore_env_after(monkeypatch, "HF_HOME")
    monkeypatch.setattr(ts_fetch, "POLL_SECONDS", 0.01)
    fake_hub(monkeypatch, [], home, fail=OSError("disk full " + "x" * 400))
    assert ts_fetch.main(["chronos-2"]) == 1
    err = capsys.readouterr().out.strip().splitlines()[-1]
    assert err.startswith("ERROR OSError: disk full") and len(err) < 330
    assert ts_fetch.main([]) == 2 and "usage" in capsys.readouterr().out
    assert ts_fetch.main(["nope"]) == 2 and "unknown model 'nope'" in capsys.readouterr().out


def test_fetch_reads_argv_when_not_given_and_runs_as_a_module(home, monkeypatch, capsys):
    import runpy
    restore_env_after(monkeypatch, "HF_HOME")
    monkeypatch.setattr(sys, "argv", ["ts_fetch", "nope"])
    assert ts_fetch.main() == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        runpy.run_module("app.ts_fetch", run_name="__main__")
    assert e.value.code == 2
