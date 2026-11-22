"""Coverage for app/fsbrowse.py (native dialog + folder listing edge cases) and app/pricing.py."""
import json
import os
import sys
import types

import pytest

from app import fsbrowse, pricing
from app.pricing import Price


# ---------- fsbrowse.pick_folder ----------

class _FakeTk:
    instances = []

    def __init__(self):
        self.destroyed = False
        self.attrs = {}
        _FakeTk.instances.append(self)

    def withdraw(self):
        self.withdrawn = True

    def attributes(self, name, value):
        self.attrs[name] = value

    def update(self):
        self.updated = True

    def destroy(self):
        self.destroyed = True


def _install_fake_tk(monkeypatch, askdirectory):
    tk_mod = types.ModuleType("tkinter")
    fd_mod = types.ModuleType("tkinter.filedialog")
    tk_mod.Tk = _FakeTk
    tk_mod.filedialog = fd_mod
    fd_mod.askdirectory = askdirectory
    monkeypatch.setitem(sys.modules, "tkinter", tk_mod)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fd_mod)
    _FakeTk.instances.clear()


def test_pick_folder_returns_resolved_choice_and_uses_initial_dir(monkeypatch, tmp_path):
    seen = {}

    def ask(parent, initialdir, title, mustexist):
        seen.update(initialdir=initialdir, title=title, mustexist=mustexist)
        return str(tmp_path)

    _install_fake_tk(monkeypatch, ask)
    out = fsbrowse.pick_folder(initial=str(tmp_path), title="Pick it")
    assert out == str(tmp_path.resolve())
    assert seen == {"initialdir": str(tmp_path), "title": "Pick it", "mustexist": True}
    root = _FakeTk.instances[0]
    assert root.destroyed is True and root.attrs == {"-topmost": True}


def test_pick_folder_falls_back_to_home_when_initial_missing(monkeypatch, tmp_path):
    seen = {}

    def ask(parent, initialdir, title, mustexist):
        seen["initialdir"] = initialdir
        return None

    _install_fake_tk(monkeypatch, ask)
    assert fsbrowse.pick_folder(initial=str(tmp_path / "missing")) is None
    assert seen["initialdir"] == str(fsbrowse.Path.home())
    assert _FakeTk.instances[0].destroyed is True


def test_pick_folder_cancel_returns_none(monkeypatch):
    _install_fake_tk(monkeypatch, lambda **kw: "")
    assert fsbrowse.pick_folder() is None


def test_pick_folder_no_gui_toolkit_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "tkinter", None)          # import -> ImportError
    with pytest.raises(RuntimeError, match="no GUI toolkit"):
        fsbrowse.pick_folder()


def test_pick_folder_dialog_failure_raises(monkeypatch):
    class Broken:
        def __init__(self):
            raise OSError("no display")

    tk_mod = types.ModuleType("tkinter")
    tk_mod.Tk = Broken
    tk_mod.filedialog = types.ModuleType("tkinter.filedialog")
    monkeypatch.setitem(sys.modules, "tkinter", tk_mod)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", tk_mod.filedialog)
    with pytest.raises(RuntimeError, match="could not open the folder dialog"):
        fsbrowse.pick_folder()


def test_pick_folder_refuses_when_dialog_already_open(monkeypatch):
    assert fsbrowse._dialog_lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="already open"):
            fsbrowse.pick_folder()
    finally:
        fsbrowse._dialog_lock.release()
    # lock released afterwards: a subsequent call is not blocked by the guard
    _install_fake_tk(monkeypatch, lambda **kw: "")
    assert fsbrowse.pick_folder() is None


# ---------- fsbrowse.roots / list_dirs ----------

def test_roots_windows_lists_existing_drives(monkeypatch):
    monkeypatch.setattr(fsbrowse.os, "name", "nt")
    present = {"C:\\", "H:\\"}
    monkeypatch.setattr(fsbrowse.os.path, "exists", lambda p: p in present)
    assert fsbrowse.roots() == [dict(name="C:", path="C:\\"), dict(name="H:", path="H:\\")]


