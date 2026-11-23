"""Apps: the manifest, the templates, the store (files, versions, proposals, keys, environment, checks)."""
from __future__ import annotations

import asyncio
import json
import py_compile
import types

import pytest

from app.apiaccess.keys import ApiKeys
from app.apps import manifest as M
from app.apps import templates as T
from app.apps.store import AppError, AppStore, safe_rel
from app.db import Database


def run(c):
    return asyncio.run(c)


class Agents:
    def __init__(self, *items):
        self.items = {a.id: a for a in items}

    def get(self, i):
        return self.items.get(i)

    def list(self):
        return list(self.items.values())


def agent(i, api=True):
    return types.SimpleNamespace(id=i, name=i.title(), description=f"the {i} agent", api_access=api)


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    return AppStore(db, home=tmp_path / "home", keys=ApiKeys(db), agents=Agents(agent("coder"), agent("quiet", api=False)))


@pytest.fixture
def app(store):
    return store.create("Hello App", "static-chat", "says hello", "coder")


# ------------------------------------------------------------------------------------------------ manifest
def test_default_manifest_and_slug():
    m = M.default_manifest("  My App  ", "node", "d")
    assert m["name"] == "My App" and m["entry"] == "server/index.js" and m["start"] == "node server/index.js" and M.clean_manifest(m) == m
    assert M.default_manifest("", "static")["name"] == "Untitled app"
    assert M.slugify("My Great App!!") == "my-great-app" and M.slugify("###") == "app" and len(M.slugify("x" * 100)) == 40


@pytest.mark.parametrize("patch,msg", [
    ({"type": "ruby"}, "type must be"), ({"name": ""}, "give the app a name"), ({"name": 5}, "give the app a name"), ({"description": "x" * 301}, "description"),
    ({"version": "1.0"}, "version must"), ({"entry": "../x"}, "entry must"), ({"entry": "/etc/passwd"}, "entry must"), ({"entry": 3}, "entry must"),
    ({"type": "node", "start": "node a.js; rm -rf /"}, "start must"), ({"port": 80}, "port must"), ({"port": True}, "port must"),
    ({"agents": "x"}, "at most"), ({"agents": [{}]}, "needs an id"), ({"agents": [{"id": "a"}, {"id": "a"}]}, "twice"), ({"agents": [{"id": "a", "purpose": "x" * 201}]}, "purpose"),
    ({"agents": [{"id": f"a{i}"} for i in range(11)]}, "at most 10"),
    ({"permissions": []}, "permissions must"), ({"permissions": {"network": "wide"}}, "network must"), ({"permissions": {"allow": ["http://x.com"]}}, "https only"),
    ({"permissions": {"allow": "x"}}, "https only"),
    ({"env": "x"}, "at most 20"), ({"env": [{"name": "lower"}]}, "NAME in capitals"), ({"env": [{"name": "PORT"}]}, "reserved"), ({"env": [{"name": "A"}, {"name": "A"}]}, "twice"),
    ({"env": [{"name": "A", "description": "x" * 201}]}, "description"), ({"runtime": "mars"}, "runtime must"), ({"description": 4}, "description"),
])
def test_manifest_refuses_bad_values(patch, msg):
    with pytest.raises(M.ManifestError, match=msg):
        M.clean_manifest({**M.default_manifest("A"), **patch})


def test_manifest_must_be_an_object_and_normalises():
    with pytest.raises(M.ManifestError, match="object"):
        M.clean_manifest([])
    m = M.clean_manifest({"name": " A ", "type": "static", "start": "node x.js", "junk": 1, "permissions": {"network": "allowlist", "allow": ["https://api.example.com:8443"]},
                          "env": [{"name": "KEY", "description": " d ", "required": 1}], "agents": [{"id": "coder", "purpose": " p "}]})
    assert m["name"] == "A" and m["start"] == "" and "junk" not in m and m["env"] == [{"name": "KEY", "description": "d", "required": True}] and m["agents"] == [{"id": "coder", "purpose": "p"}]


