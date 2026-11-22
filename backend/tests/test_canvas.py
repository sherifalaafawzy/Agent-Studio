"""Canvas: versions, proposals, conflicts, the agent tools, the REST API, live updates, plan mode, the AI-BOM and AG-UI."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.canvas.api import event_stream
from app.canvas.service import CanvasConflict, CanvasError, apply_edits, clean_edits
from app.canvas.tools import CANVAS_RULES, canvas_tools
from app.harness.policy import ToolPolicy
from app.harness.tools import ALL_TOOLS, ToolContext, build_tools


def run(c):
    return asyncio.run(c)


@pytest.fixture
def svc(state):
    return state.canvas


def doc(svc, content="Hello world. Second line.", **kw):
    return svc.create(kw.pop("title", "Notes"), kw.pop("type", "markdown"), content, **kw)


# ------------------------------------------------------------------------------------------------ edits
def test_edits_replace_exact_text_that_appears_once():
    assert apply_edits("a b c", [{"old": "b", "new": "B"}, {"at": "start", "new": ">"}, {"at": "end", "new": "<"}]) == (">a B c<", [])
    with pytest.raises(CanvasConflict, match="no longer there") as e:
        apply_edits("a b", [{"old": "zz", "new": "x"}])
    assert e.value.conflicts == [1]
    with pytest.raises(CanvasConflict, match="appears 2 times"):
        apply_edits("a a", [{"old": "a", "new": "x"}])
    assert apply_edits("a a b", [{"old": "a", "new": "x"}, {"old": "b", "new": "B"}], skip=True) == ("a a B", [1])


def test_edits_are_validated():
    assert clean_edits([{"old": "a", "new": "b"}, {"at": "end", "new": "c"}, {"at": "start", "old": "ignored", "new": "d"}], 100) == [{"old": "a", "new": "b"}, {"at": "end", "new": "c"}, {"at": "start", "new": "d"}]
    for bad, msg in (([], "1 to 30"), ("x", "1 to 30"), ([{}] * 31, "1 to 30"), ([{"old": "a"}], "needs 'new'"), ([5], "needs 'new'"), ([{"old": "", "new": "x"}], "exact 'old' text"),
                     ([{"old": "a", "new": "x" * 101}], "longer than 100")):
        with pytest.raises(CanvasError, match=msg):
            clean_edits(bad, 100)


# ------------------------------------------------------------------------------------------------ settings
def test_settings_are_validated_and_the_canvas_can_be_switched_off(svc):
    assert svc.config() == {"enabled": True, "auto_apply": False, "max_chars": 200000, "keep_versions": 100}
    assert svc.set_config({"auto_apply": True, "keep_versions": 5})["keep_versions"] == 5
    for bad, msg in (("x", "send an object"), ({"nope": 1}, "unknown canvas setting"), ({"enabled": "yes"}, "true or false"), ({"max_chars": 5}, "from 1000 to"), ({"keep_versions": True}, "whole number")):
        with pytest.raises(CanvasError, match=msg):
            svc.set_config(bad)
    svc.db.set_setting("canvas_config", "{broken")
    assert svc.config()["max_chars"] == 200000
    svc.db.set_setting("canvas_config", "[1]")
    assert svc.config()["enabled"] is True
    svc.set_config({"enabled": False})
    with pytest.raises(CanvasError, match="switched off"):
        doc(svc)


# ------------------------------------------------------------------------------------------------ documents and versions
def test_creating_updating_and_listing(svc):
    c = doc(svc, "# Plan", session_id="s1", agent="writer")
    assert c["version"] == 1 and c["artifact_id"] and c["size"] == 6 and c["pending"] == 0
    assert svc.get(c["id"])["content"] == "# Plan"
    empty = svc.create("", "code", "", "python", session_id="s1")
    assert empty["title"] == "Untitled" and empty["artifact_id"] == "" and empty["language"] == "python"
    assert [x["id"] for x in svc.list("s1")] == [empty["id"], c["id"]] and "content" not in svc.list()[0]
    assert svc.latest_for_session("s1") == empty["id"] and svc.latest_for_session("nope") == "" and svc.latest_for_session("") == ""
    u = svc.update(c["id"], content="# Plan 2", title="  Roadmap  ", language="markdown", base_version=1)
    assert (u["version"], u["title"], u["language"]) == (2, "Roadmap", "markdown")
    same = svc.update(c["id"], content="# Plan 2")
    assert same["version"] == 2
    assert svc.update(c["id"], title="")["title"] == "Roadmap" and svc.update(c["id"], language="")["language"] == ""
    with pytest.raises(CanvasConflict, match="changed since you opened") as e:
        svc.update(c["id"], content="stale", base_version=1)
    assert e.value.current == 2
    with pytest.raises(CanvasError, match="no such canvas"):
        svc.get("nope")


def test_limits_and_bad_values_are_refused(svc):
    svc.set_config({"max_chars": 1000})
    with pytest.raises(CanvasError, match="type must be"):
        svc.create("x", "video")
    with pytest.raises(CanvasError, match="language must be"):
        svc.create("x", "code", "", "cobol")
    with pytest.raises(CanvasError, match="1000 character limit"):
        svc.create("x", "text", "a" * 1001)
    c = doc(svc)
    with pytest.raises(CanvasError, match="1000 character limit"):
        svc.update(c["id"], content="a" * 1001)
    with pytest.raises(CanvasError, match="language must be"):
        svc.update(c["id"], language="cobol")
    for i in range(50):
        svc.create(f"n{i}", "text", "", session_id="full")
    with pytest.raises(CanvasError, match="at most 50 canvases"):
        svc.create("one more", "text", "", session_id="full")
    assert svc.create("by someone", "text", "x", author="not valid!")["version"] == 1


def test_versions_are_kept_pruned_and_can_be_restored(svc):
    svc.set_config({"keep_versions": 5})
    c = doc(svc, "v1")
    for i in range(2, 9):
        svc.update(c["id"], content=f"v{i}")
    vs = svc.versions(c["id"])
    assert [v["version"] for v in vs] == [8, 7, 6, 5, 4] and vs[0]["author"] == "user" and vs[0]["size"] == 2
    assert svc.version_content(c["id"], 6)["content"] == "v6"
    with pytest.raises(CanvasError, match="version 1 is not kept"):
        svc.version_content(c["id"], 1)
    r = svc.restore(c["id"], 6)
    assert r["content"] == "v6" and r["version"] == 9
    top = svc.versions(c["id"])[0]
    assert top["author"] == "restore" and top["note"] == "restored version 6"
    again = svc.restore(c["id"], 9)
    assert again["version"] == 9                                  # nothing changed: no new version
    assert svc.delete(c["id"]) == {"deleted": True}
    with pytest.raises(CanvasError):
        svc.versions(c["id"])


def test_the_canvas_is_kept_as_a_new_version_of_one_artifact(svc, state):
    c = doc(svc, "first draft")
    a1 = c["artifact_id"]
    svc.update(c["id"], content="second draft")
    assert svc.get(c["id"])["artifact_id"] == a1                  # typing saves to the canvas only, not an artifact each time
    snap = svc.snapshot(c["id"])
    assert snap["artifact_id"] != a1
    art = state.artifacts.get(snap["artifact_id"])
    assert art["name"] == "Notes.md" and art["version"] == 2 and art["source"] == "canvas" and art["tags"] == ["canvas"]
    code = svc.create("Tool", "code", "print(1)", "python")
    assert state.artifacts.get(code["artifact_id"])["name"] == "Tool.py"
    state.artifacts.set_config({"enabled": False})
    assert svc.snapshot(c["id"])["id"] == c["id"]                 # artifacts off: the canvas still works
    svc.artifacts = None
    assert svc.snapshot(c["id"])["id"] == c["id"]


# ------------------------------------------------------------------------------------------------ the user's selection
def test_a_selection_is_found_again_even_after_the_text_moved(svc):
    c = doc(svc, "Alpha beta. Gamma delta.")
    assert svc.selection(c["id"]) is None
    assert svc.set_selection(c["id"], 6, 10) == {"start": 6, "end": 10, "text": "beta", "version": 1}
    assert svc.selection(c["id"]) == {"text": "beta", "start": 6, "end": 10}
    svc.update(c["id"], content="Intro. Alpha beta. Gamma delta.")
    assert svc.selection(c["id"]) == {"text": "beta", "start": 13, "end": 17}
    svc.update(c["id"], content="beta and beta")
    assert svc.selection(c["id"]) is None                        # ambiguous now
    svc.update(c["id"], content="gone")
    assert svc.selection(c["id"]) is None
    svc.db.execute("UPDATE canvases SET selection=? WHERE id=?", ("{bad", c["id"]))
    assert svc.selection(c["id"]) is None
    for s, e in ((-1, 2), (5, 2), (0, 99), (True, 2), ("a", 2), (0, 1.5)):
        with pytest.raises(CanvasError, match="outside the document"):
            svc.set_selection(c["id"], s, e)


# ------------------------------------------------------------------------------------------------ proposals
def test_an_agent_change_waits_for_review_and_can_be_accepted(svc):
    c = doc(svc, "Hello world. Second line.")
    p = svc.propose(c["id"], [{"old": "Hello", "new": "Hi"}], author="agent:writer", note="friendlier greeting")
    assert p["status"] == "proposed" and p["version"] == 1 and svc.get(c["id"])["content"] == "Hello world. Second line." and svc.get(c["id"])["pending"] == 1
    [pr] = svc.proposals(c["id"])
    assert pr["edits"][0] == {"old": "Hello", "new": "Hi", "n": 1, "fits": True, "start": 0, "end": 5} and pr["note"] == "friendlier greeting" and pr["status"] == "pending"
    out = svc.accept(c["id"], p["proposal_id"])
    assert out["content"] == "Hi world. Second line." and out["version"] == 2 and out["skipped"] == [] and out["pending"] == 0
    top = svc.versions(c["id"])[0]
    assert top["author"] == "agent:writer" and top["note"] == "friendlier greeting"
    assert svc.proposals(c["id"]) == [] and svc.proposals(c["id"], "accepted")[0]["status"] == "accepted" and len(svc.proposals(c["id"], "")) == 1
    with pytest.raises(CanvasError, match="already accepted"):
        svc.accept(c["id"], p["proposal_id"])
    with pytest.raises(CanvasError, match="no such proposal"):
        svc.reject(c["id"], "nope")


def test_proposals_are_checked_when_they_are_made(svc):
    c = doc(svc)
    for bad, msg in (([{"old": "absent", "new": "x"}], "no longer there"), ([{"old": "o", "new": "x"}], "appears")):
        with pytest.raises(CanvasConflict, match=msg):
            svc.propose(c["id"], bad, author="agent:w")
    with pytest.raises(CanvasConflict, match="read it again") as e:
        svc.propose(c["id"], [{"at": "end", "new": "x"}], author="agent:w", base_version=7)
    assert e.value.current == 1
    svc.set_config({"max_chars": 1000})
    with pytest.raises(CanvasError, match="result would be longer"):
        svc.propose(c["id"], [{"at": "end", "new": "x" * 999}], author="agent:w")
    assert svc.propose(c["id"], [{"at": "end", "new": "!"}], author="bad author!")["status"] == "proposed"
    assert svc.proposals(c["id"])[0]["author"] == "agent:agent"


def test_too_many_waiting_changes_are_refused(svc):
    c = doc(svc)
    for _ in range(20):
        svc.propose(c["id"], [{"at": "end", "new": "x"}], author="agent:w")
    with pytest.raises(CanvasError, match="20 changes waiting"):
        svc.propose(c["id"], [{"at": "end", "new": "x"}], author="agent:w")


def test_auto_apply_is_a_setting_and_plan_mode_overrides_it(svc):
    c = doc(svc)
    assert svc.propose(c["id"], [{"at": "end", "new": " A"}], author="agent:w", apply=True)["status"] == "proposed"     # the setting is off
    svc.set_config({"auto_apply": True})
    done = svc.propose(c["id"], [{"at": "end", "new": " B"}], author="agent:w", note="", apply=True)
    assert done["status"] == "applied" and done["version"] == 2 and svc.get(c["id"])["content"].endswith(" B")
    assert svc.versions(c["id"])[0]["note"] == "agent change"
    assert svc.propose(c["id"], [{"at": "end", "new": " C"}], author="agent:w", apply=False)["status"] == "proposed"      # plan mode: never applied


def test_accepting_after_the_text_changed_reports_the_conflict(svc):
    c = doc(svc, "one two three")
    p1 = svc.propose(c["id"], [{"old": "one", "new": "1"}, {"old": "three", "new": "3"}], author="agent:w", note="numbers")
    svc.update(c["id"], content="one two 3 three")                # the user edited meanwhile: "three" is still there once, so both still fit
    assert svc.accept(c["id"], p1["proposal_id"])["content"] == "1 two 3 3"
    p2 = svc.propose(c["id"], [{"old": "two", "new": "2"}, {"old": "1", "new": "ONE"}], author="agent:w")
    svc.update(c["id"], content="1 two")
    pr = svc.proposals(c["id"])[0]
    assert [e["fits"] for e in pr["edits"]] == [True, True]
    svc.update(c["id"], content="1 1 two")                        # "1" is ambiguous now
    pr = svc.proposals(c["id"])[0]
    assert [e["fits"] for e in pr["edits"]] == [True, False] and pr["edits"][1]["start"] is None
    with pytest.raises(CanvasConflict, match="ambiguous") as e:
        svc.accept(c["id"], p2["proposal_id"])
    assert e.value.conflicts == [2]
    out = svc.accept(c["id"], p2["proposal_id"], skip_conflicts=True)
    assert out["content"] == "1 1 2" and out["skipped"] == [2]
    p3 = svc.propose(c["id"], [{"old": "2", "new": "two"}], author="agent:w")
    svc.update(c["id"], content="nothing left")
    with pytest.raises(CanvasConflict, match="dropped") as e:
        svc.accept(c["id"], p3["proposal_id"], skip_conflicts=True)
    assert e.value.conflicts == [1] and svc.proposals(c["id"], "stale")


def test_accept_all_and_reject_all(svc):
    c = doc(svc, "a b c")
    for old, new in (("a", "A"), ("b", "B")):
        svc.propose(c["id"], [{"old": old, "new": new}], author="agent:w", note=f"{old} to {new}")
    out = svc.accept_all(c["id"])
    assert out["content"] == "A B c" and (out["accepted"], out["skipped"], out["dropped"]) == (2, 0, 0)
    svc.propose(c["id"], [{"old": "A", "new": "1"}], author="agent:w", note="first")
    svc.propose(c["id"], [{"old": "B", "new": "2"}], author="agent:w", note="second")
    svc.update(c["id"], content="A c")                            # the second no longer fits
    with pytest.raises(CanvasConflict, match="second") as e:
        svc.accept_all(c["id"])
    assert e.value.conflicts == [1]
    assert svc.get(c["id"])["content"] == "1 c"                   # the first one that fit was already applied
    svc.propose(c["id"], [{"old": "c", "new": "C"}], author="agent:w")
    out = svc.accept_all(c["id"], skip_conflicts=True)
    assert out["content"] == "1 C" and out["accepted"] == 1 and out["dropped"] == 1
    svc.propose(c["id"], [{"at": "end", "new": "!"}], author="agent:w")
    svc.propose(c["id"], [{"at": "end", "new": "?"}], author="agent:w")
    assert svc.reject_all(c["id"]) == {"rejected": 2} and svc.proposals(c["id"]) == []
    one = svc.propose(c["id"], [{"at": "start", "new": "-"}], author="agent:w")
    assert svc.reject(c["id"], one["proposal_id"]) == {"rejected": True}
    assert svc.counts() == {"canvases": 1, "pending": 0}


# ------------------------------------------------------------------------------------------------ live updates
def test_subscribers_hear_about_changes_and_closed_ones_are_dropped(svc):
    async def go():
        q = svc.subscribe("c1")
        svc._emit("c1", {"type": "version", "version": 2})
        assert await asyncio.wait_for(q.get(), 1) == {"type": "version", "version": 2}
        svc.unsubscribe("c1", q)
        svc._emit("c1", {"type": "x"})
        assert q.empty()
        class Dead:
            def call_soon_threadsafe(self, *a):
                raise RuntimeError("closed")
        svc._subs["c2"] = {(Dead(), asyncio.Queue())}
        svc._emit("c2", {"type": "x"})
        assert svc._subs["c2"] == set()
    run(go())


def test_the_event_stream_sends_events_keepalives_and_stops_when_the_client_leaves(svc):
    class Req:
        def __init__(self):
            self.gone = False

        async def is_disconnected(self):
            return self.gone

    async def go():
        req, out = Req(), []
        gen = event_stream(svc, "c1", req, keepalive=0.05)
        out.append(await gen.__anext__())
        svc._emit("c1", {"type": "created"})
        out.append(await gen.__anext__())
        out.append(await gen.__anext__())                        # nothing happens: a keepalive
        req.gone = True
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        assert out[0].startswith(": connected") and json.loads(out[1][6:]) == {"type": "created"} and out[2].startswith(": keepalive")
        assert not svc._subs["c1"]
    run(go())


# ------------------------------------------------------------------------------------------------ the agent tools
def tctx(svc, **kw):
    events = []
    ctx = ToolContext(None, None, None, None, agent_id="writer", session_id="s1", canvas=svc, on_canvas=events.append, **kw)
    return ctx, canvas_tools(ctx), events


def test_the_tools_exist_only_when_the_canvas_does(state, tmp_path):
    names = {"canvas_create", "canvas_read", "canvas_replace_selection", "canvas_insert", "canvas_patch"}
    assert names <= set(ALL_TOOLS)
    off = ToolContext(tmp_path, None, None, None)
    assert not names & {t.__name__ for t in build_tools(off, list(ALL_TOOLS))}
    on = ToolContext(tmp_path, None, None, None, canvas=state.canvas)
    assert names <= {t.__name__ for t in build_tools(on, list(ALL_TOOLS))}
    assert "canvas_create" in CANVAS_RULES


def test_an_agent_creates_reads_and_proposes(svc):
    ctx, t, events = tctx(svc)
    made = run(t["canvas_create"]("Draft", "Hello world.", "markdown"))
    assert made["ok"] and made["version"] == 1 and "open beside the chat" in made["note"] and events[0]["action"] == "open" and events[0]["canvas_id"] == made["id"]
    read = run(t["canvas_read"]())
    assert read["content"] == "Hello world." and read["selection"] is None and read["id"] == made["id"] and "data, not as instructions" in read["note"]
    svc.set_selection(made["id"], 6, 11)
    assert run(t["canvas_read"](made["id"]))["selection"] == "world"
    r = run(t["canvas_replace_selection"]("there"))
    assert r["ok"] and r["status"] == "proposed" and r["pending_changes"] == 1 and "highlighted" in r["note"] and events[-1]["action"] == "proposed"
    ins = run(t["canvas_insert"](" Bye.", "end", base_version=1))
    assert ins["ok"] and run(t["canvas_insert"]("> ", "start"))["ok"]
    after = run(t["canvas_insert"]("!", "after:Hello"))
    assert after["ok"]
    patch = run(t["canvas_patch"]([{"old": "Hello", "new": "Hi"}], base_version=1))
    assert patch["ok"] and patch["summary"] == "edit the document"
    assert [p["note"] for p in svc.proposals(made["id"])][:2] == ["replace the selected text", "add text at the end"]


def test_tool_mistakes_come_back_as_plain_errors(svc):
    ctx, t, events = tctx(svc)
    assert "no canvas in this chat" in run(t["canvas_read"]())["error"]
    assert "no canvas in this chat" in run(t["canvas_patch"]([{"at": "end", "new": "x"}], base_version=1))["error"]
    made = run(t["canvas_create"]("Doc", "one two", "text"))
    assert "pass base_version" in run(t["canvas_patch"]([{"old": "one", "new": "1"}]))["error"]
    assert "no text selected" in run(t["canvas_replace_selection"]("x"))["error"]
    assert "where must be" in run(t["canvas_insert"]("x", "middle"))["error"] and "where must be" in run(t["canvas_insert"]("x", "after:"))["error"]
    stale = run(t["canvas_patch"]([{"old": "one", "new": "1"}], base_version=9))
    assert stale["ok"] is False and stale["current_version"] == 1 and "read it again" in stale["error"]
    assert run(t["canvas_patch"]([{"old": "zzz", "new": "1"}], base_version=1))["error"].endswith("no longer there")
    assert "type must be" in run(t["canvas_create"]("x", "x", "video"))["error"]
    assert made["ok"]


def test_in_plan_mode_an_agent_can_only_propose_even_with_auto_apply(svc):
    svc.set_config({"auto_apply": True})
    _, t, _ = tctx(svc, can_apply=False)
    made = run(t["canvas_create"]("Plan doc", "Starting text", "markdown"))
    assert "Plan mode is on" in made["note"]
    c = svc.get(made["id"])
    assert c["content"] == "" and c["pending"] == 1 and svc.proposals(c["id"])[0]["edits"][0]["new"] == "Starting text"
    r = run(t["canvas_insert"]("more"))
    assert r["status"] == "proposed" and svc.get(c["id"])["content"] == ""
    empty = run(t["canvas_create"]("Empty", "", "text"))
    assert "Plan mode" in empty["note"] and svc.get(empty["id"])["pending"] == 0
    _, applying, _ = tctx(svc)
    done = run(applying["canvas_insert"]("direct", canvas_id=c["id"]))
    assert done["status"] == "applied" and "undo it from the version history" in done["note"]


def test_a_plan_mode_policy_allows_canvas_proposals_but_not_other_writes():
    plan = ToolPolicy("plan")
    assert plan.before("canvas_read", {}) is None and plan.before("canvas_patch", {"x": 1}) is None and plan.before("canvas_create", {}) is None
    assert "Plan mode is on" in plan.before("write_file", {"path": "a"})
    assert ToolPolicy("full").before("canvas_insert", {}) is None


# ------------------------------------------------------------------------------------------------ the REST API
def test_the_api_covers_a_whole_editing_session(client, state):
    assert client.get("/api/canvas/config").json()["enabled"] is True
    assert client.put("/api/canvas/config", json={"auto_apply": True}).json()["auto_apply"] is True
    assert client.put("/api/canvas/config", json={"auto_apply": "yes"}).status_code == 422
    client.put("/api/canvas/config", json={"auto_apply": False})
    c = client.post("/api/canvas", json={"title": "Memo", "type": "markdown", "content": "Dear team, hello.", "session_id": "s9", "agent": "writer"}).json()
    assert c["version"] == 1 and client.get("/api/canvas", params={"session_id": "s9"}).json()[0]["id"] == c["id"]
    assert client.post("/api/canvas", json={"type": "video"}).status_code == 422
    assert client.get("/api/canvas/nope").status_code == 404
    u = client.put(f"/api/canvas/{c['id']}", json={"content": "Dear team, hi.", "base_version": 1, "title": "Memo 2"}).json()
    assert u["version"] == 2
    stale = client.put(f"/api/canvas/{c['id']}", json={"content": "x", "base_version": 1})
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2 and "changed since" in stale.json()["detail"]["message"]
    assert client.put(f"/api/canvas/{c['id']}", json={"content": "x", "base_version": "1"}).status_code == 422
    assert client.put(f"/api/canvas/{c['id']}", json={"content": 5}).status_code == 422
    assert [v["version"] for v in client.get(f"/api/canvas/{c['id']}/versions").json()] == [2, 1]
    assert client.get(f"/api/canvas/{c['id']}/versions/1").json()["content"] == "Dear team, hello."
    assert client.get(f"/api/canvas/{c['id']}/versions/9").status_code == 422
    assert client.post(f"/api/canvas/{c['id']}/selection", json={"start": 0, "end": 4}).json()["text"] == "Dear"
    assert client.post(f"/api/canvas/{c['id']}/selection", json={"start": 0, "end": 400}).status_code == 422
    p = state.canvas.propose(c["id"], [{"old": "hi", "new": "hello"}], author="agent:writer", note="warmer")
    assert client.get(f"/api/canvas/{c['id']}/proposals").json()[0]["id"] == p["proposal_id"]
    assert client.post(f"/api/canvas/{c['id']}/proposals/{p['proposal_id']}/accept", json={}).json()["content"] == "Dear team, hello."
    again = client.post(f"/api/canvas/{c['id']}/proposals/{p['proposal_id']}/accept", json={})
    assert again.status_code == 422 and "already accepted" in again.json()["detail"]
    q = state.canvas.propose(c["id"], [{"old": "team", "new": "all"}], author="agent:writer")
    state.canvas.update(c["id"], content="Dear team team")
    conflict = client.post(f"/api/canvas/{c['id']}/proposals/{q['proposal_id']}/accept", json={})
    assert conflict.status_code == 409 and conflict.json()["detail"]["conflicts"] == [1]
    assert client.post(f"/api/canvas/{c['id']}/proposals/{q['proposal_id']}/reject").json() == {"rejected": True}
    state.canvas.propose(c["id"], [{"at": "end", "new": "!"}], author="agent:writer")
    assert client.post(f"/api/canvas/{c['id']}/accept-all", json={"skip_conflicts": True}).json()["accepted"] == 1
    state.canvas.propose(c["id"], [{"at": "end", "new": "?"}], author="agent:writer")
    assert client.post(f"/api/canvas/{c['id']}/reject-all").json() == {"rejected": 1}
    assert client.post(f"/api/canvas/{c['id']}/restore", json={"version": 1}).json()["content"] == "Dear team, hello."
    assert client.post(f"/api/canvas/{c['id']}/restore", json={"version": "1"}).status_code == 422
    assert client.post(f"/api/canvas/{c['id']}/snapshot").json()["artifact_id"]
    assert client.get("/api/artifacts", params={"source": "canvas"}).json()["total"] >= 1
    assert client.delete(f"/api/canvas/{c['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/canvas/{c['id']}").status_code == 404


def test_the_events_endpoint_checks_the_canvas_exists(client):
    assert client.get("/api/canvas/nope/events").status_code == 404


def test_the_events_route_streams_server_sent_events(state):
    from fastapi import FastAPI

    from app.canvas.api import register
    app = FastAPI()
    app.state.s = state
    register(app)
    endpoint = next(x.endpoint for x in app.routes[-1].original_router.routes if x.path == "/api/canvas/{cid}/events")
    c = state.canvas.create("Live", "text", "x")

    class Req:
        app = SimpleNamespace(state=SimpleNamespace(s=state))

        async def is_disconnected(self):
            return True

    async def go():
        resp = await endpoint(c["id"], Req())
        assert resp.media_type == "text/event-stream" and resp.headers["cache-control"] == "no-cache"
        assert (await resp.body_iterator.__anext__()).startswith(": connected")
        await resp.body_iterator.aclose()
    run(go())


# ------------------------------------------------------------------------------------------------ the rest of the app
def sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_a_real_turn_can_open_a_canvas_and_propose_a_change(client, state, tmp_path):
    client.put("/api/providers/mock", json={"cap_usd": 1000, "max_output_tokens": 128})
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    msg = '!tool canvas_create {"title": "Brief", "content": "Hello world."}\n!tool canvas_insert {"text": " Bye.", "where": "end"}'
    r = client.post("/api/chat/stream", json={"provider": "mock", "agent": "engineer", "message": msg})
    events = sse(r.text)
    assert [e["action"] for e in events if e.get("type") == "canvas"] == ["open", "proposed"]
    [c] = state.canvas.list()
    assert c["title"] == "Brief" and c["pending"] == 1 and state.canvas.get(c["id"])["content"] == "Hello world." and c["session_id"]
    plain = client.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": "hello"})
    assert "## Canvas" in plain.json()["runs"][0]["instruction"]
    state.canvas.set_config({"auto_apply": True})
    r = client.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": '!tool canvas_insert {"text": " Direct.", "where": "end", "canvas_id": "' + c["id"] + '"}'})
    assert state.canvas.get(c["id"])["content"].endswith(" Direct.")
    state.db.set_setting("harness_policy", json.dumps({"permission_mode": "plan"}))
    client.post("/api/chat", json={"provider": "mock", "agent": "engineer", "message": '!tool canvas_create {"title": "Planned", "content": "Only proposed."}'})
    planned = next(x for x in state.canvas.list() if x["title"] == "Planned")
    assert state.canvas.get(planned["id"])["content"] == "" and planned["pending"] == 1


def test_the_ai_bom_has_counts_and_never_titles(client, state):
    c = state.canvas.create("Secret merger plan", "text", "the deal closes in March")
    from app.aibom.builder import build_bom
    for level in ("public", "internal", "full"):
        bom = build_bom(state, level)
        svc = next(s for s in bom["services"] if s["bom-ref"] == "canvas")
        props = {p["name"].split(":")[-1]: p["value"] for p in svc["properties"]}
        assert props["canvases"] == "1" and props["enabled"] == "true" and "Secret merger" not in json.dumps(bom) and "closes in March" not in json.dumps(bom)
    assert c["id"]
    state.canvas = None
    assert not any(s["bom-ref"] == "canvas" for s in build_bom(state, "public")["services"])


def test_the_settings_page_can_export_and_import_canvas_settings(client, state):
    client.put("/api/canvas/config", json={"keep_versions": 7})
    exported = client.get("/api/settings/export").json()
    assert exported["settings"]["canvas_config"]["keep_versions"] == 7
    client.put("/api/canvas/config", json={"keep_versions": 9})
    r = client.post("/api/settings/import", json={"data": exported, "dry_run": False}).json()
    assert not [e for e in r["errors"] if e["key"] == "canvas_config"] and state.canvas.config()["keep_versions"] == 7
    bad = {"format": "agent-studio-settings", "settings": {"canvas_config": {"keep_versions": 1}}}
    assert client.post("/api/settings/import", json={"data": bad, "dry_run": False}).json()["errors"][0]["key"] == "canvas_config"


def test_canvas_events_in_a_chat_become_ag_ui_activity_and_state():
    from app.agui import AguiTranslator
    tr = AguiTranslator("th", "r1")
    out = tr.feed({"type": "canvas", "action": "open", "canvas_id": "c1", "title": "Doc", "version": 1, "pending": 0, "seq": 3})
    kinds = [str(e.type) for e in out]
    act = next(e for e in out if str(e.type).endswith("ACTIVITY_SNAPSHOT"))
    assert act.activity_type == "canvas" and act.content["canvas_id"] == "c1" and "seq" not in act.content
    assert any(k.endswith("STATE_DELTA") for k in kinds) and tr.state["canvases"] == {"c1": {"title": "Doc", "version": 1, "pending": 0}}
    tr.feed({"type": "canvas", "action": "proposed", "canvas_id": "c1", "version": 1, "pending": 1})
    assert tr.state["canvases"]["c1"] == {"version": 1, "pending": 1}
