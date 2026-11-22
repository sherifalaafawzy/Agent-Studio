"""The artifact store: configuration, finding what a turn produced, copying to several storages (with the secret scan), searching, previews, retention."""
from __future__ import annotations

import pathlib
import time

import pytest

from app.artifacts import service as SV
from app.artifacts.service import ArtifactError, ArtifactService, ignored, scan_text
from app.artifacts.storage import ArtifactStorage, StorageError, build_storage
from app.db import Database

SECRET = "sk-proj-SECRETKEY1234567890abcdefXYZ"


class MemStorage(ArtifactStorage):
    kind, cloud = "s3", True

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail: str | None = None

    def put(self, key, data, mime):
        if self.fail:
            raise StorageError(self.fail)
        self.objects[key] = data

    def get(self, key):
        if self.fail:
            raise StorageError(self.fail)
        try:
            return self.objects[key]
        except KeyError:
            raise StorageError("not there")

    def delete(self, key):
        if self.fail:
            raise StorageError(self.fail)
        self.objects.pop(key, None)

    def health(self):
        return {"ok": not self.fail, "detail": self.fail or "fine"}

    def where(self):
        return "mem://bucket"


@pytest.fixture(autouse=True)
def _unix_newlines(monkeypatch):
    real = pathlib.Path.write_text
    monkeypatch.setattr(pathlib.Path, "write_text", lambda self, data, encoding=None, errors=None, newline=None: real(self, data, encoding=encoding, errors=errors, newline=chr(10)))


@pytest.fixture
def mems():
    return {}


@pytest.fixture
def svc(tmp_path, mems):
    def factory(kind, cfg, secrets, home):
        if kind == "s3":
            if not cfg.get("bucket"):
                raise StorageError("the bucket name looks wrong")
            return mems.setdefault(cfg["bucket"], MemStorage())
        return build_storage(kind, cfg, secrets, home)
    return ArtifactService(Database(tmp_path / "t.db"), home=tmp_path / "home", start_worker=False, storage_factory=factory)


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    return d


def cloud(svc, **extra):
    """Switch a (fake) S3 storage on and make it the home of documents."""
    svc.set_config({"storages": [{"id": "local", "kind": "local"}, {"id": "cloud", "kind": "s3", "config": {"bucket": "b1"}}],
                    "policy": [{"types": ["document", "data"], "storages": ["local", "cloud"]}], **extra})


def turn(svc, ws, make, **kw):
    before = svc.snapshot(ws)
    make()
    return svc.capture_changes(before, ws, agent="engineer", session_id="s1", trace_id="t1", **kw)


# ------------------------------------------------------------------------------------------------ helpers
def test_ignore_patterns_and_the_scan():
    pats = SV.DEFAULT_IGNORE
    assert ignored("node_modules/x/y.js", pats) and ignored("src/.git/config", pats) and ignored("a/b/c.pyc", pats) and ignored("poetry.lock", pats) and ignored("AGENTS.md", pats)
    assert not ignored("report.md", pats) and not ignored("src/app.py", pats) and ignored("build", ["build"]) and ignored("docs/tmp/x.txt", ["docs/tmp/*"])
    assert scan_text("key " + SECRET + " end") == ("key [secret removed] end", True) and scan_text("nothing here") == ("nothing here", False)


# ------------------------------------------------------------------------------------------------ settings
def test_defaults_and_public_view(svc):
    cfg = svc.config()
    assert cfg["enabled"] and cfg["capture"] == "new" and cfg["secret_scan"] == "redact" and [s["id"] for s in cfg["storages"]] == ["local"] and cfg["default_storages"] == ["local"]
    pub = svc.public_config()
    assert pub["storages"][0]["where"] == str(svc.home / "artifacts") and not pub["storages"][0]["cloud"] and {k["id"] for k in pub["kinds"]} >= {"local", "s3", "gcs", "azure_blob", "webdav", "s3_compatible"}
    svc.db.set_setting(SV.CONFIG_KEY, "{not json")
    assert svc.config()["capture"] == "new"
    svc.db.set_setting(SV.CONFIG_KEY, '{"storages": [{"id": "x", "kind": "s3", "enabled": true, "config": {}}], "capture": "off"}')
    assert [s["id"] for s in svc.config()["storages"]] == ["local", "x"] and svc.config()["capture"] == "off"
    assert "not usable" in next(s for s in svc.public_config()["storages"] if s["id"] == "x")["where"]