# ------------------------------------------------------------------------------------------------ templates
@pytest.mark.parametrize("tid", list(T.TEMPLATES))
def test_every_template_renders_a_valid_app(tid, tmp_path):
    files = T.render(tid, "My <App> & co", "desc <b>", "coder")
    assert {"app.json", "Dockerfile", "README.md", ".gitignore", ".env.example", "public/index.html", "public/app.js", "public/style.css"} <= set(files)
    m = M.clean_manifest(json.loads(files["app.json"]))
    assert m["type"] == T.TEMPLATES[tid]["type"] and m["agents"][0]["id"] == "coder" and m["entry"] in files or m["type"] == "static"
    assert "My &lt;App&gt; &amp; co" in files["public/index.html"] and 'data-agent="coder"' in files["public/index.html"]
    for rel, text in files.items():
        if rel.startswith("public/"):
            assert "http://" not in text and "https://" not in text                    # nothing is loaded from another website
        if rel.endswith(".py"):
            f = tmp_path / rel.replace("/", "_")
            f.write_text(text, encoding="utf-8")
            py_compile.compile(str(f), doraise=True)
    static = T.TEMPLATES[tid]["type"] == "static"
    assert ("public/agent-client.js" in files) == static and ("server/" in "".join(files)) == (not static)


def test_unknown_template_and_env_example():
    with pytest.raises(KeyError):
        T.render("nope", "x")
    ex = T.env_example({"env": [{"name": "TOKEN", "description": "the token"}, {"name": "B", "description": ""}]})
    assert "# the token\nTOKEN=" in ex and "\nB=" in ex and "AGENT_STUDIO_API_KEY" in ex
    assert "agent-client.js" in T.render("static-chat", "x")["README.md"] and "AGENT_STUDIO_BASE_URL" in T.render("node-api", "x")["README.md"]
    assert T.render("static-chat", "x")["public/index.html"].count('data-agent=""') == 1


# ------------------------------------------------------------------------------------------------ paths
@pytest.mark.parametrize("bad", [None, "", "  ", "/etc/x", "C:/x", "C:\\x", "a/../b", "../a", "a//b", "./", "a/./b", ".git/config", "node_modules/x", "x/.git/y", "a\x00b",
                                 "a" * 201, "a/b/c/d/e/f/g/h/i", "con", "dir/NUL.txt", "trailing.", "x/..hidden/.."])
def test_unsafe_paths_are_refused(bad):
    with pytest.raises(AppError):
        safe_rel(bad)


def test_safe_paths_are_normalised():
    assert safe_rel("./public\\app.js") == "public/app.js" and safe_rel("a/b.txt") == "a/b.txt" and safe_rel(".env.example") == ".env.example"


# ------------------------------------------------------------------------------------------------ creating, listing, updating
def test_create_list_get_and_limits(store):
    a = store.create("Hello", "static-form", "d", "coder", mode="simple", session_id="s1")
    assert a["slug"] == "hello" and a["type"] == "static" and a["mode"] == "simple" and a["version"] == 1 and a["agents"] == ["coder"] and a["files"] >= 8
    b = store.create("Hello", "node-api")
    assert b["slug"] == "hello-2" and b["type"] == "node"
    assert [x["id"] for x in store.list()] == [b["id"], a["id"]] and store.get(a["id"])["name"] == "Hello" and len(store.templates()) == 6
    assert store.latest_for_session("s1") == a["id"] and store.latest_for_session("") == "" and store.latest_for_session("nope") == ""
    with pytest.raises(AppError, match="template must"):
        store.create("x", "nope")
    with pytest.raises(AppError, match="mode must"):
        store.create("x", "static-chat", mode="weird")
    for bad in ("nope", "../x", None, "ABCDEF123456"):
        with pytest.raises(AppError, match="no such app"):
            store.get(bad)
    store.set_config({"max_apps": 2})
    with pytest.raises(AppError, match="at most 2 apps"):
        store.create("c", "static-chat")
    with pytest.raises(AppError, match="at most 2 apps"):
        store.duplicate(a["id"])


