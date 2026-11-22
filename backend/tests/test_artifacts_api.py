"""Artifacts end to end: the REST API (and its safe serving rules), the save_artifact tool, a real agent turn that produces files, the AI-BOM and settings import."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.harness.tools import ToolContext, build_tools
from app.main import State, create_app


def run(c):
    return asyncio.run(c)


@pytest.fixture
def api(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    return client


def save(c, name, content, **kw):
    r = c.post("/api/artifacts", json={"name": name, "content": content, **kw})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ the REST API
def test_save_list_get_preview_and_delete(api):
    a = save(api, "notes.md", "# Plan\nship it", agent="engineer", tags=["plan"], session_id="s1", trace_id="t9")
    assert a["source"] == "chat" and a["tags"] == ["plan"] and a["trace_id"] == "t9"
    assert api.get("/api/artifacts").json()["total"] == 1 and api.get("/api/artifacts", params={"q": "ship"}).json()["items"][0]["id"] == a["id"]
    assert api.get("/api/artifacts", params={"q": "ship", "type": "image"}).json()["total"] == 0
    assert api.get(f"/api/artifacts/{a['id']}").json()["name"] == "notes.md"
    assert api.get(f"/api/artifacts/{a['id']}/preview").json() == {"kind": "markdown", "text": "# Plan\nship it", "truncated": False}
    f = api.get("/api/artifacts/facets").json()
    assert f["total"] == 1 and f["agents"] == [{"value": "engineer", "count": 1}]
    assert api.post(f"/api/artifacts/{a['id']}/resync").json()["copies"][0]["status"] == "synced"
    assert api.delete(f"/api/artifacts/{a['id']}").json()["deleted"] is True
    for call in (lambda: api.get(f"/api/artifacts/{a['id']}"), lambda: api.get(f"/api/artifacts/{a['id']}/preview"), lambda: api.get(f"/api/artifacts/{a['id']}/content"),
                 lambda: api.delete(f"/api/artifacts/{a['id']}"), lambda: api.post(f"/api/artifacts/{a['id']}/resync")):
        assert call().status_code == 404


def test_saving_needs_content_and_a_default_name(api):
    assert api.post("/api/artifacts", json={"content": ""}).status_code == 422
    r = api.post("/api/artifacts", json={"content": "x", "tags": "not a list"})
    assert r.status_code == 200 and r.json()["name"] == "note.md" and r.json()["tags"] == []


def test_files_are_served_so_a_browser_can_never_run_them(api):
    ids = {n: save(api, n, c)["id"] for n, c in (("page.html", "<script>alert(1)</script>"), ("data.json", '{"a": 1}'), ("pic.png", "PNG"), ("doc.pdf", "%PDF"), ("art.svg", "<svg/>"),
                                                  ("pack.zip", "PK"), ("notes.md", "# hi"), ("odd name é.txt", "x"))}
    page = api.get(f"/api/artifacts/{ids['page.html']}/content")
    assert page.headers["content-type"].startswith("text/plain") and page.headers["x-content-type-options"] == "nosniff" and page.headers["content-disposition"].startswith("inline;")
    assert "sandbox" in page.headers["content-security-policy"] and page.text == "<script>alert(1)</script>"
    assert api.get(f"/api/artifacts/{ids['pic.png']}/content").headers["content-type"] == "image/png"
    svg = api.get(f"/api/artifacts/{ids['art.svg']}/content")
    assert svg.headers["content-type"] == "image/svg+xml" and svg.headers["content-security-policy"].startswith("sandbox")
    pdf = api.get(f"/api/artifacts/{ids['doc.pdf']}/content")
    assert pdf.headers["content-type"] == "application/pdf" and "object-src 'self'" in pdf.headers["content-security-policy"] and "sandbox" not in pdf.headers["content-security-policy"]
    z = api.get(f"/api/artifacts/{ids['pack.zip']}/content")
    assert z.headers["content-type"] == "application/octet-stream" and z.headers["content-disposition"].startswith("attachment;")
    dl = api.get(f"/api/artifacts/{ids['notes.md']}/content", params={"download": True})
    assert dl.headers["content-disposition"] == "attachment; filename*=UTF-8''notes.md"
    assert api.get(f"/api/artifacts/{ids['odd name é.txt']}/content").headers["content-disposition"] == "inline; filename*=UTF-8''odd%20name%20_.txt"


def test_config_storages_and_purge_endpoints(api, tmp_path):
    cfg = api.get("/api/artifacts/config").json()
    assert cfg["capture"] == "new" and cfg["storages"][0]["id"] == "local"
    other = tmp_path / "second"
    put = api.put("/api/artifacts/config", json={"storages": [{"id": "local", "kind": "local"}, {"id": "disk2", "kind": "local", "config": {"folder": str(other)}}],
                                                  "policy": [{"types": ["image"], "storages": ["local", "disk2"]}], "retention_days": 7})
    assert put.status_code == 200 and put.json()["storages"][1]["where"] == str(other)
    assert api.put("/api/artifacts/config", json={"capture": "sometimes"}).status_code == 422
    assert api.post("/api/artifacts/storages/disk2/test").json() == {"ok": True, "detail": f"{other} is writable"}
    assert api.post("/api/artifacts/storages/ghost/test").status_code == 404
    a = save(api, "pic.png", "PNG")
    assert api.get(f"/api/artifacts/{a['id']}").json()["copies"][0]["storage"] == "local"
    assert api.post("/api/artifacts/purge").json() == {"deleted": 0} and api.post("/api/artifacts/purge", params={"days": 1}).json() == {"deleted": 0}
    api.app.state.s.db.execute("UPDATE artifacts SET created=1")
    assert api.post("/api/artifacts/purge", params={"days": 1}).json() == {"deleted": 1}


# ------------------------------------------------------------------------------------------------ the tool
@pytest.fixture
def tools(state, tmp_path):
    events = []

    def make(artifacts="default"):
        ctx = ToolContext(tmp_path, None, None, None, agent_id="analyst", session_id="s1", trace_id="t1", on_artifact=events.append,
                          artifacts=state.artifacts if artifacts == "default" else artifacts)
        return {f.__name__: f for f in build_tools(ctx, ["save_artifact", "list_dir"])}
    return make, events


def test_save_artifact_keeps_a_file_or_some_text(tools, tmp_path, state):
    make, events = tools
    t = make()["save_artifact"]
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "q3.md").write_bytes(b"# Q3\nnumbers")
    r = run(t("q3-report.md", path="out/q3.md", tags="sales, q3"))
    assert r["ok"] and r["name"] == "q3-report.md" and r["type"] == "document" and r["kept_in"] == ["local"] and len(events) == 1
    saved = state.artifacts.get(r["id"])
    assert saved["agent"] == "analyst" and saved["session_id"] == "s1" and saved["trace_id"] == "t1" and saved["tags"] == ["sales", "q3"] and saved["source"] == "tool" and saved["path"] == "out/q3.md"
    again = run(t("q3-report.md", path="out/q3.md"))
    assert again["ok"] and again["note"] == "this exact file was already saved" and len(events) == 1
    txt = run(t("summary.md", content="All good."))
    assert txt["ok"] and state.artifacts.get(txt["id"])["path"] == "" and len(events) == 2
    assert run(t("x.md", path="out"))["error"] == "not a file: out" and "escapes the workspace" in run(t("x.md", path="../up.md"))["error"]
    assert run(t("x.md", content=""))["error"] == "there is nothing to save: the content is empty" and run(t("x.md", path="out/missing.md"))["error"].startswith("not a file")
    assert run(make(None)["save_artifact"]("x.md", content="x")) == {"ok": False, "error": "artifacts are not available"}
    quiet = ToolContext(tmp_path, None, None, None, artifacts=state.artifacts)               # no chat to tell: still works
    assert run({f.__name__: f for f in build_tools(quiet, ["save_artifact"])}["save_artifact"]("solo.md", content="solo"))["ok"]


# ------------------------------------------------------------------------------------------------ a real agent turn
def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_files_an_agent_creates_become_artifacts_in_the_result_the_stream_and_the_history(api, tmp_path):
    msg = '!tool write_file {"path": "reports/q3.md", "content": "# Q3\\nrevenue"}\n!tool save_artifact {"name": "kept.md", "content": "deliberately kept", "tags": "kept"}'
    r = api.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": msg})
    assert r.status_code == 200
    assert "## Results and rich answers" in r.json()["runs"][0]["instruction"] and "```chart" in r.json()["runs"][0]["instruction"]      # the agent is told what its answer can show
    names = sorted(a["name"] for a in r.json()["artifacts"])
    assert names == ["kept.md", "q3.md"]
    q3 = next(a for a in r.json()["artifacts"] if a["name"] == "q3.md")
    assert q3["source"] == "auto" and q3["agent"] == "engineer" and q3["trace_id"] == r.json()["trace_id"] and q3["path"] == "reports/q3.md" and q3["session_id"] == r.json()["session_id"]
    hist = api.get(f"/api/chat/sessions/{r.json()['session_id']}").json()["messages"]
    assert sorted(a["name"] for a in next(m for m in hist if m["role"] == "bot")["meta"]["artifacts"]) == ["kept.md", "q3.md"]
    assert api.get("/api/artifacts", params={"trace_id": r.json()["trace_id"]}).json()["total"] == 2
    again = api.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": '!tool read_file {"path": "reports/q3.md"}', "session_id": r.json()["session_id"]})
    assert again.json()["artifacts"] == []                                              # nothing new was made

    streamed = api.post("/api/chat/stream", json={"provider": "mock", "agent": "engineer", "message": '!tool write_file {"path": "b.csv", "content": "a,b"}'})
    ev = [e for e in sse(streamed.text) if e.get("type") == "artifact"]
    assert [e["artifact"]["name"] for e in ev] == ["b.csv"] and ev[0]["artifact"]["preview"] == "csv"


def test_plain_chat_and_switched_off_collect_nothing(api, tmp_path):
    api.put("/api/artifacts/config", json={"capture": "off"})
    r = api.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": '!tool write_file {"path": "z.md", "content": "z"}'})
    assert r.json()["artifacts"] == [] and (tmp_path / "ws" / "z.md").exists()
    api.put("/api/artifacts/config", json={"capture": "new"})
    plain = api.post("/api/chat", json={"provider": "mock", "message": "hello"})
    assert plain.json()["artifacts"] == []


# ------------------------------------------------------------------------------------------------ AI-BOM and settings
def test_the_ai_bom_has_counts_and_never_names(api, state):
    save(api, "secret-roadmap-2027.md", "private plans")
    from app.aibom.builder import build_bom
    for level in ("public", "internal", "full"):
        bom = build_bom(state, level)
        svc = next(s for s in bom["services"] if s["bom-ref"] == "artifacts")
        props = {p["name"].split(":")[-1]: p["value"] for p in svc["properties"]}
        assert props["artifacts-total"] == "1" and props["storage-kinds"] and "secret-roadmap" not in json.dumps(bom) and "private plans" not in json.dumps(bom)
    api.put("/api/artifacts/config", json={"storages": [{"id": "local", "kind": "local"}, {"id": "dav", "kind": "webdav", "config": {"url": "https://cloud.example.com"}}]})
    svc = next(s for s in build_bom(state, "public")["services"] if s["bom-ref"] == "artifacts")
    assert "webdav" in svc["data"][0]["description"]
    state.artifacts = None                                                       # a state without the artifact store simply has no such entry
    assert not any(s["bom-ref"] == "artifacts" for s in build_bom(state, "public")["services"])


def test_the_settings_page_can_export_and_import_the_artifact_rules(api):
    api.put("/api/artifacts/config", json={"capture": "new_and_changed", "max_file_mb": 9})
    exported = api.get("/api/settings/export").json()
    assert exported["settings"]["artifact_config"]["max_file_mb"] == 9 and "artifact_secrets" not in exported["settings"]
    api.put("/api/artifacts/config", json={"capture": "off", "max_file_mb": 3})
    imp = api.post("/api/settings/import", json={"data": exported, "dry_run": False})
    assert imp.status_code == 200 and api.get("/api/artifacts/config").json()["max_file_mb"] == 9


def test_audio_and_video_play_inline_with_byte_ranges_and_captions_are_text(api):
    song = save(api, "song.mp3", "0123456789" * 10)["id"]
    r = api.get(f"/api/artifacts/{song}/content")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.headers["accept-ranges"] == "bytes" and r.headers["content-disposition"].startswith("inline;")
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    part = api.get(f"/api/artifacts/{song}/content", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == b"0123456789" and part.headers["content-range"] == "bytes 10-19/100"
    assert api.get(f"/api/artifacts/{song}/content", headers={"Range": "bytes=500-"}).status_code == 200
    dl = api.get(f"/api/artifacts/{song}/content", params={"download": True}, headers={"Range": "bytes=0-1"})
    assert dl.status_code == 200 and "accept-ranges" not in dl.headers
    mkv = save(api, "clip.mkv", "xx")["id"]
    assert api.get(f"/api/artifacts/{mkv}/content").headers["content-type"] == "application/octet-stream"
    vtt = save(api, "clip.vtt", "WEBVTT\n\n00:00.000 --> 00:01.000\nhi")["id"]
    assert api.get(f"/api/artifacts/{vtt}/content").headers["content-type"].startswith("text/vtt")