def test_set_config_validates_every_field(svc):
    for patch, msg in (({"enabled": "yes"}, "enabled must be"), ({"capture": "always"}, "capture must be"), ({"secret_scan": "maybe"}, "secret_scan must be"),
                       ({"max_file_mb": 0}, "largest file size"), ({"max_file_mb": True}, "largest file size"), ({"max_per_turn": 501}, "files collected per turn"),
                       ({"retention_days": -1}, "retention"), ({"ignore": "x"}, "ignore must be"), ({"ignore": ["x" * 121]}, "ignore must be"),
                       ({"storages": "x"}, "list of at most 10"), ({"storages": [{"id": "Bad Id", "kind": "s3"}]}, "needs an id"),
                       ({"storages": [{"id": "a", "kind": "s3", "config": {"bucket": "b"}}, {"id": "a", "kind": "s3", "config": {"bucket": "b"}}]}, "two storages are called"), ({"storages": [{"id": "a", "kind": "ftp"}]}, "kind must be"),
                       ({"storages": [{"id": "local", "kind": "s3"}]}, "folder on this PC"), ({"storages": [{"id": "a", "kind": "s3", "config": {}}]}, "storage 'a': the bucket name looks wrong"),
                       ({"default_storages": []}, "default storages must name"), ({"default_storages": ["nope"]}, "default storages must name"), ({"policy": "x"}, "list of at most 20"),
                       ({"policy": [{"types": ["bogus"], "storages": ["local"]}]}, "each rule needs types"), ({"policy": [{"types": [], "storages": ["local"]}]}, "each rule needs types"),
                       ({"policy": [{"types": ["image"], "storages": ["nope"]}]}, "rule's storages"), ({"policy": [{"types": ["image"], "storages": ["local"], "max_mb": 0.5}]}, "largest file size"),
                       ({"secrets": "x"}, "secrets must look like"), ({"secrets": {"a": {"k": 1}}}, "secrets must look like")):
        with pytest.raises(ArtifactError, match=msg):
            svc.set_config(patch)
    assert svc.set_config({"unknown": 1, "enabled": None})["enabled"] is True


def test_set_config_roundtrip_with_write_only_secrets(svc):
    out = svc.set_config({"capture": "new_and_changed", "max_file_mb": 5, "max_per_turn": 7, "retention_days": 30, "secret_scan": "block", "ignore": ["*.log"], "enabled": True,
                          "storages": [{"id": "cloud", "kind": "s3", "label": "Team bucket", "config": {"bucket": "b1", "ignored_field": "x", "obj": {"a": 1}}}],
                          "policy": [{"types": ["image", "image", "data"], "max_mb": 10, "storages": ["cloud", "cloud"]}], "default_storages": ["local", "cloud"],
                          "secrets": {"cloud": {"access_key_id": "AKIA1", "secret_access_key": "S"}, "ghost": {"x": "y"}}})
    assert [s["id"] for s in out["storages"]] == ["local", "cloud"] and out["storages"][1]["label"] == "Team bucket" and out["storages"][1]["config"] == {"bucket": "b1"}
    assert out["storages"][1]["secrets_set"] == ["access_key_id", "secret_access_key"] and "AKIA1" not in str(out) and out["policy"] == [{"types": ["image", "data"], "max_mb": 10, "storages": ["cloud"]}]
    assert out["capture"] == "new_and_changed" and out["max_per_turn"] == 7 and out["default_storages"] == ["local", "cloud"]
    out = svc.set_config({"secrets": {"cloud": {"access_key_id": "", "session_token": "TOK"}}})               # empty value clears one, others stay
    assert out["storages"][1]["secrets_set"] == ["secret_access_key", "session_token"]
    out = svc.set_config({"storages": [{"id": "local", "kind": "local", "enabled": False}]})                    # the folder can never be switched off; a removed storage takes its secrets
    assert [s["id"] for s in out["storages"]] == ["local"] and out["storages"][0]["enabled"] and svc._secrets() == {}
    assert out["policy"] == [] and out["default_storages"] == ["local"]
    svc.db.set_setting(SV.SECRETS_KEY, "[1]")
    assert svc._secrets() == {}
    svc.db.set_setting(SV.SECRETS_KEY, "{bad")
    assert svc._secrets() == {}


