"""The smarter builder over HTTP: the REST endpoints, what the preview reports back, the read-only data proxy and the preview server's new routes."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import types

import pytest
from fastapi.testclient import TestClient

from app.apiaccess.keys import ApiKeys
from app.apps import runner as R
from app.apps.server import create_apps_app
from app.apps.store import AppStore
from app.apps.studio_js import STUDIO_JS
from app.datasources.service import DataService
from app.db import Database


def run(c):
    return asyncio.run(c)


@pytest.fixture
def sqlite_file(tmp_path):
    f = tmp_path / "sales.db"
    c = sqlite3.connect(f)
    c.execute("CREATE TABLE orders(region TEXT, total INTEGER)")
    c.executemany("INSERT INTO orders VALUES(?,?)", [("n", 1), ("s", 2), ("e", 3)])
    c.commit()
    c.close()
    return f


@pytest.fixture
def made(client):
    return client.post("/api/apps", json={"name": "Board", "template": "static-dashboard", "agent_id": "engineer", "mode": "simple"}).json()


# ------------------------------------------------------------------------------------------------ catalogues and the model
def test_themes_components_and_the_model_list(client, state, monkeypatch):
    assert len(client.get("/api/apps/themes").json()["themes"]) == 8 and len(client.get("/api/apps/components").json()) == 10
    prov = lambda i, kind, ready=True: types.SimpleNamespace(id=i, label=i, kind=kind, enabled=True, pro_model=f"{i}-pro", public=lambda: {"auth_ready": ready})       # noqa: E731
    monkeypatch.setattr(state.providers, "list", lambda: [prov("local", "ollama"), prov("oa", "openai", ready=False), prov("an", "anthropic")])
    m = client.get("/api/apps/models").json()
    assert [o["provider"] for o in m["options"]] == ["an", "oa", "local"] and m["best"]["provider"] == "an" and m["best"]["model"]


def test_the_model_for_one_app(client, state, made, monkeypatch):
    aid = made["id"]
    prov = types.SimpleNamespace(id="an", label="an", kind="anthropic", enabled=True, pro_model="big", public=lambda: {"auth_ready": True})
    monkeypatch.setattr(state.providers, "list", lambda: [prov])
    first = client.get(f"/api/apps/{aid}/model").json()
    assert first["saved"]["mode"] == "best" and first["effective"]["provider"] == "an" and first["effective"]["source"] == "best"
    r = client.put(f"/api/apps/{aid}/model", json={"mode": "custom", "provider": "an", "model": "small"}).json()
    assert r["saved"]["model"] == "small" and r["effective"] == dict(provider="an", label="an", kind="anthropic", model="small", source="custom")
    assert client.put(f"/api/apps/{aid}/model", json={"mode": "custom", "provider": "ghost"}).status_code == 422
    assert client.put(f"/api/apps/{aid}/model", json={"mode": "x"}).status_code == 422
    assert client.put(f"/api/apps/{aid}/model", json={"mode": "best"}).json()["saved"]["mode"] == "best"
    assert client.get("/api/apps/" + "0" * 12 + "/model").status_code == 404


# ------------------------------------------------------------------------------------------------ plan, verify, undo, compare
def test_plan_verify_undo_and_compare_endpoints(client, state, made):
    aid = made["id"]
    assert client.get(f"/api/apps/{aid}/plan").json() == {"plan": None}
    state.apps.set_plan(aid, {"title": "T", "steps": ["a", "b"]})
    assert client.get(f"/api/apps/{aid}/plan").json()["plan"]["steps"][0]["title"] == "a"
    assert client.delete(f"/api/apps/{aid}/plan").json() == {"plan": None} and client.get(f"/api/apps/{aid}/plan").json() == {"plan": None}
    assert client.get(f"/api/apps/{aid}/console").json() == {"loaded": False, "errors": []}
    state.apps.add_console(aid, "error", "boom", "index.html", "app.js", 4)
    v = client.get(f"/api/apps/{aid}/verify").json()
    assert v["ok"] is False and v["round"] == 0 and "boom" in v["report"]
    assert client.post(f"/api/apps/{aid}/verify").json()["round"] == 1 and client.post(f"/api/apps/{aid}/verify").json()["round"] == 2
    assert client.post(f"/api/apps/{aid}/verify/reset").json() == {"round": 0}
    state.apps.write(aid, "public/app.js", "// changed")
    state.apps.snapshot(aid, "changed", "user")
    c = client.get(f"/api/apps/{aid}/versions/compare", params={"a": 1, "b": 2}).json()
    assert [f["path"] for f in c] == ["public/app.js"]
    assert client.get(f"/api/apps/{aid}/versions/compare", params={"a": 1, "b": 99}).status_code == 422
    u = client.post(f"/api/apps/{aid}/undo").json()
    assert u["version"] == 3 and "// changed" not in state.apps.read(aid, "public/app.js")["content"]
    assert client.post("/api/apps/" + "0" * 12 + "/undo").status_code == 404


# ------------------------------------------------------------------------------------------------ look, parts and connections
def test_theme_components_and_connection_endpoints(client, state, made, sqlite_file):
    aid = made["id"]
    assert client.get(f"/api/apps/{aid}/theme").json()["theme"] == "warm"
    t = client.put(f"/api/apps/{aid}/theme", json={"theme": "rose", "font": "serif", "radius": "sharp"}).json()
    assert t["theme"] == "rose" and t["version"] == 2 and client.put(f"/api/apps/{aid}/theme", json={"theme": "zzz"}).status_code == 422
    assert client.put(f"/api/apps/{aid}/theme", json={}).json()["theme"] == "warm"
    chat = client.post("/api/apps", json={"name": "Chat", "template": "static-chat"}).json()["id"]
    c = client.post(f"/api/apps/{chat}/components", json={"names": ["tabs", "modal"]}).json()
    assert set(c["usage"]) == {"tabs", "modal"} and client.post(f"/api/apps/{chat}/components", json={"names": ["zzz"]}).status_code == 422
    assert client.post(f"/api/apps/{chat}/components", json={}).status_code == 422

    conns = client.get(f"/api/apps/{chat}/connections").json()
    assert "engineer" in {a["id"] for a in conns["agents"]} and conns["data"] == []
    r = client.post(f"/api/apps/{chat}/connect/agent", json={"id": "engineer", "purpose": "helps"}).json()
    assert r["agent"] == "engineer" and state.apps.manifest(chat)["agents"][0]["purpose"] == "helps"
    quiet = next(a for a in state.agents.list() if not a.api_access and a.id != "app_builder")
    off = client.post(f"/api/apps/{chat}/connect/agent", json={"id": quiet.id}).json()
    assert off["api_access"] is False and off["enabled_now"] is False
    on = client.post(f"/api/apps/{chat}/connect/agent", json={"id": quiet.id, "enable_api": True}).json()
    assert on["enabled_now"] is True and state.agents.get(quiet.id).api_access is True
    assert client.post(f"/api/apps/{chat}/connect/agent", json={"id": "ghost"}).status_code == 404
    assert client.delete(f"/api/apps/{chat}/connect/agent/{quiet.id}").json()["version"] > 0 and client.delete(f"/api/apps/{chat}/connect/agent/{quiet.id}").status_code == 422

    state.data.set_source({"id": "sales", "kind": "sqlite", "config": {"path": str(sqlite_file)}})
    assert client.post(f"/api/apps/{chat}/connect/data", json={"id": "sales", "purpose": "totals"}).json()["source"] == "sales"
    assert client.get(f"/api/apps/{chat}/connections").json()["data"][0]["connected"] is True
    assert client.post(f"/api/apps/{chat}/connect/data", json={"id": "ghost"}).status_code == 422
    assert client.delete(f"/api/apps/{chat}/connect/data/sales").json()["version"] > 0 and client.delete(f"/api/apps/{chat}/connect/data/sales").status_code == 422


# ------------------------------------------------------------------------------------------------ the runner: reports and data
class Agents:
    def __init__(self, *ids):
        self.items = {i: types.SimpleNamespace(id=i, name=i, description="", api_access=True) for i in ids}

    def get(self, i):
        return self.items.get(i)

    def list(self):
        return list(self.items.values())


@pytest.fixture
def now():
    return types.SimpleNamespace(t=1_000_000.0)


@pytest.fixture
def rig(tmp_path, now, sqlite_file):
    db = Database(tmp_path / "t.db")
    store = AppStore(db, home=tmp_path / "home", keys=ApiKeys(db), agents=Agents("coder"))
    store.data = DataService(db)
    store.data.set_source({"id": "sales", "kind": "sqlite", "config": {"path": str(sqlite_file)}})
    runner = R.AppRunner(store, home=tmp_path / "home", clock=lambda: now.t, frame_ancestors=["http://127.0.0.1:3100"])
    runner.data = store.data
    app = store.create("Dash", "static-dashboard", "", "coder")
    store.connect_data(app["id"], "sales")
    return store, runner, app["id"]


def post(runner, app_id, **body):
    return runner.record_console(app_id, runner.token(app_id), json.dumps(body).encode())


def test_every_page_gets_the_studio_script_and_the_script_itself_is_served(rig):
    store, runner, aid = rig
    code, h, body = runner.serve(aid, "", "http://127.0.0.1:8767")
    assert code == 200 and f'<script src="http://127.0.0.1:8767/apps-run/{aid}/_studio.js"></script>' in body.decode() and body.decode().index("AGENT_STUDIO") < body.decode().index("_studio.js")
    store.write(aid, "public/other.html", "<head></head><p>two</p>")
    assert "_studio.js" in runner.serve(aid, "other.html", "http://x")[2].decode()
    code, h, js = runner.serve(aid, "_studio.js", "http://x")
    assert code == 200 and js.decode() == STUDIO_JS and h["Content-Type"].startswith("text/javascript") and h["Cache-Control"] == "no-store"
    assert runner.serve(aid, "sub/_studio.js", "http://x")[2].decode() == STUDIO_JS
    assert runner.serve("0" * 12, "_studio.js", "http://x")[0] == 404
    assert "_studio.js" not in runner.serve(aid, "app.js", "http://x")[2].decode()


def test_the_page_reports_its_errors_and_only_with_a_valid_token(rig, now):
    store, runner, aid = rig
    assert post(runner, aid, kind="ready", text="loaded") == (204, {})
    assert post(runner, aid, kind="error", text="x is not defined", page="index.html", source="app.js", line=9) == (204, {})
    err = store.console(aid)["errors"][0]
    assert err["text"] == "x is not defined" and err["source"] == "app.js" and err["line"] == 9 and store.console(aid)["loaded"] is True
    post(runner, aid, kind="resource", text="failed to load x.js", line="not a number")
    assert store.console(aid)["errors"][1]["line"] == 0
    assert runner.record_console(aid, "bad", b"{}")[0] == 401
    assert runner.record_console(aid, runner.token(aid), b"x" * 5000)[0] == 413
    for bad in (b"not json", b'["x"]', b'{"kind":"weird","text":"x"}', b'{}'):
        assert runner.record_console(aid, runner.token(aid), bad)[0] == 400
    assert runner.record_console("0" * 12, runner.token("0" * 12), b'{"kind":"error"}')[0] == 400
    now.t += R.TOKEN_TTL + 5
    assert runner.record_console(aid, "1.x", b"{}")[0] == 401


def q(runner, aid, source="sales", token=None, **body):
    return run(runner.data_query(aid, runner.token(aid) if token is None else token, source, json.dumps(body).encode()))


def test_a_page_may_read_only_the_data_sources_its_manifest_lists(rig, now, tmp_path):
    store, runner, aid = rig
    code, out = q(runner, aid, sql="SELECT region, total FROM orders ORDER BY total")
    assert code == 200 and out["columns"] == ["region", "total"] and out["rows"][0] == ["n", 1] and out["row_count"] == 3
    assert q(runner, aid, sql="SELECT * FROM orders", max_rows=2)[1]["row_count"] == 2 and q(runner, aid, sql="SELECT * FROM orders", max_rows=True)[0] == 200
    audit = store.data.audit("sales")
    assert audit[0]["agent"] == "app:dash" and audit[0]["ok"] == 1
    assert q(runner, aid, sql="DELETE FROM orders")[0] == 422 and "read" in q(runner, aid, sql="DROP TABLE orders")[1]["error"]["message"].lower()
    c, e = q(runner, aid, "other", sql="SELECT 1")
    assert c == 403 and "sales" in e["error"]["message"]
    assert q(runner, aid, sql="")[0] == 400 and q(runner, aid, sql=5)[0] == 400
    assert run(runner.data_query(aid, runner.token(aid), "sales", b"not json"))[0] == 400
    assert run(runner.data_query(aid, runner.token(aid), "sales", b'["x"]'))[0] == 400
    assert q(runner, aid, token="bad", sql="SELECT 1")[0] == 401
    assert run(runner.data_query(aid, runner.token(aid), "sales", b"x" * (R.MAX_DATA_BODY + 1)))[0] == 413
    nothing = store.create("None", "static-chat", "", "coder")["id"]
    c, e = q(runner, nothing, sql="SELECT 1")
    assert c == 403 and "no data sources" in e["error"]["message"]
    runner.data = None
    assert q(runner, aid, sql="SELECT 1")[0] == 503
    runner.data = store.data
    for _ in range(R.DATA_PER_MIN - 3):
        q(runner, aid, sql="SELECT 1")
    c, e = q(runner, aid, sql="SELECT 1")
    assert c == 429 and "at most 60" in e["error"]["message"]
    now.t += 61
    assert q(runner, aid, sql="SELECT 1")[0] == 200


# ------------------------------------------------------------------------------------------------ the preview server routes
def test_the_preview_server_routes_for_reports_and_data(rig):
    store, runner, aid = rig
    c = TestClient(create_apps_app(runner), base_url="http://127.0.0.1:8767")
    tok = runner.token(aid)
    pre = c.options(f"/apps-run/{aid}/_console")
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "null" and "X-App-Token" in pre.headers["access-control-allow-headers"]
    ok = c.post(f"/apps-run/{aid}/_console", content=json.dumps({"kind": "error", "text": "boom"}), headers={"X-App-Token": tok})
    assert ok.status_code == 204 and ok.content == b"" and store.console(aid)["errors"][0]["text"] == "boom"
    assert c.post(f"/apps-run/{aid}/_console", content="{}", headers={"X-App-Token": tok}).status_code == 400
    assert c.post(f"/apps-run/{aid}/_console", content="{}").json() == {"error": "this preview has expired"}
    js = c.get(f"/apps-run/{aid}/_studio.js")
    assert js.status_code == 200 and js.text == STUDIO_JS and "asPicked" in js.text and js.headers["access-control-allow-origin"] == "null"

    dpre = c.options(f"/apps-run/{aid}/_data/sales")
    assert dpre.status_code == 204 and "POST" in dpre.headers["access-control-allow-methods"]
    good = c.post(f"/apps-run/{aid}/_data/sales", content=json.dumps({"sql": "SELECT region FROM orders"}), headers={"X-App-Token": tok})
    assert good.status_code == 200 and good.json()["rows"] == [["n"], ["s"], ["e"]] and good.headers["cache-control"] == "no-store" and good.headers["access-control-allow-origin"] == "null"
    bad = c.post(f"/apps-run/{aid}/_data/sales", content=json.dumps({"sql": "DELETE FROM orders"}), headers={"X-App-Token": tok})
    assert bad.status_code == 422 and bad.json()["error"]["type"] == "app_data_error"
    assert c.post(f"/apps-run/{aid}/_data/sales", content="{}").status_code == 401

    evil = TestClient(create_apps_app(runner), base_url="http://evil.example.com")
    assert evil.post(f"/apps-run/{aid}/_console", content="{}").status_code == 404 and evil.options(f"/apps-run/{aid}/_console").status_code == 404
    assert evil.post(f"/apps-run/{aid}/_data/sales", content="{}").status_code == 404 and evil.options(f"/apps-run/{aid}/_data/sales").status_code == 404


def test_local_scripts_are_served_with_crossorigin_so_their_errors_are_readable(rig):
    store, runner, aid = rig
    store.write(aid, "public/index.html", '<head></head><script src="a.js"></script><script crossorigin="use-credentials" src="b.js"></script><script type="module" src="c.js"></script><script>1</script>')
    html = runner.serve(aid, "", "http://x")[2].decode()
    assert '<script crossorigin="anonymous" src="a.js">' in html and 'crossorigin="use-credentials" src="b.js"' in html and html.count('crossorigin="anonymous"') == 2
    assert '<script crossorigin="anonymous" type="module" src="c.js">' in html and "<script>1</script>" in html