def test_roots_posix_is_filesystem_root(monkeypatch):
    monkeypatch.setattr(fsbrowse.os, "name", "posix")
    assert fsbrowse.roots() == [dict(name="/", path="/")]


def test_list_dirs_without_path_returns_home_and_roots(monkeypatch):
    monkeypatch.setattr(fsbrowse, "roots", lambda: [dict(name="Z:", path="Z:\\")])
    r = fsbrowse.list_dirs("")
    assert r["path"] is None and r["parent"] is None
    assert r["dirs"] == [dict(name="Home", path=r["home"]), dict(name="Z:", path="Z:\\")]


def test_list_dirs_filesystem_root_has_no_parent(tmp_path):
    anchor = os.path.abspath(os.sep)
    if os.name == "nt":
        anchor = os.path.splitdrive(str(tmp_path))[0] + "\\"
    r = fsbrowse.list_dirs(anchor)
    assert r["parent"] is None
    assert r["path"] == str(fsbrowse.Path(anchor).resolve())


def test_list_dirs_skips_files_hidden_and_special_entries(tmp_path):
    (tmp_path / "beta").mkdir()
    (tmp_path / "Alpha").mkdir()
    (tmp_path / "$Recycle").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "System Volume Information").mkdir()
    (tmp_path / "note.txt").write_text("x")
    r = fsbrowse.list_dirs(str(tmp_path))
    names = [d["name"] for d in r["dirs"]]
    assert names == ["Alpha", "beta"]                     # case-insensitive sort, no files/hidden/system
    assert r["parent"] == str(tmp_path.parent)


def test_list_dirs_ignores_entries_raising_oserror(monkeypatch, tmp_path):
    (tmp_path / "ok").mkdir()

    class Flaky:
        name = "flaky"
        path = str(tmp_path / "flaky")

        def is_dir(self, follow_symlinks=True):
            raise OSError("broken link")

    real = os.scandir(tmp_path)
    entries = list(real)
    real.close()
    monkeypatch.setattr(fsbrowse.os, "scandir", lambda p: entries + [Flaky()])
    r = fsbrowse.list_dirs(str(tmp_path))
    assert [d["name"] for d in r["dirs"]] == ["ok"]


def test_list_dirs_permission_error_becomes_value_error(monkeypatch, tmp_path):
    def deny(p):
        raise PermissionError("denied")

    monkeypatch.setattr(fsbrowse.os, "scandir", deny)
    with pytest.raises(ValueError, match="no permission"):
        fsbrowse.list_dirs(str(tmp_path))


def test_list_dirs_endpoint_maps_permission_to_422(client, monkeypatch, tmp_path):
    def deny(p):
        raise PermissionError("denied")

    monkeypatch.setattr(fsbrowse.os, "scandir", deny)
    assert client.get("/api/fs/dirs", params={"path": str(tmp_path)}).status_code == 422


# ---------- pricing ----------

def test_corrupt_overrides_file_is_ignored(tmp_path):
    f = pricing.data_dir() / "pricing_overrides.json"
    f.write_text("{not json", encoding="utf-8")
    try:
        assert pricing._overrides() == {}
    finally:
        f.unlink()


def test_override_beats_builtin_and_ollama_is_free():
    f = pricing.data_dir() / "pricing_overrides.json"
    f.write_text(json.dumps({"mock/mock-cheap": {"input_per_m": 3, "output_per_m": 4}}), encoding="utf-8")
    try:
        p = pricing.price_for("mock", "mock-cheap", "mock/mock-cheap")
        assert p == Price(3.0, 4.0, True, "override")
    finally:
        f.unlink()
    assert pricing.price_for("ollama", "llama3", "ollama/llama3") == Price(0.0, 0.0, True, "local")
    assert pricing.price_for("mock", "mock-expensive", "mock/mock-expensive").source == "builtin"