def test_a_removed_storage_leaves_the_rules_clean(svc):
    cloud(svc)
    out = svc.set_config({"storages": [{"id": "local", "kind": "local"}]})
    assert out["policy"] == [{"types": ["document", "data"], "max_mb": 0, "storages": ["local"]}] and out["default_storages"] == ["local"]


def test_targets_follow_the_first_matching_rule_and_fall_back(svc):
    cloud(svc)
    assert svc.targets_for("document", 10) == ["local", "cloud"] and svc.targets_for("image", 10) == ["local"]
    svc.set_config({"policy": [{"types": ["image"], "max_mb": 1, "storages": ["cloud"]}], "default_storages": ["local"]})
    assert svc.targets_for("image", 100) == ["cloud"] and svc.targets_for("image", 5 * 1024 * 1024) == ["local"]
    cfg = svc.config()
    cfg["storages"][1]["enabled"] = False
    assert svc.targets_for("image", 100, cfg) == ["local"]                                 # a rule that names only switched-off storages falls back
    cfg["default_storages"] = ["cloud"]
    assert svc.targets_for("video", 1, cfg) == ["local"]


# ------------------------------------------------------------------------------------------------ finding what a turn produced
def test_new_files_become_artifacts_and_changed_ones_do_not(svc, ws):
    (ws / "old.md").write_text("old")
    (ws / "node_modules").mkdir()

    def make():
        (ws / "report.md").write_text("# Q3 report\nrevenue grew")
        (ws / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        (ws / "node_modules" / "junk.js").write_text("x")
        (ws / "old.md").write_text("old but edited")
        (ws / "sub").mkdir()
        (ws / "sub" / "data.csv").write_text("a,b\n1,2\n")
    got = turn(svc, ws, make)
    assert sorted(a["name"] for a in got) == ["data.csv", "plot.png", "report.md"]
    a = next(x for x in got if x["name"] == "report.md")
    assert a["type"] == "document" and a["preview"] == "markdown" and a["agent"] == "engineer" and a["session_id"] == "s1" and a["trace_id"] == "t1" and a["source"] == "auto"
    assert a["copies"] == [{"storage": "local", "status": "synced", "detail": "", "size": a["copies"][0]["size"], "updated": a["copies"][0]["updated"]}] and a["version"] == 1
    assert svc.read_bytes(a["id"]) == (b"# Q3 report\nrevenue grew", "local") and not (svc.stage / a["id"]).exists()          # nothing left in staging once every copy landed
    assert next(x for x in got if x["name"] == "data.csv")["path"] == "sub/data.csv"


def test_changed_files_are_collected_when_asked_and_versions_count_up(svc, ws):
    svc.set_config({"capture": "new_and_changed"})
    (ws / "r.md").write_text("v1")
    first = turn(svc, ws, lambda: None)
    assert first == []
    got = turn(svc, ws, lambda: (ws / "r.md").write_text("version two"))
    assert [a["version"] for a in got] == [1]
    got2 = turn(svc, ws, lambda: (ws / "r.md").write_text("version three!"))
    assert [a["version"] for a in got2] == [2]
    assert turn(svc, ws, lambda: (ws / "r.md").write_text("version three!"))[0:0] == []              # same bytes again: not a new artifact
    assert svc.list(q="r.md")["total"] == 2


def test_the_same_bytes_are_never_stored_twice(svc, ws):
    got = turn(svc, ws, lambda: (ws / "a.md").write_text("same"))
    again = svc.capture_file(ws / "a.md", "a.md", workspace=str(ws), source="tool", agent="x")
    assert again["id"] == got[0]["id"] and again["existing"] and svc.list()["total"] == 1


def test_capture_is_off_by_switch_and_never_breaks_a_turn(svc, ws, monkeypatch):
    svc.set_config({"capture": "off"})
    assert svc.snapshot(ws) is None and svc.capture_changes(None, ws, agent="a", session_id="s", trace_id="t") == []
    svc.set_config({"capture": "new", "enabled": False})
    assert svc.snapshot(ws) is None
    svc.set_config({"enabled": True})
    before = svc.snapshot(ws)
    (ws / "x.md").write_text("x")
    monkeypatch.setattr(svc, "snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk gone")))
    assert svc.capture_changes(before, ws, agent="a", session_id="s", trace_id="t") == []