def test_config_validation(store):
    assert store.config()["enabled"] and store.set_config({"auto_apply": True, "git_name": "Me", "max_files": 50})["max_files"] == 50
    for bad in ({"x": 1}, {"enabled": 1}, {"max_apps": 0}, {"max_apps": True}, {"git_name": "a\nb"}, {"git_email": 5}, {"preview_port": 80}):
        with pytest.raises(AppError):
            store.set_config(bad)
    with pytest.raises(AppError, match="send an object"):
        store.set_config([])
    store.db.set_setting("apps_config", "{broken")
    assert store.config()["max_apps"] == 50
    store.db.set_setting("apps_config", "[]")
    assert store.config()["max_apps"] == 50
    store.set_config({"enabled": False})
    with pytest.raises(AppError, match="switched off"):
        store.create("x", "static-chat")


def test_update_rename_and_mode(store, app):
    out = store.update(app["id"], name="Renamed", description="new", mode="simple", session_id="abc")
    assert out["name"] == "Renamed" and out["description"] == "new" and out["mode"] == "simple" and out["session_id"] == "abc"
    assert store.manifest(app["id"])["name"] == "Renamed" and store.update(app["id"])["name"] == "Renamed"
    assert store.update(app["id"], description="again")["description"] == "again" and store.update(app["id"], name="N2")["name"] == "N2"
    with pytest.raises(AppError, match="mode must"):
        store.update(app["id"], mode="x")


def test_duplicate_and_delete(store, app):
    store.write(app["id"], "public/extra.txt", "hello")
    cp = store.duplicate(app["id"])
    assert cp["name"] == "Hello App copy" and cp["slug"] == "hello-app-copy" and cp["files"] == app["files"] + 1 and store.read(cp["id"], "public/extra.txt")["content"] == "hello"
    assert store.duplicate(app["id"])["slug"] == "hello-app-copy-2"
    store.ensure_key(app["id"])
    kid = store._secrets()[app["id"]]["key_id"]
    store.propose(app["id"], [{"op": "write", "path": "a.txt", "content": "x"}])
    assert store.delete(app["id"]) == {"deleted": True}
    assert not (store.base / app["id"]).exists() and store.db.one("SELECT id FROM api_keys WHERE id=?", (kid,)) is None and app["id"] not in store._secrets()
    assert store.db.one("SELECT COUNT(*) AS n FROM app_versions WHERE app_id=?", (app["id"],))["n"] == 0
    with pytest.raises(AppError):
        store.delete(app["id"])
    other = store.create("o", "static-chat")
    store.ensure_key(other["id"])                                   # no agents: no key; nothing to delete
    store.delete(other["id"])


def test_delete_tolerates_a_key_that_is_already_gone(store, app):
    store.ensure_key(app["id"])
    store.keys.delete(store._secrets()[app["id"]]["key_id"])
    store.delete(app["id"])


# ------------------------------------------------------------------------------------------------ files
def test_tree_read_write_delete(store, app):
    aid = app["id"]
    tree = {f["path"]: f for f in store.tree(aid)}
    assert "public/index.html" in tree and not tree["app.json"]["binary"]
    (store.root(aid) / "public" / "pic.bin").write_bytes(b"\x00\x01\x02")
    (store.root(aid) / "node_modules").mkdir()
    (store.root(aid) / "node_modules" / "x.js").write_text("x")
    names = {f["path"]: f for f in store.tree(aid)}
    assert names["public/pic.bin"]["binary"] and "node_modules/x.js" not in names
    assert store.read(aid, "public/pic.bin") == dict(path="public/pic.bin", size=3, binary=True, content=None)
    out = store.write(aid, "public/new/page.html", "<p>hi</p>")
    assert out["content"] == "<p>hi</p>" and store.read(aid, "./public/new/page.html")["size"] == 9
    with pytest.raises(AppError, match="no such file"):
        store.read(aid, "nope.txt")
    with pytest.raises(AppError, match="must be text"):
        store.write(aid, "a.txt", 5)
    assert store.delete_file(aid, "public/new/page.html") == {"deleted": "public/new/page.html"} and not (store.root(aid) / "public" / "new").exists()
    with pytest.raises(AppError, match="cannot be deleted"):
        store.delete_file(aid, "app.json")
    with pytest.raises(AppError, match="no such file"):
        store.delete_file(aid, "gone.txt")
    big = store.root(aid) / "big.txt"
    big.write_bytes(b"a" * (2 * 1024 * 1024 + 1))
    with pytest.raises(AppError, match="larger than 2 MB"):
        store.read(aid, "big.txt")


