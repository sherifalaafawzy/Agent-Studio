"""Apps: export, git, deploy plan, the builder's tools, the REST API and the wiring into chat, the BOM and settings."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import subprocess
import types
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.apps import export as E
from app.apps.api import event_stream
from app.apps.store import AppError
from app.apps.tools import APP_RULES, app_tools
from app.harness.tools import ALL_TOOLS, ToolContext, build_tools


def run(c):
    return asyncio.run(c)


@pytest.fixture
def store(state):
    return state.apps


@pytest.fixture
def app(store):
    return store.create("Hello", "static-chat", "d", "coder")


def cp(out="", err="", rc=0):
    return subprocess.CompletedProcess([], rc, out, err)


class FakeGit:
    """A fake `git` runner: records the verb and options of each call and answers from `replies[verb]` (a result, a list of results, or an exception)."""

    def __init__(self, **replies):
        self.calls, self.replies = [], replies

    def __call__(self, args, cwd, env, timeout):
        rest, i = [], 1
        while i < len(args):
            if args[i] == "-c":
                rest.append(("opt", args[i + 1]))
                i += 2
            else:
                rest.append(args[i])
                i += 1
        verb = next(a for a in rest if isinstance(a, str))
        self.calls.append(dict(verb=verb, args=[a for a in rest if isinstance(a, str)], opts=[a[1] for a in rest if isinstance(a, tuple)], env=env, cwd=cwd, timeout=timeout))
        r = self.replies.get(verb, cp())
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, BaseException):
            raise r
        return r

    def last(self, verb):
        return [c for c in self.calls if c["verb"] == verb][-1]


def git(store, **replies):
    fake = FakeGit(**replies)
    return E.Git(store, run=fake, which=lambda name: "/usr/bin/git"), fake


# ------------------------------------------------------------------------------------------------ export
def test_ignore_patterns():
    pats = E._patterns("# comment\n\n.env\n.env.*\n!.env.example\nnode_modules/\n*.log\nbuild/**\nsecret.txt\n")
    assert E.ignored(".env", pats) and E.ignored(".env.local", pats) and not E.ignored(".env.example", pats) and E.ignored("a/node_modules/x.js", pats)
    assert E.ignored("deep/app.log", pats) and E.ignored("secret.txt", pats) and E.ignored("a/secret.txt", pats) and not E.ignored("public/app.js", pats) and not E.ignored("node_modules", pats)


def test_the_zip_holds_the_app_without_what_gitignore_excludes(store, app):
    aid = app["id"]
    store.write(aid, ".env", "TOKEN=abc")
    store.write(aid, "debug.log", "x")
    data, info = E.export_zip(store, aid)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert info["slug"] == "hello" and info["files"] == len(names) and ".env" in info["skipped"] and "debug.log" in info["skipped"]
    assert "hello/app.json" in names and "hello/.env.example" in names and not any(n.endswith((".env", "debug.log")) for n in names)
    assert info["size"] == len(data)


def test_a_secret_blocks_the_export_and_always_skipped_folders_stay_out(store, app):
    aid = app["id"]
    store.write(aid, "public/app.js", "var k='sk-" + "x" * 30 + "';")
    with pytest.raises(AppError, match="not exported: these files look like they contain a secret: public/app.js"):
        E.export_zip(store, aid)
    assert E.secret_findings(store, aid) == ["public/app.js"]
    store.write(aid, "public/app.js", "ok")
    (store.root(aid) / "public" / "pic.bin").write_bytes(b"\x00\x01")
    keep, skipped = E.exported_files(store, aid)
    assert "public/pic.bin" in keep and "public/app.js" in keep and skipped == []
    store.write(aid, ".gitignore", "")
    (store.root(aid) / "__pycache__").mkdir()
    (store.root(aid) / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert "__pycache__/x.pyc" not in E.exported_files(store, aid)[0]


# ------------------------------------------------------------------------------------------------ git
def test_git_must_be_installed(store, app):
    g = E.Git(store, run=FakeGit(), which=lambda name: None)
    with pytest.raises(AppError, match="Git is not installed"):
        g.init(app["id"])
    assert E.Git(store).which is not None and E._run is not None


def test_status_before_and_after_init(store, app):
    aid = app["id"]
    g, fake = git(store, status=cp("## main...origin/main [ahead 1]\n M public/app.js\n?? new.txt\n"), log=cp("abc1234 first commit\n"))
    assert g.status(aid) == dict(initialised=False, remote="", branch="", changes=[], has_token=False)
    (store.root(aid) / ".git").mkdir()
    out = g.status(aid)
    assert out["initialised"] and out["branch"] == "main" and out["tracking"].startswith("main...origin/main") and out["changes"] == [dict(status="M", path="public/app.js"), dict(status="??", path="new.txt")]
    assert out["last_commit"] == "abc1234 first commit" and out["has_token"] is False
    g2, _ = git(store, status=cp(""), log=cp("", "", 128))
    assert g2.status(aid)["last_commit"] == "" and g2.status(aid)["branch"] == ""
    for first in ("## No commits yet on trunk\n", "## Initial commit on trunk\n"):
        g1, _ = git(store, status=cp(first), log=cp("", "", 128))
        assert g1.status(aid)["branch"] == "trunk"
    g3, _ = git(store, status=cp("", "fatal: not a git repository", 128))
    with pytest.raises(AppError, match="git status failed: fatal: not a git repository"):
        g3.status(aid)


def test_init_validates_and_runs(store, app):
    aid = app["id"]
    g, fake = git(store, status=cp("## main\n"))
    with pytest.raises(AppError, match="branch name"):
        g.init(aid, "bad branch!")
    out = g.init(aid, "trunk")
    assert fake.calls[0]["args"] == ["init", "-b", "trunk"] and fake.calls[0]["env"]["GIT_TERMINAL_PROMPT"] == "0" and fake.calls[0]["cwd"] == str(store.root(aid))
    (store.root(aid) / ".git").mkdir()
    with pytest.raises(AppError, match="already a git repository"):
        g.init(aid)
    g2, _ = git(store, init=cp("", "fatal: boom", 1))
    (store.root(aid) / ".git").rmdir()
    with pytest.raises(AppError, match="git init failed: fatal: boom"):
        g2.init(aid)


def test_commit_checks_then_commits(store, app):
    aid = app["id"]
    g, fake = git(store, status=cp("## main\n"), **{"rev-parse": cp("abc123\n")})
    with pytest.raises(AppError, match="commit message"):
        g.commit(aid, "  ")
    with pytest.raises(AppError, match="commit message"):
        g.commit(aid, "x" * 201)
    with pytest.raises(AppError, match="not a git repository yet"):
        g.commit(aid, "first")
    (store.root(aid) / ".git").mkdir()
    store.write(aid, "public/app.js", "k = 'sk-" + "z" * 30 + "'")
    with pytest.raises(AppError, match="not committed: these files look like they contain a secret"):
        g.commit(aid, "first")
    store.write(aid, "public/app.js", "ok")
    with pytest.raises(AppError, match="name and email"):
        g.commit(aid, "first")
    assert not [x for x in fake.calls if x["verb"] == "add"]                                  # nothing was staged before the check
    store.set_config({"git_name": "Ada", "git_email": "ada@example.com"})
    out = g.commit(aid, "first")
    c = fake.last("commit")
    assert out["commit"] == "abc123" and c["args"] == ["commit", "-m", "first"] and "user.name=Ada" in c["opts"] and "user.email=ada@example.com" in c["opts"]
    assert [x["args"][:2] for x in fake.calls if x["verb"] == "add"] == [["add", "-A"]]
    nothing, _ = git(store, commit=cp("nothing to commit, working tree clean", "", 1))
    with pytest.raises(AppError, match="nothing new to commit"):
        nothing.commit(aid, "again")
    failing, _ = git(store, commit=cp("", "fatal: unable", 1))
    with pytest.raises(AppError, match="git commit failed: fatal: unable"):
        failing.commit(aid, "again")
    empty_fail, _ = git(store, commit=cp("", "", 1))
    with pytest.raises(AppError, match="no message"):
        empty_fail.commit(aid, "again")


def test_diff_and_log(store, app):
    aid = app["id"]
    g, fake = git(store, diff=cp("diff --git a b\n"), **{"rev-parse": cp("abc\n")}, log=cp("abc\tAda\t2026-11-01\tfirst\nbroken line\n"))
    assert g.log(aid) == []
    with pytest.raises(AppError, match="not a git repository yet"):
        g.diff(aid)
    (store.root(aid) / ".git").mkdir()
    assert g.diff(aid) == dict(diff="diff --git a b\n", truncated=False) and fake.last("diff")["args"] == ["diff", "HEAD"]
    assert g.log(aid, 500) == [dict(hash="abc", author="Ada", date="2026-11-01", message="first")] and "-100" in fake.last("log")["args"]
    nohead, nf = git(store, diff=cp("d"), **{"rev-parse": cp("", "", 128)})
    assert nohead.diff(aid)["diff"] == "d" and nf.last("diff")["args"] == ["diff"] and nohead.log(aid) == []
    big, _ = git(store, diff=cp("x" * 60001), **{"rev-parse": cp("abc")})
    assert big.diff(aid)["truncated"] is True and len(big.diff(aid)["diff"]) == 60000


def test_remote_validation_and_token_handling(store, app, tmp_path):
    aid = app["id"]
    g, fake = git(store, status=cp("## main\n"), **{"remote": [cp("", "", 1), cp("https://old", "", 0)]})
    with pytest.raises(AppError, match="start a git repository first"):
        g.set_remote(aid, "https://github.com/a/b.git")
    (store.root(aid) / ".git").mkdir()
    for bad in ("", "http://github.com/a/b.git", "https://user:pw@github.com/a/b.git", "https://github.com", "ssh://x/y", "relative/path", str(tmp_path / "nope")):
        with pytest.raises(AppError, match="https address|plain https"):
            g.set_remote(aid, bad)
    with pytest.raises(AppError, match="branch name"):
        g.set_remote(aid, "https://github.com/a/b.git", branch="a b")
    out = g.set_remote(aid, "https://github.com/a/b.git", token="ghp-token-value", branch="dev")
    assert [c["args"] for c in fake.calls if c["verb"] == "remote"][-1] == ["remote", "add", "origin", "https://github.com/a/b.git"]
    assert out["remote"] == "https://github.com/a/b.git" and out["has_token"] is True and "ghp-token-value" not in json.dumps(out) and store.get(aid)["git"] == dict(remote="https://github.com/a/b.git", branch="dev")
    assert "ghp-token-value" not in store.db.one("SELECT git FROM apps WHERE id=?", (aid,))["git"]
    g.set_remote(aid, "https://github.com/a/c.git")
    assert [c["args"] for c in fake.calls if c["verb"] == "remote"][-1][1] == "set-url"
    assert g._info(aid)["remote"] == "https://github.com/a/c.git" and store._secrets()[aid]["git_token"] == "ghp-token-value"
    bare = tmp_path / "bare.git"
    bare.mkdir()
    assert g.set_remote(aid, str(bare), token=None)["remote"] == str(bare)
    with pytest.raises(AppError, match="at most 400"):
        g.set_remote(aid, str(bare), token="x" * 401)
    with pytest.raises(AppError, match="at most 400"):
        g.set_remote(aid, str(bare), token=5)


def test_push_and_pull_need_a_remote_and_a_confirm(store, app):
    aid = app["id"]
    g, fake = git(store, status=cp("## main\n"), remote=[cp("", "", 1), cp()])
    with pytest.raises(AppError, match="connect a remote first"):
        g.push(aid, True)
    (store.root(aid) / ".git").mkdir()
    g.set_remote(aid, "https://github.com/a/b.git", token="tok-s3cret", branch="main")
    with pytest.raises(AppError, match="confirm to go ahead"):
        g.push(aid)
    with pytest.raises(AppError, match="confirm to go ahead"):
        g.push(aid, "yes")
    store.write(aid, "public/app.js", "k='sk-" + "q" * 30 + "'")
    with pytest.raises(AppError, match="not pushed"):
        g.push(aid, True)
    store.write(aid, "public/app.js", "ok")
    out = g.push(aid, True)
    c = fake.last("push")
    assert out["pushed"] and c["args"] == ["push", "-u", "origin", "main"] and c["timeout"] == 120
    hdr = next(o for o in c["opts"] if o.startswith("http.extraheader="))
    assert base64.b64encode(b"x-access-token:tok-s3cret").decode() in hdr and "tok-s3cret" not in json.dumps(out)
    before = store.get(aid)["version"]
    with pytest.raises(AppError, match="confirm to go ahead"):
        g.pull(aid)
    out = g.pull(aid, True)
    assert out["pulled"] and out["version"] == before + 2 and fake.last("pull")["args"] == ["pull", "--ff-only", "origin", "main"]
    assert [v["note"] for v in store.versions(aid)[:2]] == ["pulled from the remote", "before pulling from the remote"]
    failing, _ = git(store, push=cp("", "rejected", 1))
    with pytest.raises(AppError, match="git push failed: rejected"):
        failing.push(aid, True)


def test_a_slow_git_is_reported(store, app):
    g, _ = git(store, status=subprocess.TimeoutExpired("git", 60))
    (store.root(app["id"]) / ".git").mkdir()
    with pytest.raises(AppError, match="did not finish in time"):
        g.status(app["id"])


def test_a_remote_without_a_token_pushes_without_a_header(store, app):
    aid = app["id"]
    g, fake = git(store, status=cp("## main\n"), remote=[cp("", "", 1), cp()])
    (store.root(aid) / ".git").mkdir()
    g.set_remote(aid, "https://github.com/a/b.git")
    g.push(aid, True)
    assert not any(o.startswith("http.extraheader") for o in fake.last("push")["opts"])
    store.set_config({"git_name": "A", "git_email": "a@b.c"})
    store.db.execute("UPDATE apps SET git='{}' WHERE id=?", (aid,))
    g.set_remote(aid, "https://github.com/a/b.git")
    assert g._info(aid)["branch"] == "main"


def test_the_default_runner_runs_a_real_program(store, app, tmp_path):
    import sys
    r = E._run([sys.executable, "-c", "import os;print(os.getcwd())"], str(tmp_path), {}, 20)
    assert r.returncode == 0 and str(tmp_path.name) in r.stdout


def test_deploy_plan(store, app):
    plan = E.deploy_plan(store, app["id"])
    assert plan["image"] == "hello:0.1.0" and [s["title"] for s in plan["steps"]][0] == "Build the image" and "docker run --rm -p 8080:80 hello:0.1.0" == plan["steps"][1]["command"]
    assert any("cannot hold a key" in n for n in plan["notes"]) and any("calls agents" in n or "The app calls agents" in n for n in plan["notes"])
    srv = store.create("S", "python-api")
    store.set_manifest(srv["id"], {"env": [{"name": "TOKEN"}, {"name": "OTHER"}]})
    p2 = E.deploy_plan(store, srv["id"])
    assert "-p 8080:8080" in p2["steps"][1]["command"] and any("TOKEN, OTHER" in n for n in p2["notes"]) and not any("cannot hold a key" in n for n in p2["notes"])
    assert len(E.deploy_plan(store, srv["id"])["notes"]) == 2


# ------------------------------------------------------------------------------------------------ the builder's tools
def tctx(store, **kw):
    events = []
    ctx = ToolContext(None, None, None, None, agent_id="app_builder", session_id="s1", apps=store, on_app=events.append, **kw)
    return ctx, app_tools(ctx), events


def test_the_tools_exist_only_when_apps_do(state, tmp_path):
    names = {"app_agents", "app_list", "app_create", "app_tree", "app_read_file", "app_write_file", "app_patch_file", "app_delete_file", "app_set_manifest", "app_check"}
    assert names <= set(ALL_TOOLS)
    assert not names & {t.__name__ for t in build_tools(ToolContext(tmp_path, None, None, None), list(ALL_TOOLS))}
    assert names <= {t.__name__ for t in build_tools(ToolContext(tmp_path, None, None, None, apps=state.apps), list(ALL_TOOLS))}
    state.apps.set_config({"enabled": False})
    assert not names & {t.__name__ for t in build_tools(ToolContext(tmp_path, None, None, None, apps=state.apps), list(ALL_TOOLS))}
    assert "app_create" in APP_RULES and "static-chat" in APP_RULES


def test_a_builder_creates_and_builds_a_new_app_straight_away(store):
    ctx, t, events = tctx(store)
    out = run(t["app_create"]("Notes", "static-form", "a form", "coder"))
    aid = out["id"]
    assert out["ok"] and out["type"] == "static" and any(f["path"] == "app.json" for f in out["files"]) and events[0]["action"] == "open" and store.get(aid)["mode"] == "simple"
    assert store.get(aid)["session_id"] == "s1"
    w = run(t["app_write_file"]("public/hello.txt", "hi", note="add a file"))
    assert w["ok"] and w["status"] == "applied" and w["version"] == 2 and store.read(aid, "public/hello.txt")["content"] == "hi" and events[-1] == dict(action="applied", app_id=aid, version=2, pending=0)
    assert run(t["app_patch_file"]("public/hello.txt", [{"old": "hi", "new": "hello"}], aid))["status"] == "applied" and store.read(aid, "public/hello.txt")["content"] == "hello"
    assert run(t["app_delete_file"]("public/hello.txt", aid))["status"] == "applied"
    m = run(t["app_set_manifest"]({"agents": [{"id": "coder", "purpose": "p"}], "permissions": {"network": "allowlist", "allow": ["https://api.example.com"]}}))
    assert m["ok"] and store.manifest(aid)["permissions"]["allow"] == ["https://api.example.com"] and store.manifest(aid)["agents"][0]["id"] == "coder"
    tree = run(t["app_tree"]())
    assert tree["ok"] and tree["id"] == aid and any(f["path"] == "public/index.html" for f in tree["files"])
    assert run(t["app_read_file"]("public/index.html"))["content"].startswith("<!doctype html>")
    assert run(t["app_check"]())["ok"] and run(t["app_list"]())["apps"][0]["id"] == aid
    ag = run(t["app_agents"]())
    assert ag["ok"] and {a["id"] for a in ag["agents"]} >= {"engineer", "app_builder"}


def test_an_advanced_app_waits_for_review_and_plan_mode_always_proposes(store):
    a = store.create("Adv", "static-chat")
    ctx, t, events = tctx(store)
    store.update(a["id"], session_id="s1")
    w = run(t["app_write_file"]("public/new.txt", "x", note=""))
    assert w["status"] == "pending" and w["pending_changes"] == 1 and "accept or reject" in w["note"] and events[-1]["action"] == "pending"
    assert "public/new.txt" not in {f["path"] for f in store.tree(a["id"])}
    store.set_config({"auto_apply": True})
    assert run(t["app_write_file"]("public/two.txt", "x"))["status"] == "applied"
    ctx2, t2, _ = tctx(store, can_apply=False)
    assert run(t2["app_write_file"]("public/three.txt", "x"))["status"] == "pending"
    store.update(a["id"], mode="simple")
    assert run(t2["app_delete_file"]("public/two.txt"))["status"] == "pending"


def test_tool_errors_are_reported_not_raised(store):
    ctx, t, _ = tctx(store)
    ctx.session_id = ""
    for call in (t["app_tree"](), t["app_read_file"]("a"), t["app_check"](), t["app_write_file"]("a", "b"), t["app_set_manifest"]({})):
        out = run(call)
        assert out["ok"] is False and "no app in this chat yet" in out["error"]
    assert run(t["app_create"]("x", "nope"))["ok"] is False
    a = store.create("E", "static-chat", session_id="s9")
    ctx.session_id = "s9"
    assert run(t["app_read_file"]("missing.txt"))["error"].startswith("no such file")
    assert run(t["app_write_file"]("../x", "b"))["ok"] is False and run(t["app_set_manifest"]("nope"))["error"] == "patch must be an object"
    assert run(t["app_set_manifest"]({"port": 5}))["ok"] is False
    ctx.apps.agents = None
    assert run(t["app_agents"]()) == dict(ok=True, agents=[], note=run(t["app_agents"]())["note"])
    ctx.on_app = None
    assert run(t["app_write_file"]("public/x.txt", "x", a["id"]))["ok"]


# ------------------------------------------------------------------------------------------------ the REST API
def test_config_templates_builder_and_server(client, state):
    assert client.get("/api/apps/config").json()["enabled"] is True and client.put("/api/apps/config", json={"max_apps": 9}).json()["max_apps"] == 9
    assert client.put("/api/apps/config", json={"max_apps": 0}).status_code == 422
    assert {t["id"] for t in client.get("/api/apps/templates").json()} == {"static-chat", "static-form", "static-dashboard", "static-table", "node-api", "python-api"}
    assert client.get("/api/apps/builder").json() == dict(id="app_builder", available=True, name="App Builder")
    assert client.get("/api/apps/server").json()["running"] is False


def test_the_whole_life_of_an_app_over_the_api(client, state):
    c = client
    a = c.post("/api/apps", json={"name": "Over API", "template": "static-chat", "agent_id": "engineer"}).json()
    aid = a["id"]
    assert c.get("/api/apps").json()[0]["id"] == aid and c.get(f"/api/apps/{aid}").json()["name"] == "Over API" and c.get("/api/apps/zzzzzzzzzzzz").status_code == 404
    assert c.post("/api/apps", json={"template": "nope"}).status_code == 422
    assert c.put(f"/api/apps/{aid}", json={"name": 5}).status_code == 422 and c.put(f"/api/apps/{aid}", json={"name": "Renamed", "mode": "simple"}).json()["mode"] == "simple"
    # files
    assert any(f["path"] == "public/app.js" for f in c.get(f"/api/apps/{aid}/files").json())
    assert c.put(f"/api/apps/{aid}/file", json={"path": "public/x.txt", "content": "hi"}).json()["content"] == "hi"
    assert c.get(f"/api/apps/{aid}/file", params={"path": "public/x.txt"}).json()["size"] == 2 and c.get(f"/api/apps/{aid}/file", params={"path": "nope"}).status_code == 404
    assert c.put(f"/api/apps/{aid}/file", json={"path": "../x", "content": "x"}).status_code == 422
    assert c.delete(f"/api/apps/{aid}/file", params={"path": "public/x.txt"}).json() == {"deleted": "public/x.txt"}
    # attachments
    assert c.post(f"/api/apps/{aid}/attachments", json={"name": "spec.md", "text": "# hi"}).json()["path"] == "assets/spec.md"
    png = base64.b64encode(b"\x89PNG\x00").decode()
    assert c.post(f"/api/apps/{aid}/attachments", json={"name": "m.png", "base64": png}).json()["size"] == 5
    assert c.post(f"/api/apps/{aid}/attachments", json={"name": "m.png", "base64": "***"}).status_code == 422 and c.post(f"/api/apps/{aid}/attachments", json={"name": "m.png"}).status_code == 422
    # manifest, check, env, key
    assert c.get(f"/api/apps/{aid}/manifest").json()["agents"][0]["id"] == "engineer"
    assert c.put(f"/api/apps/{aid}/manifest", json={"env": [{"name": "TOKEN"}]}).json()["env"][0]["name"] == "TOKEN" and c.put(f"/api/apps/{aid}/manifest", json={"port": 1}).status_code == 422
    assert c.get(f"/api/apps/{aid}/check").json()["ok"] is True
    assert c.put(f"/api/apps/{aid}/env", json={"TOKEN": "abc"}).json()[0]["set"] is True and "abc" not in c.get(f"/api/apps/{aid}/env").text and c.put(f"/api/apps/{aid}/env", json={"NOPE": "x"}).status_code == 422
    assert c.get(f"/api/apps/{aid}/key").json()["exists"] is False
    rot = c.post(f"/api/apps/{aid}/key/rotate").json()
    assert rot["key"].startswith("asx_") and rot["agents"] == ["engineer"] and "key" not in c.get(f"/api/apps/{aid}/key").json()
    assert c.post(f"/api/apps/{aid}/key/revoke").json()["revoked"] is True and c.post("/api/apps/zzzzzzzzzzzz/key/revoke").status_code == 404
    # proposals
    state.apps.propose(aid, [{"op": "write", "path": "p1.txt", "content": "1"}], author="agent:A")
    state.apps.propose(aid, [{"op": "write", "path": "p2.txt", "content": "2"}], author="agent:A")
    state.apps.propose(aid, [{"op": "write", "path": "p3.txt", "content": "3"}], author="agent:A")
    pend = c.get(f"/api/apps/{aid}/proposals").json()
    assert len(pend) == 3 and pend[0]["changes"][0]["new"] == "1"
    assert c.post(f"/api/apps/{aid}/proposals/{pend[0]['id']}/accept").json()["app"]["pending"] == 2
    assert c.post(f"/api/apps/{aid}/proposals/{pend[1]['id']}/reject").json() == {"rejected": True} and c.post(f"/api/apps/{aid}/proposals/nope/accept").status_code == 404
    assert c.post(f"/api/apps/{aid}/accept-all").json()["accepted"] == 1
    state.apps.propose(aid, [{"op": "write", "path": "p4.txt", "content": "4"}])
    assert c.post(f"/api/apps/{aid}/reject-all").json() == {"rejected": 1}
    # versions
    v = c.get(f"/api/apps/{aid}/versions").json()
    assert v[0]["version"] >= 3 and c.post(f"/api/apps/{aid}/versions", json={"note": "keep"}).json()["version"] == v[0]["version"] + 1 and c.post(f"/api/apps/{aid}/versions", json={}).status_code == 200
    d = c.get(f"/api/apps/{aid}/versions/1/diff").json()
    assert {x["path"] for x in d} >= {"p1.txt", "p3.txt"} and c.get(f"/api/apps/{aid}/versions/99/diff").status_code == 422
    assert c.post(f"/api/apps/{aid}/restore", json={"version": 1}).json()["app"]["id"] == aid and c.post(f"/api/apps/{aid}/restore", json={"version": "x"}).status_code == 422
    # duplicate, delete
    cp_ = c.post(f"/api/apps/{aid}/duplicate").json()
    assert cp_["name"].endswith("copy") and c.delete(f"/api/apps/{cp_['id']}").status_code == 422
    assert c.delete(f"/api/apps/{cp_['id']}", params={"confirm": True}).json() == {"deleted": True} and c.delete(f"/api/apps/{cp_['id']}", params={"confirm": True}).status_code == 404


def test_running_exporting_and_planning_over_the_api(client, state, monkeypatch):
    aid = client.post("/api/apps", json={"name": "Run Me", "template": "static-chat", "agent_id": "engineer"}).json()["id"]
    monkeypatch.setattr(state.apps_server, "ensure", lambda port=None: "http://127.0.0.1:9999")
    monkeypatch.setattr(state.apps_server, "url", lambda: "http://127.0.0.1:9999")
    out = client.post(f"/api/apps/{aid}/run").json()
    assert out["url"] == f"http://127.0.0.1:9999/apps-run/{aid}/" and state.app_runner.api_url == "http://testserver"
    assert client.get(f"/api/apps/{aid}/status").json()["status"] == "ready" and client.post(f"/api/apps/{aid}/stop").json() == {"status": "ready"} and "no process" in client.get(f"/api/apps/{aid}/logs").json()["logs"]
    monkeypatch.setattr(state.apps_server, "ensure", lambda port=None: (_ for _ in ()).throw(RuntimeError("port in use")))
    r = client.post(f"/api/apps/{aid}/run")
    assert r.status_code == 503 and "port in use" in r.json()["detail"]
    z = client.get(f"/api/apps/{aid}/export.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip" and 'run-me.zip' in z.headers["content-disposition"] and "run-me/app.json" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    state.apps.write(aid, "public/app.js", "k='sk-" + "n" * 30 + "'")
    assert client.get(f"/api/apps/{aid}/export.zip").status_code == 422
    plan = client.get(f"/api/apps/{aid}/deploy-plan").json()
    assert plan["image"] == "run-me:0.1.0" and client.get("/api/apps/zzzzzzzzzzzz/deploy-plan").status_code == 404
    for path in ("run", "stop"):
        assert client.post(f"/api/apps/zzzzzzzzzzzz/{path}").status_code == 404
    assert client.get("/api/apps/zzzzzzzzzzzz/status").status_code == 404 and client.get("/api/apps/zzzzzzzzzzzz/logs").status_code == 404


def test_deleting_an_app_stops_it_first(client, state, monkeypatch):
    aid = client.post("/api/apps", json={"name": "Bye", "template": "static-chat"}).json()["id"]
    stopped = []
    monkeypatch.setattr(state.app_runner, "stop", lambda i: stopped.append(i))
    assert client.delete(f"/api/apps/{aid}", params={"confirm": True}).json() == {"deleted": True} and stopped == [aid]
    aid2 = client.post("/api/apps", json={"name": "Bye2", "template": "static-chat"}).json()["id"]
    monkeypatch.setattr(state.app_runner, "stop", lambda i: (_ for _ in ()).throw(AppError("docker is gone")))
    assert client.delete(f"/api/apps/{aid2}", params={"confirm": True}).json() == {"deleted": True}
    assert client.delete("/api/apps/zzzzzzzzzzzz", params={"confirm": True}).status_code == 404


def test_git_over_the_api(client, state, monkeypatch):
    fake = FakeGit(status=cp("## main\n"), **{"rev-parse": cp("abc\n")}, diff=cp("d"), log=cp("abc\tA\t2026-01-01\tm\n"), remote=[cp("", "", 1), cp()])
    monkeypatch.setattr(E, "_run", fake)
    monkeypatch.setattr(E.shutil, "which", lambda n: "/usr/bin/git")
    aid = client.post("/api/apps", json={"name": "G", "template": "static-chat"}).json()["id"]
    assert client.get(f"/api/apps/{aid}/git").json()["initialised"] is False
    client.put("/api/apps/config", json={"git_name": "A", "git_email": "a@b.c"})
    assert client.post(f"/api/apps/{aid}/git/init", json={}).status_code == 200
    (state.apps.root(aid) / ".git").mkdir()
    assert client.get(f"/api/apps/{aid}/git").json()["initialised"] and client.get(f"/api/apps/{aid}/git/diff").json()["diff"] == "d" and client.get(f"/api/apps/{aid}/git/log").json()[0]["message"] == "m"
    assert client.post(f"/api/apps/{aid}/git/commit", json={"message": "first"}).json()["commit"] == "abc" and client.post(f"/api/apps/{aid}/git/commit", json={}).status_code == 422
    assert client.put(f"/api/apps/{aid}/git/remote", json={"url": "https://github.com/a/b.git", "token": "t"}).json()["remote"] == "https://github.com/a/b.git"
    assert client.put(f"/api/apps/{aid}/git/remote", json={"url": "nope"}).status_code == 422
    assert client.post(f"/api/apps/{aid}/git/push", json={}).status_code == 422 and client.post(f"/api/apps/{aid}/git/push", json={"confirm": True}).json()["pushed"] is True
    assert client.post(f"/api/apps/{aid}/git/pull", json={}).status_code == 422 and client.post(f"/api/apps/{aid}/git/pull", json={"confirm": True}).json()["pulled"] is True


def test_the_event_stream_sends_events_keepalives_and_stops_when_the_client_leaves(store, app):
    class Req:
        def __init__(self):
            self.gone = False

        async def is_disconnected(self):
            return self.gone

    async def go():
        req, out = Req(), []
        gen = event_stream(store, app["id"], req, keepalive=0.05)
        out.append(await gen.__anext__())
        store._emit(app["id"], {"type": "files"})
        out.append(await gen.__anext__())
        out.append(await gen.__anext__())
        req.gone = True
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        assert out[0].startswith(": connected") and json.loads(out[1][6:]) == {"type": "files"} and out[2].startswith(": keepalive") and not store._subs[app["id"]]
    run(go())


def test_the_events_endpoint(client, state):
    from app.apps.api import register
    aid = client.post("/api/apps", json={"name": "Live", "template": "static-chat"}).json()["id"]
    fa = FastAPI()
    fa.state.s = state
    register(fa)
    endpoint = next(x.endpoint for x in fa.routes[-1].original_router.routes if x.path == "/api/apps/{aid}/events")

    class Req:
        app = SimpleNamespace(state=SimpleNamespace(s=state))

        async def is_disconnected(self):
            return True

    async def go():
        resp = await endpoint(aid, Req())
        assert resp.media_type == "text/event-stream" and resp.headers["cache-control"] == "no-cache"
        assert (await resp.body_iterator.__anext__()).startswith(": connected")
        await resp.body_iterator.aclose()
    run(go())
    assert client.get("/api/apps/zzzzzzzzzzzz/events").status_code == 404


# ------------------------------------------------------------------------------------------------ the rest of the app
def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_a_real_turn_builds_an_app_with_the_builder_agent(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    msg = ('!tool app_create {"name": "Greeter", "template": "static-form", "description": "says hi", "agent_id": "engineer"}\n'
           '!tool app_write_file {"path": "public/hello.txt", "content": "hello"}')
    r = client.post("/api/chat/stream", json={"provider": "mock", "agent": "app_builder", "message": msg})
    events = [e for e in sse(r.text) if e.get("type") == "app"]
    assert [e["action"] for e in events] == ["open", "applied"]
    [a] = state.apps.list()
    assert a["name"] == "Greeter" and a["session_id"] and a["agents"] == ["engineer"] and state.apps.read(a["id"], "public/hello.txt")["content"] == "hello"
    plain = client.post("/api/chat", json={"provider": "mock", "agent": "app_builder", "message": "hello"})
    assert "## Apps" in plain.json()["runs"][0]["instruction"]
    state.db.set_setting("harness_policy", json.dumps({"permission_mode": "plan"}))
    client.post("/api/chat", json={"provider": "mock", "agent": "app_builder", "message": '!tool app_write_file {"path": "public/planned.txt", "content": "x", "app_id": "' + a["id"] + '"}'})
    assert "public/planned.txt" not in {f["path"] for f in state.apps.tree(a["id"])} and state.apps.get(a["id"])["pending"] == 1
    client.post("/api/chat", json={"provider": "mock", "agent": "app_builder", "message": '!tool app_create {"name": "Blocked", "template": "static-chat"}'})
    assert len(state.apps.list()) == 1                                                       # creating an app is a write: plan mode refuses it


def test_the_bom_counts_apps_without_names(client, state):
    from app.aibom.builder import build_bom
    state.apps.create("Secret Project Name", "node-api", agent_id="engineer")
    for level in ("public", "internal", "full"):
        bom = build_bom(state, level)
        svc = next(x for x in bom["services"] if x["bom-ref"] == "apps")
        props = {p["name"].split(":")[-1]: p["value"] for p in svc["properties"]}
        assert props["apps"] == "1" and props["enabled"] == "true" and props["agents-called"] == "1" and "Secret Project Name" not in json.dumps(bom)
    state.apps = None
    assert not any(x["bom-ref"] == "apps" for x in build_bom(state, "public")["services"])


def test_settings_for_apps_are_registered_exported_and_imported(client, state):
    reg = {s["key"] for s in client.get("/api/settings/registry").json()["settings"]}
    assert {"apps_config", "apps_secrets", "apps_token_secret"} <= reg
    client.put("/api/apps/config", json={"max_files": 77})
    exported = client.get("/api/settings/export").json()
    assert exported["settings"]["apps_config"]["max_files"] == 77 and "apps_secrets" not in json.dumps(exported) and "apps_token_secret" not in json.dumps(exported)
    client.put("/api/apps/config", json={"max_files": 99})
    r = client.post("/api/settings/import", json={"data": exported, "dry_run": False}).json()
    assert not [e for e in r["errors"] if e["key"] == "apps_config"] and state.apps.config()["max_files"] == 77
    bad = {"format": "agent-studio-settings", "settings": {"apps_config": {"max_files": 1}}}
    assert client.post("/api/settings/import", json={"data": bad, "dry_run": False}).json()["errors"][0]["key"] == "apps_config"


def test_app_events_in_a_chat_become_ag_ui_activity():
    from app.agui import AguiTranslator
    out = AguiTranslator("th", "r1").feed({"type": "app", "action": "applied", "app_id": "a1", "version": 2, "pending": 0, "seq": 3})
    assert any(getattr(e, "activity_type", "") == "app" for e in out)


def test_shutting_down_stops_the_app_containers_and_the_preview_server(state, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app
    stopped = []
    monkeypatch.setattr(state.app_runner, "stop_all", lambda: stopped.append("containers"))
    monkeypatch.setattr(state.apps_server, "stop", lambda: stopped.append("preview"))
    with TestClient(create_app(state)):
        pass
    assert stopped == ["containers", "preview"]