def test_limits_skip_big_files_cap_the_count_and_stop_at_huge_trees(svc, ws, monkeypatch):
    svc.set_config({"max_file_mb": 1, "max_per_turn": 2})

    def make():
        (ws / "big.bin").write_bytes(b"\x01" * (1024 * 1024 + 1))
        for i in range(4):
            (ws / f"f{i}.txt").write_text(f"file {i}")
    got = turn(svc, ws, make)
    assert len(got) == 2 and all(a["name"] != "big.bin" for a in got)
    with pytest.raises(ArtifactError, match="more than the 1 MB limit"):
        svc.capture_file(ws / "big.bin", "big.bin", workspace=str(ws))
    monkeypatch.setattr(SV, "MAX_SCAN_FILES", 3)
    assert len(svc.snapshot(ws)) == 3


def test_snapshot_skips_links_unreadable_and_ignored_things(svc, ws, monkeypatch):
    (ws / "keep.md").write_text("k")
    (ws / "dist").mkdir()
    (ws / "dist" / "out.js").write_text("x")
    (ws / "bad.tmp").write_text("x")
    snap = svc.snapshot(ws)
    assert list(snap) == ["keep.md"]
    import os
    real = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p: (_ for _ in ()).throw(OSError("gone")) if str(p).endswith("keep.md") else real(p))
    assert svc.snapshot(ws) == {}
    monkeypatch.setattr(os, "lstat", lambda p: type("S", (), {"st_mode": 0o120777, "st_mtime_ns": 1, "st_size": 1})())      # a symlink
    assert svc.snapshot(ws) == {}


def test_capture_file_and_save_text_errors(svc, ws, monkeypatch):
    with pytest.raises(ArtifactError, match="cannot read ghost.md"):
        svc.capture_file(ws / "ghost.md", "ghost.md")
    (ws / "a.md").write_text("x")
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda self: (_ for _ in ()).throw(PermissionError("no")))
    with pytest.raises(ArtifactError, match="cannot read a.md: PermissionError"):
        svc.capture_file(ws / "a.md", "a.md")
    monkeypatch.undo()
    with pytest.raises(ArtifactError, match="there is nothing to save"):
        svc.save_text("n.md", "")
    svc.set_config({"max_file_mb": 1})
    with pytest.raises(ArtifactError, match="larger than the 1 MB limit"):
        svc.save_text("n.md", "x" * (1024 * 1024 + 1))
    svc.set_config({"enabled": False})
    with pytest.raises(ArtifactError, match="switched off"):
        svc.save_text("n.md", "x")
    with pytest.raises(ArtifactError, match="switched off"):
        svc.capture_file(ws / "a.md", "a.md")


def test_save_text_makes_a_searchable_artifact(svc):
    a = svc.save_text("../summary notes.md", "The quarterly revenue chart shows growth in EMEA.", agent="analyst", session_id="s9", tags=["Q3", " ", "sales"] + [f"t{i}" for i in range(20)], source="chat")
    assert a["name"] == "summary notes.md" and a["path"] == "" and a["source"] == "chat" and a["tags"][:2] == ["Q3", "sales"] and len(a["tags"]) == 12 and a["version"] == 1
    assert a["summary"].startswith("The quarterly revenue") and a["type"] == "document"
    assert svc.save_text("again.md", "The quarterly revenue chart shows growth in EMEA.")["version"] == 1                 # no path: no versions


# ------------------------------------------------------------------------------------------------ copies, retries, the secret scan
def test_cloud_copies_are_made_in_the_background_with_staging_until_they_land(svc, ws, mems):
    cloud(svc)
    got = turn(svc, ws, lambda: (ws / "r.md").write_text("# report"))
    a = got[0]
    assert [(c["storage"], c["status"]) for c in a["copies"]] == [("local", "synced"), ("cloud", "pending")] and (svc.stage / a["id"]).is_file()
    assert svc.sync_pending() == 1 and svc.sync_pending() == 0
    a = svc.get(a["id"])
    assert [(c["storage"], c["status"]) for c in a["copies"]] == [("local", "synced"), ("cloud", "synced")] and not (svc.stage / a["id"]).exists()
    assert mems["b1"].objects == {f"{a['id']}/r.md": b"# report"}
    assert svc.list(storage="cloud")["total"] == 1 and svc.list(storage="nope")["total"] == 0