def test_manifest_stays_valid_when_edited_as_a_file(store, app):
    aid = app["id"]
    with pytest.raises(AppError, match="not a valid manifest"):
        store.write(aid, "app.json", "{nope")
    with pytest.raises(AppError, match="not a valid manifest"):
        store.write(aid, "app.json", json.dumps({"name": ""}))
    good = json.dumps({**store.manifest(aid), "version": "2.0.0"})
    store.write(aid, "app.json", good)
    assert store.manifest(aid)["version"] == "2.0.0"
    (store.root(aid) / "app.json").write_text("{bad")
    with pytest.raises(AppError, match="missing or not valid"):
        store.manifest(aid)
    assert store.get(aid)["agents"] == [] and store.get(aid)["type"] == "static"
    (store.root(aid) / "app.json").unlink()
    assert store.check(aid)["ok"] is False


def test_quotas(store, app):
    aid = app["id"]
    store.set_config({"max_file_kb": 16, "max_files": 10, "max_total_mb": 1})
    with pytest.raises(AppError, match="larger than 16 KB"):
        store.write(aid, "a.txt", "x" * (16 * 1024 + 1))
    for i in range(10 - store.get(aid)["files"]):
        store.write(aid, f"f{i}.txt", "x")
    with pytest.raises(AppError, match="at most 10 files"):
        store.write(aid, "one-more.txt", "x")
    store.set_config({"max_files": 100, "max_file_kb": 4096})
    with pytest.raises(AppError, match="at most 1 MB"):
        store.add_asset(aid, "big.csv", b"x" * (1024 * 1024 + 10))
    store.set_config({"enabled": False})
    for call in (lambda: store.write(aid, "x.txt", "x"), lambda: store.delete_file(aid, "f0.txt"), lambda: store.add_asset(aid, "a.csv", b"x"), lambda: store.set_manifest(aid, {}),
                 lambda: store.restore(aid, 1), lambda: store.propose(aid, []), lambda: store.update(aid), lambda: store.set_env(aid, {}), lambda: store.rotate_key(aid)):
        with pytest.raises(AppError, match="switched off"):
            call()