def test_cost_usd_is_per_million_tokens():
    p = Price(2.0, 10.0, True, "x")
    assert pricing.cost_usd(p, 500_000, 100_000) == pytest.approx(2.0)


def test_known_models_mock_returns_builtin_list():
    rows = pricing.known_models("mock")
    assert {"model": "mock-cheap", "input_per_m": 1.0, "output_per_m": 2.0} in rows


def test_known_models_unknown_kind_is_empty():
    assert pricing.known_models("no-such-kind") == []


def test_known_models_filters_and_sorts_litellm_map(monkeypatch):
    cost_map = {
        "gpt-4o": {"mode": "chat", "input_cost_per_token": 2e-6, "output_cost_per_token": 8e-6},
        "gpt-4o-embed": {"mode": "embedding", "input_cost_per_token": 1e-6, "output_cost_per_token": 0},
        "openai/gpt-x": {"mode": "chat", "input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6},
        "davinci": {"mode": "chat", "input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6},
        "gpt-free": {"mode": "chat", "input_cost_per_token": None, "output_cost_per_token": 1e-6},
        "o3-mini": {"mode": None, "input_cost_per_token": 1e-6, "output_cost_per_token": 4e-6},
        "claude-3": {"mode": "chat", "input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6},
        "mistral-7b": {"mode": "chat", "input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6},
        "xai/grok-2": {"mode": "chat", "input_cost_per_token": 2e-6, "output_cost_per_token": 10e-6},
        "xai/grok-broken": {"mode": "chat", "input_cost_per_token": 2e-6, "output_cost_per_token": None},
    }
    monkeypatch.setattr(pricing.litellm, "model_cost", cost_map)

    oa = pricing.known_models("openai")
    assert [r["model"] for r in oa] == ["gpt-4o", "o3-mini"]
    assert oa[0]["input_per_m"] == pytest.approx(2.0) and oa[0]["output_per_m"] == pytest.approx(8.0)

    an = pricing.known_models("anthropic")
    assert [r["model"] for r in an] == ["claude-3"]

    xai = pricing.known_models("xai")
    assert [r["model"] for r in xai] == ["grok-2"]          # prefix stripped, broken entry skipped


def test_known_models_truncates_to_300(monkeypatch):
    cost_map = {f"xai/m{i:03d}": {"mode": "chat", "input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6}
                for i in range(350)}
    monkeypatch.setattr(pricing.litellm, "model_cost", cost_map)
    rows = pricing.known_models("xai")
    assert len(rows) == 300 and rows[0]["model"] == "m000"


def test_list_dirs_rejects_relative_and_missing_paths(tmp_path):
    with pytest.raises(ValueError, match="not an existing absolute folder"):
        fsbrowse.list_dirs("relative/dir")
    with pytest.raises(ValueError, match="not an existing absolute folder"):
        fsbrowse.list_dirs(str(tmp_path / "nope"))


def test_price_for_uses_litellm_cost_map_with_bedrock_fallbacks(monkeypatch):
    cost_map = {
        "bedrock/anthropic.claude-x": {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6},
        "gpt-lite": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6},
        "half": {"input_cost_per_token": 1e-6, "output_cost_per_token": None},
    }
    monkeypatch.setattr(pricing.litellm, "model_cost", cost_map)
    # cross-region profile prefix is stripped and retried as bedrock/<base>
    p = pricing.price_for("bedrock", "us.anthropic.claude-x", "bedrock/us.anthropic.claude-x")
    assert p.known is True and p.source == "litellm"
    assert p.input_per_m == pytest.approx(3.0) and p.output_per_m == pytest.approx(15.0)
    # plain match via the bare model name
    assert pricing.price_for("openai", "gpt-lite", "gpt-lite").output_per_m == pytest.approx(2.0)
    # incomplete entry is not used; falls back to the conservative price and is flagged unknown
    p = pricing.price_for("openai", "half", "half")
    assert p == Price(pricing.FALLBACK_IN_PER_M, pricing.FALLBACK_OUT_PER_M, False, "fallback")