def test_the_secret_scan_redacts_blocks_or_is_off(svc, ws, mems):
    cloud(svc)
    text = f"token = {SECRET}\nplain line"
    a = turn(svc, ws, lambda: (ws / "env.md").write_text(text))[0]
    assert a["secrets"] is True and SECRET not in a["summary"] and SECRET not in svc.db.one("SELECT search_text FROM artifacts WHERE id=?", (a["id"],))["search_text"]
    svc.sync_pending()
    assert svc.read_bytes(a["id"])[0].decode() == text                              # the copy on this PC is untouched
    assert b"[secret removed]" in mems["b1"].objects[f"{a['id']}/env.md"] and SECRET.encode() not in mems["b1"].objects[f"{a['id']}/env.md"]
    assert next(c for c in svc.get(a["id"])["copies"] if c["storage"] == "cloud")["detail"] == "secret-looking values were removed from this copy"
    svc.set_config({"secret_scan": "block"})
    b = turn(svc, ws, lambda: (ws / "env2.md").write_text(text + "!"))[0]
    svc.sync_pending()
    cb = next(c for c in svc.get(b["id"])["copies"] if c["storage"] == "cloud")
    assert cb["status"] == "blocked" and "not sent" in cb["detail"] and f"{b['id']}/env2.md" not in mems["b1"].objects
    svc.set_config({"secret_scan": "off"})
    assert svc.resync(b["id"]) and mems["b1"].objects[f"{b['id']}/env2.md"].decode() == text + "!"                      # retried after the rule changed
    c = turn(svc, ws, lambda: (ws / "pic.png").write_bytes(b"\x89PNG\x00" + SECRET.encode()))[0]
    svc.set_config({"secret_scan": "redact", "policy": [{"types": ["image"], "storages": ["cloud"]}]})
    svc.resync(c["id"])
    assert SECRET.encode() in mems["b1"].objects[f"{c['id']}/pic.png"]               # binary files cannot be scanned: they go as they are


def test_failed_copies_are_recorded_and_retried(svc, ws, mems):
    cloud(svc)
    a = turn(svc, ws, lambda: (ws / "r.md").write_text("# report"))[0]
    mems["b1"].fail = "the storage refused the credentials"
    svc.sync_pending()
    c = next(c for c in svc.get(a["id"])["copies"] if c["storage"] == "cloud")
    assert c["status"] == "failed" and c["detail"] == "the storage refused the credentials" and (svc.stage / a["id"]).is_file() and svc.sync_pending() == 0
    mems["b1"].fail = None
    assert next(c for c in svc.resync(a["id"])["copies"] if c["storage"] == "cloud")["status"] == "synced" and not (svc.stage / a["id"]).exists()


def test_sync_handles_missing_pieces(svc, ws, mems):
    svc.sync("nope")                                                                   # an unknown artifact: nothing happens
    cloud(svc)
    a = turn(svc, ws, lambda: (ws / "r.md").write_text("# report"))[0]
    svc.set_config({"storages": [{"id": "local", "kind": "local"}, {"id": "cloud", "kind": "s3", "enabled": False, "config": {"bucket": "b1"}}]})
    svc.sync_pending()
    assert next(c for c in svc.get(a["id"])["copies"] if c["storage"] == "cloud")["detail"] == "this storage is switched off or was removed"
    svc.set_config({"storages": [{"id": "local", "kind": "local"}, {"id": "cloud", "kind": "s3", "config": {"bucket": "b1"}}]})
    (svc.stage / a["id"]).unlink(missing_ok=True)
    svc.db.execute("UPDATE artifact_copies SET status='pending' WHERE storage_id='cloud'")
    svc.sync_pending()                                                                 # the bytes come back from the copy on this PC
    assert next(c for c in svc.get(a["id"])["copies"] if c["storage"] == "cloud")["status"] == "synced"
    b = turn(svc, ws, lambda: (ws / "s.md").write_text("# second"))[0]
    (svc.stage / b["id"]).unlink(missing_ok=True)
    svc.db.execute("UPDATE artifact_copies SET status='failed' WHERE artifact_id=? AND storage_id='local'", (b["id"],))
    svc.sync(b["id"], retry_failed=True)                                               # nowhere left to read the bytes from
    assert {c["storage"]: c["status"] for c in svc.get(b["id"])["copies"]}["local"] == "failed"
    assert "no longer available" in next(c for c in svc.get(b["id"])["copies"] if c["storage"] == "local")["detail"]