def test_assets(store, app):
    aid = app["id"]
    out = store.add_asset(aid, "C:\\fakepath\\My Spec (v2).MD", b"# spec")
    assert out == dict(path="assets/My_Spec_v2_.MD", size=6) and store.read(aid, out["path"])["content"] == "# spec"
    with pytest.raises(AppError, match="attachments can be"):
        store.add_asset(aid, "run.exe", b"x")
    with pytest.raises(AppError, match="at most 2 MB"):
        store.add_asset(aid, "big.pdf", b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(AppError, match="attachments can be"):
        store.add_asset(aid, "...", b"x")


def test_symlinks_and_escapes_are_refused(store, app, tmp_path):
    aid = app["id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    link = store.root(aid) / "public" / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not allowed here")
    with pytest.raises(AppError, match="link"):
        store.write(aid, "public/link/x.txt", "x")
    assert "public/link" not in {f["path"] for f in store.tree(aid)}
    assert not (outside / "x.txt").exists()


def test_resolve_refuses_a_path_outside_the_app(store, app, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr("app.apps.store.safe_rel", lambda rel: rel)
    with pytest.raises(AppError, match="outside the app"):
        store._resolve(app["id"], "../outside.txt")
    assert isinstance(store.root(app["id"]), Path)


# ------------------------------------------------------------------------------------------------ manifest changes
def test_set_manifest_merges_and_syncs_the_key(store, app):
    aid = app["id"]
    key = store.ensure_key(aid)
    out = store.set_manifest(aid, {"permissions": {"network": "allowlist", "allow": ["https://api.example.com"]}, "port": 9000, "version": "1.0.0", "junk": 1,
                                   "agents": [{"id": "coder", "purpose": "a"}, {"id": "quiet", "purpose": "b"}]})
    assert out["permissions"] == {"network": "allowlist", "allow": ["https://api.example.com"]} and out["port"] == 9000 and "junk" not in out
    assert sorted(store.key_info(aid)["agents"]) == ["coder", "quiet"] and store.ensure_key(aid) == key
    store.set_manifest(aid, {"permissions": {"network": "none"}})
    assert store.manifest(aid)["permissions"]["allow"] == ["https://api.example.com"]
    with pytest.raises(AppError, match="send an object"):
        store.set_manifest(aid, [])
    with pytest.raises(AppError, match="port must"):
        store.set_manifest(aid, {"port": 5})


# ------------------------------------------------------------------------------------------------ versions
def test_versions_diff_and_restore(store, app):
    aid = app["id"]
    assert [v["version"] for v in store.versions(aid)] == [1] and store.get(aid)["version"] == 1
    store.write(aid, "public/app.js", "changed\n")
    store.write(aid, "public/new.txt", "new\n")
    store.delete_file(aid, "README.md")
    (store.root(aid) / "public" / "bin.dat").write_bytes(b"\x00\x00")
    assert store.snapshot(aid, "mine") == 2 and store.snapshot(aid, "x" * 300, "agent:Coder") == 3 and store.snapshot(aid, "y", "bogus author!") == 4
    assert [v["author"] for v in store.versions(aid)] == ["user", "agent:Coder", "user", "user"]
    d = {x["path"]: x for x in store.diff(aid, 1)}
    assert d["public/app.js"]["status"] == "changed" and d["public/new.txt"]["status"] == "added" and d["README.md"]["status"] == "removed" and d["public/bin.dat"]["old"] is None
    assert d["public/new.txt"]["old"] is None and d["public/new.txt"]["new"] == "new\n" and d["README.md"]["new"] is None
    out = store.restore(aid, 1)
    assert out["version"] == 5 and "public/new.txt" not in {f["path"] for f in store.tree(aid)} and store.read(aid, "README.md")["content"] and store.versions(aid)[0]["author"] == "restore"
    for bad in (99, True, "x"):
        with pytest.raises(AppError, match="not kept"):
            store.version_files(aid, bad)
    store.set_config({"keep_versions": 5})
    for _ in range(6):
        store.snapshot(aid)
    assert len(store.versions(aid)) == 5


# ------------------------------------------------------------------------------------------------ proposals
def test_proposals_are_reviewed_then_applied(store, app):
    aid = app["id"]
    res = store.propose(aid, [{"op": "write", "path": "public/app.js", "content": "// new\n"}, {"op": "patch", "path": "public/style.css", "edits": [{"old": "margin: 0;", "new": "margin: 1px;"}]},
                              {"op": "delete", "path": "README.md"}], author="agent:Coder", note="restyle", session_id="chat1")
    assert res["status"] == "pending" and store.get(aid)["pending"] == 1 and store.get(aid)["session_id"] == "chat1"
    pend = store.proposals(aid)[0]
    ch = {c["path"]: c for c in pend["changes"]}
    assert ch["public/app.js"]["new"] == "// new\n" and ch["public/app.js"]["old"] and "margin: 1px;" in ch["public/style.css"]["new"] and ch["README.md"]["old"] and ch["README.md"]["fits"]
    assert store.read(aid, "public/app.js")["content"] != "// new\n"
    out = store.accept(aid, pend["id"])
    assert out["version"] == 2 and store.read(aid, "public/app.js")["content"] == "// new\n" and "README.md" not in {f["path"] for f in store.tree(aid)} and store.get(aid)["pending"] == 0
    assert store.versions(aid)[0]["author"] == "agent:Coder" and store.proposals(aid, "accepted")[0]["changes"][0]["old"] is None
    assert len(store.proposals(aid, "all")) == 1
    with pytest.raises(AppError, match="already handled"):
        store.accept(aid, pend["id"])
    with pytest.raises(AppError, match="no such change"):
        store.reject(aid, "nope")


def test_apply_now_and_reject_and_accept_all(store, app):
    aid = app["id"]
    res = store.propose(aid, [{"op": "write", "path": "a.txt", "content": "a"}], author="agent:X", apply=True, note="")
    assert res["status"] == "applied" and res["version"] == 2 and store.read(aid, "a.txt")["content"] == "a"
    p1 = store.propose(aid, [{"op": "write", "path": "b.txt", "content": "b"}], author="weird author")["proposal_id"]
    p2 = store.propose(aid, [{"op": "write", "path": "c.txt", "content": "c"}])["proposal_id"]
    assert store.reject(aid, p1) == {"rejected": True}
    store.accept(aid, p2)
    p3 = store.propose(aid, [{"op": "patch", "path": "c.txt", "edits": [{"old": "c", "new": "d"}]}])["proposal_id"]
    p4 = store.propose(aid, [{"op": "write", "path": "e.txt", "content": "e"}])["proposal_id"]
    out = store.accept_all(aid)
    assert out["accepted"] == 2 and out["stale"] == [] and store.read(aid, "c.txt")["content"] == "d"
    assert {p["status"] for p in store.proposals(aid, "all") if p["id"] in (p2, p3, p4)} == {"accepted"}
    store.propose(aid, [{"op": "write", "path": "z.txt", "content": "z"}])
    assert store.reject_all(aid) == {"rejected": 1} and store.get(aid)["pending"] == 0


def test_a_proposal_that_no_longer_fits_goes_stale(store, app):
    aid = app["id"]
    store.write(aid, "t.txt", "hello world")
    pid = store.propose(aid, [{"op": "patch", "path": "t.txt", "edits": [{"old": "world", "new": "there"}]}])["proposal_id"]
    store.write(aid, "t.txt", "something else")
    with pytest.raises(AppError, match="no longer fits"):
        store.accept(aid, pid)
    assert store.proposals(aid, "stale")[0]["changes"][0]["old"] is None
    pid2 = store.propose(aid, [{"op": "patch", "path": "t.txt", "edits": [{"old": "else", "new": "x"}]}])["proposal_id"]
    store.write(aid, "t.txt", "gone")
    pend = store.proposals(aid)[0]
    assert pend["changes"][0]["fits"] is False and pend["changes"][0]["new"] is None
    assert store.accept_all(aid)["stale"][0]["id"] == pid2
    store.write(aid, "t.txt", "present")
    pid3 = store.propose(aid, [{"op": "delete", "path": "t.txt"}])["proposal_id"]
    store.delete_file(aid, "t.txt")
    assert store.proposals(aid)[0]["changes"][0]["fits"] is False
    store.reject(aid, pid3)


@pytest.mark.parametrize("changes,msg", [
    ([], "send 1 to"), ("x", "send 1 to"), ([{"op": "zap", "path": "a"}], "op must be"), ([5], "op must be"), ([{"op": "write", "path": "a.txt", "content": 3}], "must be text"),
    ([{"op": "write", "path": "../a", "content": "x"}], "not allowed"), ([{"op": "patch", "path": "a.txt", "edits": []}], "edits"), ([{"op": "patch", "path": "a.txt"}], "edits"),
    ([{"op": "delete", "path": "nope.txt"}], "does not exist"), ([{"op": "delete", "path": "app.json"}], "cannot be deleted"),
    ([{"op": "patch", "path": "nope.txt", "edits": [{"old": "a", "new": "b"}]}], "not an existing text file"),
    ([{"op": "patch", "path": "README.md", "edits": [{"old": "zzzzqqq", "new": "b"}]}], "no longer there"),
    ([{"op": "write", "path": "k.txt", "content": "token sk-" + "a" * 30}], "secret"),
    ([{"op": "write", "path": "app.json", "content": "{bad"}], "not a valid manifest"),
])
def test_bad_proposals_are_refused(store, app, changes, msg):
    with pytest.raises(AppError, match=msg):
        store.propose(app["id"], changes)
    assert store.get(app["id"])["pending"] == 0


def test_patching_a_binary_file_is_refused_and_pending_is_capped(store, app):
    aid = app["id"]
    (store.root(aid) / "b.bin").write_bytes(b"\x00\x01")
    with pytest.raises(AppError, match="not an existing text file"):
        store.propose(aid, [{"op": "patch", "path": "b.bin", "edits": [{"old": "a", "new": "b"}]}])
    for i in range(20):
        store.propose(aid, [{"op": "write", "path": f"p{i}.txt", "content": "x"}])
    with pytest.raises(AppError, match="20 changes are already waiting"):
        store.propose(aid, [{"op": "write", "path": "p99.txt", "content": "x"}])
    store.set_config({"max_file_kb": 16})
    with pytest.raises(AppError, match="at most 16 KB"):
        store.propose(aid, [{"op": "write", "path": "big.txt", "content": "x" * (17 * 1024)}])


def test_skip_conflicts_in_plan(store, app):
    aid = app["id"]
    changes = store._clean_changes([{"op": "delete", "path": "nope.txt"}, {"op": "write", "path": "ok.txt", "content": "x"}])
    overlay, bad = store._plan(aid, changes, skip=True)
    assert bad == [1] and overlay == {"ok.txt": b"x"}


# ------------------------------------------------------------------------------------------------ keys and environment
def test_the_app_gets_its_own_key_limited_to_its_agents(store, app):
    aid = app["id"]
    assert store.key_info(aid)["exists"] is False and "created the first time" in store.key_info(aid)["note"]
    key = store.ensure_key(aid)
    assert key.startswith("asx_") and store.ensure_key(aid) == key
    info = store.key_info(aid)
    assert info["exists"] and info["agents"] == ["coder"] and "*" not in info["agents"] and not info["revoked"] and info["rate_per_min"] == 60
    assert store.keys.verify(key) and store.keys.allows(store.keys.verify(key), "coder") and not store.keys.allows(store.keys.verify(key), "quiet")
    rot = store.rotate_key(aid)
    assert rot["key"] != key and store.keys.verify(key) is None and store.keys.verify(rot["key"]) and store.ensure_key(aid) == rot["key"]
    assert store.revoke_key(aid)["revoked"] is True
    with pytest.raises(AppError, match="revoked"):
        store.ensure_key(aid)
    again = store.rotate_key(aid)
    assert store.keys.verify(again["key"]) and store.key_info(aid)["revoked"] is False


def test_no_agents_means_no_key(store):
    a = store.create("Bare", "static-chat")
    assert store.ensure_key(a["id"]) is None and "Add an agent" in store.key_info(a["id"])["note"]
    with pytest.raises(AppError, match="add an agent"):
        store.rotate_key(a["id"])
    with pytest.raises(AppError, match="no key yet"):
        store.revoke_key(a["id"])
    store._sync_key(a["id"])
    store.keys = None
    assert store.ensure_key(a["id"]) is None
    store.delete(a["id"])


def test_a_key_removed_elsewhere_is_made_again(store, app):
    aid = app["id"]
    first = store.ensure_key(aid)
    store.keys.delete(store._secrets()[aid]["key_id"])
    assert store.ensure_key(aid) not in (None, first)


def test_broken_secrets_setting_is_tolerated(store, app):
    store.db.set_setting("apps_secrets", "{bad")
    assert store._secrets() == {}
    store.db.set_setting("apps_secrets", "[]")
    assert store._secrets() == {} and store._agent_ids("nope") == []


def test_environment_values_are_write_only(store, app):
    aid = app["id"]
    store.set_manifest(aid, {"env": [{"name": "API_TOKEN", "description": "the token", "required": True}, {"name": "OTHER"}]})
    assert store.env_status(aid) == [dict(name="API_TOKEN", description="the token", required=True, set=False), dict(name="OTHER", description="", required=False, set=False)]
    out = store.set_env(aid, {"API_TOKEN": "s3cret"})
    assert out[0]["set"] is True and "s3cret" not in json.dumps(out) and store.env_values(aid) == {"API_TOKEN": "s3cret"}
    store.set_env(aid, {"API_TOKEN": ""})
    assert store.env_values(aid) == {}
    with pytest.raises(AppError, match="not listed"):
        store.set_env(aid, {"NOPE": "x"})
    with pytest.raises(AppError, match="at most 4000"):
        store.set_env(aid, {"OTHER": "x" * 4001})
    with pytest.raises(AppError, match="send"):
        store.set_env(aid, [])
    store.set_env(aid, {"OTHER": "v"})
    store.set_manifest(aid, {"env": []})
    assert store.env_values(aid) == {}
    assert "s3cret" not in (store.root(aid) / "app.json").read_text()


# ------------------------------------------------------------------------------------------------ checks
def test_check_reports_what_is_wrong(store, app):
    aid = app["id"]
    ok = store.check(aid)
    assert ok["ok"] and [p["level"] for p in ok["problems"]] == [] and ok["agents"] == [dict(id="coder", exists=True, api_access=True)]
    store.set_manifest(aid, {"agents": [{"id": "quiet"}, {"id": "ghost"}], "entry": "public/missing.html"})
    store.write(aid, "public/index.html", '<script src="https://cdn.example.com/x.js"></script>')
    store.write(aid, "public/app.js", "var k = 'sk-" + "b" * 30 + "';")
    (store.root(aid) / "public" / "bin.png").write_bytes(b"\x00\x01")
    res = store.check(aid)
    text = " | ".join(f"{p['level']}:{p['text']}" for p in res["problems"])
    assert not res["ok"] and "entry file 'public/missing.html' does not exist" in text and "API access switched off" in text and "'ghost' does not exist" in text
    assert "looks like it contains a secret" in text and "loads code or styles from another website" in text
    store.delete_file(aid, "public/index.html")
    assert "no public/index.html" in " ".join(p["text"] for p in store.check(aid)["problems"])
    srv = store.create("Srv", "node-api")
    assert any(p["level"] == "info" for p in store.check(srv["id"])["problems"])
    store.set_config({"allow_server_apps": True})
    assert not any(p["level"] == "info" for p in store.check(srv["id"])["problems"])
    store.agents = None
    assert store.check(aid)["agents"][0]["exists"] is False


def test_counts_for_the_bom(store, app):
    store.create("S", "node-api", agent_id="quiet")
    store.propose(app["id"], [{"op": "write", "path": "a.txt", "content": "x"}])
    assert store.counts() == dict(apps=2, by_type={"static": 1, "node": 1}, agents_called=2, pending=1)


# ------------------------------------------------------------------------------------------------ live updates
def test_events_reach_subscribers(store, app):
    async def go():
        q = store.subscribe(app["id"])
        store.write(app["id"], "a.txt", "x")
        store.propose(app["id"], [{"op": "write", "path": "b.txt", "content": "y"}], author="agent:A")
        ev = [await asyncio.wait_for(q.get(), 1) for _ in range(2)]
        store.unsubscribe(app["id"], q)
        store.write(app["id"], "c.txt", "z")
        return ev
    ev = run(go())
    assert ev[0]["type"] == "files" and ev[1]["type"] == "proposed" and ev[1]["pending"] == 1


def test_a_closed_loop_is_dropped(store, app):
    loop = asyncio.new_event_loop()
    loop.close()
    store._subs[app["id"]] = {(loop, asyncio.Queue())}
    store.write(app["id"], "a.txt", "x")
    assert store._subs[app["id"]] == set()


def test_invalid_utf8_counts_as_binary_and_an_unreadable_file_too(store, app, monkeypatch):
    aid = app["id"]
    (store.root(aid) / "public" / "latin.txt").write_bytes(bytes([99, 97, 102, 233]))
    assert {f["path"]: f for f in store.tree(aid)}["public/latin.txt"]["binary"] is True
    monkeypatch.setattr(store, "_walk", lambda root: [dict(path="ghost.txt", size=0)])
    assert store.tree(aid) == [dict(path="ghost.txt", size=0, binary=True)]


def test_an_app_whose_folder_vanished_still_lists(store, app):
    import shutil
    shutil.rmtree(store.root(app["id"]))
    assert store.get(app["id"])["files"] == 0 and store.get(app["id"])["agents"] == []


def test_a_link_in_the_path_is_refused(store, app, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "link")
    with pytest.raises(AppError, match="link"):
        store._resolve(app["id"], "public/link/x.txt")


def test_rotating_a_key_whose_row_was_deleted_makes_a_new_one(store, app):
    aid = app["id"]
    store.ensure_key(aid)
    store.keys.delete(store._secrets()[aid]["key_id"])
    assert store.keys.verify(store.rotate_key(aid)["key"])


def test_deleting_an_app_removes_read_only_git_files(store, app):
    import os
    import stat
    obj = store.root(app["id"]) / ".git" / "objects" / "ab"
    obj.mkdir(parents=True)
    f = obj / "cdef"
    f.write_bytes(b"x")
    os.chmod(f, stat.S_IREAD)
    root = store.root(app["id"])
    store.delete(app["id"])
    assert not root.exists()
    from app.apps.store import remove_tree
    remove_tree(root)                                              # already gone: nothing to do