def test_resync_adds_copies_a_new_rule_asks_for(svc, ws, mems):
    a = turn(svc, ws, lambda: (ws / "r.md").write_text("# report"))[0]
    assert [c["storage"] for c in a["copies"]] == ["local"]
    cloud(svc)
    out = svc.resync(a["id"])
    assert {c["storage"]: c["status"] for c in out["copies"]} == {"local": "synced", "cloud": "synced"}
    with pytest.raises(ArtifactError, match="no such artifact"):
        svc.resync("nope")


def test_background_worker_is_started_for_cloud_copies_and_at_startup(svc, ws, mems, tmp_path):
    cloud(svc)
    live = ArtifactService(svc.db, home=svc.home, start_worker=True, storage_factory=svc.storage_factory)
    before = live.snapshot(ws)
    (ws / "r.md").write_text("# report")
    got = live.capture_changes(before, ws, agent="a", session_id="s", trace_id="t")
    live._thread.join(timeout=5)
    assert next(c for c in live.get(got[0]["id"])["copies"] if c["storage"] == "cloud")["status"] == "synced"
    live.kick()
    live._thread.join(timeout=5)
    svc.db.execute("UPDATE artifact_copies SET status='pending' WHERE storage_id='cloud'")
    live.resume()
    live._thread.join(timeout=5)
    assert live.get(got[0]["id"])["copies"][1]["status"] == "synced"
    live.resume()                                                                      # nothing pending: nothing started
    live.sync_pending = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    ArtifactService._run(live)                                                         # the worker swallows anything


# ------------------------------------------------------------------------------------------------ reading, searching, deleting
def seed(svc, ws):
    def make():
        (ws / "revenue.md").write_text("# Revenue\nEMEA grew strongly in Q3. Contact: priya")
        (ws / "users.csv").write_text("name,plan\nada,pro\n")
        (ws / "chart.png").write_bytes(b"\x89PNG\x00\x01")
        (ws / "model.pt").write_bytes(b"\x00\x01\x02" * 50)
        (ws / "script.py").write_text("print('hello world')\n")
    got = turn(svc, ws, make)
    return {a["name"]: a for a in got}


def test_search_filters_sorting_and_paging(svc, ws):
    arts = seed(svc, ws)
    assert svc.list()["total"] == 5
    assert [a["name"] for a in svc.list(q="emea q3")["items"]] == ["revenue.md"] and svc.list(q="HELLO")["items"][0]["name"] == "script.py"
    assert svc.list(q="100%_")["total"] == 0 and svc.list(q="ada")["total"] == 1
    assert {a["name"] for a in svc.list(type="image")["items"]} == {"chart.png"} and svc.list(agent="engineer")["total"] == 5 and svc.list(agent="other")["total"] == 0
    assert svc.list(session_id="s1")["total"] == 5 and svc.list(trace_id="t1")["total"] == 5 and svc.list(source="auto")["total"] == 5 and svc.list(source="chat")["total"] == 0
    assert svc.list(storage="local")["total"] == 5
    now = time.time()
    assert svc.list(since=now - 60, until=now + 60)["total"] == 5 and svc.list(since=now + 60)["total"] == 0 and svc.list(until=now - 3600)["total"] == 0
    assert [a["name"] for a in svc.list(min_size=100)["items"]] == ["model.pt"] and svc.list(max_size=10)["total"] == 1
    assert [a["name"] for a in svc.list(sort="name", order="asc")["items"]] == ["chart.png", "model.pt", "revenue.md", "script.py", "users.csv"]
    assert [a["name"] for a in svc.list(sort="size", order="desc", limit=2)["items"]][0] == "model.pt" and svc.list(limit=2, offset=4)["items"].__len__() == 1
    assert svc.list(sort="bogus")["total"] == 5 and svc.list(type="data")["items"][0]["tags"] == []
    f = svc.facets()
    assert f["total"] == 5 and {x["value"]: x["count"] for x in f["types"]} == {"document": 1, "data": 1, "image": 1, "model": 1, "code": 1} and f["agents"] == [{"value": "engineer", "count": 5}]
    assert f["storages"] == [{"value": "local", "count": 5}] and f["sources"] == [{"value": "auto", "count": 5}] and f["bytes"] > 0
    assert arts["revenue.md"]["summary"].startswith("# Revenue EMEA grew")


def test_previews_read_the_right_bytes(svc, ws, mems):
    arts = seed(svc, ws)
    p = svc.preview(arts["revenue.md"]["id"])
    assert p["kind"] == "markdown" and p["text"].startswith("# Revenue") and not p["truncated"]
    assert svc.preview(arts["users.csv"]["id"])["kind"] == "csv" and svc.preview(arts["script.py"]["id"])["kind"] == "text"
    assert svc.preview(arts["chart.png"]["id"]) == {"kind": "image", "text": "", "truncated": False} and svc.preview(arts["model.pt"]["id"])["kind"] == "none"
    big = svc.save_text("big.txt", "x" * (SV.PREVIEW_BYTES + 10))
    assert svc.preview(big["id"])["truncated"] and len(svc.preview(big["id"])["text"]) == SV.PREVIEW_BYTES
    weird = svc.save_text("data.weird", "plain words")
    assert weird["preview"] == "text" and svc.preview(weird["id"])["text"] == "plain words"


def test_reading_falls_back_between_storages_and_staging(svc, ws, mems):
    cloud(svc)
    a = turn(svc, ws, lambda: (ws / "r.md").write_text("# report"))[0]
    svc.sync_pending()
    import shutil
    shutil.rmtree(svc.home / "artifacts")                                                   # the folder copy is gone
    assert svc.read_bytes(a["id"]) == (b"# report", "cloud")
    mems["b1"].fail = "down"
    with pytest.raises(ArtifactError, match=r"could not be read from any storage \(local: .*; cloud: down\)"):
        svc.read_bytes(a["id"])
    (svc.stage).mkdir(parents=True, exist_ok=True)
    (svc.stage / a["id"]).write_bytes(b"staged")
    assert svc.read_bytes(a["id"]) == (b"staged", "staging")
    with pytest.raises(ArtifactError, match="no such artifact"):
        svc.read_bytes("nope")
    with pytest.raises(ArtifactError, match="no such artifact"):
        svc.get("nope")


def test_delete_removes_every_copy_and_reports_the_ones_that_failed(svc, ws, mems):
    cloud(svc)
    a = turn(svc, ws, lambda: (ws / "r.md").write_text("# report"))[0]
    svc.sync_pending()
    mems["b1"].fail = "refused"
    out = svc.delete(a["id"])
    assert out == {"deleted": True, "storage_errors": [{"storage": "cloud", "error": "refused"}]} and svc.list()["total"] == 0 and not (svc.home / "artifacts" / a["id"]).exists()
    with pytest.raises(ArtifactError, match="no such artifact"):
        svc.delete(a["id"])
    b = turn(svc, ws, lambda: (ws / "s.md").write_text("# second"))[0]                      # pending copies are not asked to delete anything
    svc.db.execute("UPDATE artifact_copies SET status='pending' WHERE artifact_id=?", (b["id"],))
    assert svc.delete(b["id"])["storage_errors"] == []


def test_retention_removes_old_artifacts(svc, ws):
    seed(svc, ws)
    assert svc.purge() == {"deleted": 0}
    svc.db.execute("UPDATE artifacts SET created=? WHERE name IN ('revenue.md','users.csv')", (time.time() - 40 * 86400,))
    assert svc.purge(30) == {"deleted": 2} and svc.list()["total"] == 3
    svc.set_config({"retention_days": 1})
    svc.db.execute("UPDATE artifacts SET created=? WHERE name='chart.png'", (time.time() - 5 * 86400,))
    before = svc.snapshot(ws)
    (ws / "new.md").write_text("n")
    svc.capture_changes(before, ws, agent="a", session_id="s", trace_id="t")                  # a purge happens at most once an hour, after a capture
    assert svc.list(q="chart")["total"] == 0 and svc._last_purge > 0
    svc.maybe_purge(svc.config())
    svc.maybe_purge({"retention_days": 0})


def test_storage_test_and_stats_never_leak_names(svc, ws, mems):
    cloud(svc)
    seed(svc, ws)
    assert svc.test_storage("local")["ok"] and svc.test_storage("cloud") == {"ok": True, "detail": "fine"}
    mems["b1"].fail = "nope"
    assert svc.test_storage("cloud") == {"ok": False, "detail": "nope"} and svc.test_storage("ghost") == {"ok": False, "detail": "the storage 'ghost' was removed"}
    st = svc.stats()
    assert st["total"] == 5 and st["by_type"]["image"] == 1 and st["storages"] == [{"kind": "local", "enabled": True, "cloud": False}, {"kind": "s3", "enabled": True, "cloud": True}]
    assert "revenue" not in str(st) and st["capture"] == "new" and st["secret_scan"] == "redact"
