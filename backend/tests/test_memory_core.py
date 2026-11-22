"""Memory: the data model, PII removal, extraction, the local store and the service (saving, searching, review, expiry, export)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.budget import BudgetExceeded
from app.memory import extract as X
from app.memory import model as M
from app.memory.pii import scrub
from app.memory.service import DEFAULT_CONFIG, MemoryError_, MemoryService
from app.memory.store import Kind, MemoryFactory, MemoryNotFound
from app.rag.embeddings import EmbeddingError
from tests.rag_fakes import FakeEmbedder

run = asyncio.run
U = ("user", M.USER_ID)


@pytest.fixture
def svc(state):
    s = MemoryService(state.db, state.providers, state.guard)
    s.embedder = FakeEmbedder()
    return s


@pytest.fixture
def vsvc(svc):
    svc.set_config({"embedding": {"provider": "mock", "model": "embed-a"}})
    return svc


def add(svc, text="The user prefers TypeScript", type="semantic", scope="user", sid=M.USER_ID, **kw):
    return run(svc.add(type=type, text=text, scope=scope, scope_id=sid, **kw))


# ------------------------------------------------------------------------------------------------ model helpers
def test_agent_memory_settings_are_validated_and_normalised():
    assert M.clean_agent_memory(None) == {} and M.clean_agent_memory({}) == {} and M.clean_agent_memory({"types": []}) == {}
    got = M.clean_agent_memory({"types": ["notes", "semantic", "semantic"], "write_scopes": ["agent"], "auto_save": "auto"})
    assert got["types"] == ["semantic", "notes"] and got["write_scopes"] == ["agent"] and got["read_scopes"] == list(M.SCOPES) and got["auto_save"] == "auto" and got["auto_recall"] is True
    for bad in ("x", {"types": "semantic"}, {"types": ["nope"]}, {"types": ["semantic"], "read_scopes": ["moon"]}, {"types": ["semantic"], "auto_recall": "yes"},
                {"types": ["semantic"], "auto_save": "sometimes"}):
        with pytest.raises(ValueError):
            M.clean_agent_memory(bad)
    assert M.uses_records({"types": ["semantic"]}) and not M.uses_records({"types": ["notes", "working"]}) and not M.uses_records({})


def test_text_helpers():
    assert M.norm("  Hello,   WORLD! ") == "hello world" and M.tokens("The user is in Paris") == ["user", "paris"]
    assert M.text_score("paris user", "the user lives in paris") == 1.0 and M.text_score("the", "x") == 0.0 and M.text_score("a b", "b") == 1.0
    assert M.cosine([1, 0], [1, 0]) == 1.0 and M.cosine([1, 0], [0, 1]) == 0.0 and M.cosine([0, 0], [1, 0]) == 0.0 and M.cosine([1], [1, 2]) == 0.0
    assert M.slug("abc") == "abc" and M.slug("H:\\My Work") != "H:\\My Work" and "/" not in M.slug("a/b/c") and M.slug("***").isalnum() and len(M.slug("x" * 100)) < 40
    assert M.scope_id("agent", "eng", "w") == "eng" and M.scope_id("user", "eng", "w") == M.USER_ID and M.scope_id("global", "a", "w") == "*"
    assert M.scope_id("workspace", "a", "H:\\Work\\Proj") == M.scope_id("workspace", "b", "h:/work/proj")           # case and slash style do not matter
    assert M.new_id().startswith("m_")


# ------------------------------------------------------------------------------------------------ PII
def test_scrub_removes_personal_and_secret_data():
    text, kinds = scrub("Mail bob@example.com or call +44 20 7946 0958, card 4111 1111 1111 1111, ssn 123-45-6789, key sk-abcdefghijklmnop1234567890")
    assert "bob@example.com" not in text and "4111" not in text and "123-45-6789" not in text and "sk-abcdef" not in text and "7946" not in text
    assert kinds == ["secret", "card", "ssn", "email", "phone"]
    plain, none = scrub("The invoice number is 1234567812345678 and the build is 2026-11-22")      # 16 digits that fail the Luhn check stay
    assert plain.startswith("The invoice number is 1234567812345678") and none == []


# ------------------------------------------------------------------------------------------------ extraction
def test_explicit_and_rule_candidates():
    assert X.explicit_candidates("Please remember that I deploy on Fridays")[0]["text"] == "I deploy on Fridays"
    assert X.explicit_candidates("remember: x")[0]["explicit"] and X.explicit_candidates("remember that ") == [] and X.explicit_candidates("hello") == [] and X.explicit_candidates(None) == []
    got = X.rule_candidates("My timezone is Europe/Paris. I prefer dark mode. I work at Acme Corp. We use Postgres. My favourite editor is Neovim. I always use tabs.")
    texts = {c["text"] for c in got}
    assert "The user's timezone is Europe/Paris" in texts and "The user prefers dark mode" in texts and "The user works at Acme Corp" in texts
    assert "The team uses Postgres" in texts and "The user's favorite editor is Neovim" in texts and "The user always uses tabs" in texts
    assert next(c for c in got if "timezone" in c["text"])["key"] == "user.timezone"
    assert len(X.rule_candidates("I prefer cats. I prefer cats.")) == 1 and X.rule_candidates("") == [] and X.rule_candidates(None) == []


def test_model_extraction_is_parsed_strictly():
    out = X.parse_model_output('noise [{"type":"semantic","text":"Uses Xero","key":"org.erp"},{"type":"entity","text":"x","name":"Alice","kind":"person","attributes":{"role":"CFO"}},'
                               '{"type":"entity","text":"no name","attributes":{}},{"type":"entity","text":"bad attrs","name":"B","attributes":"x"},'
                               '{"type":"episodic","text":"skipped"},{"type":"semantic","text":""},"junk",{"type":"procedural","text":"Run make test"}] trailing')
    assert [c["type"] for c in out] == ["semantic", "entity", "procedural"] and out[1]["attributes"] == {"role": "CFO"} and out[1]["name"] == "Alice"
    assert X.parse_model_output("no json") == [] and X.parse_model_output("[not json]") == [] and X.parse_model_output('{"a": 1} [') == [] and X.parse_model_output(None) == []
    assert "User said:\nhi" in X.model_prompt("hi", "hello") and X.model_prompt(None, None).count("User said") == 1
    assert X.episode_text("  fix\nthe bug ", "done") == "Chat about: fix the bug — done" and X.episode_text("x", "") == "Chat about: x"


# ------------------------------------------------------------------------------------------------ store + factory
def test_factory_builds_backends_and_checks_required_fields():
    f = MemoryFactory()
    f.register(Kind("local", "Local", "", False))
    f.register(Kind("thing", "Thing", "", True, (("url", "Address", False, True, ""), ("key", "Key", True, True, "")), lambda c, s, x: ("built", c, s)))
    assert f.create("local", {}, {}, {}) is None
    with pytest.raises(MemoryError_, match="unknown memory backend"):
        f.create("nope", {}, {}, {})
    with pytest.raises(MemoryError_, match="Address is required"):
        f.create("thing", {}, {"key": "k"}, {})
    with pytest.raises(MemoryError_, match="Key is required"):
        f.create("thing", {"url": "u"}, {}, {})
    assert f.create("thing", {"url": "u"}, {"key": "k"}, {})[0] == "built"


def test_local_store_queries(svc):
    st = svc.local
    a = add(svc, "alpha fact", scope="agent", sid="eng")
    b = add(svc, "beta fact about paris", type="entity", sid=M.USER_ID)
    assert {r["id"] for r in st.query()} == {a["id"], b["id"]} and [r["id"] for r in st.query(type="entity")] == [b["id"]]
    assert [r["id"] for r in st.query(scope="agent", scope_id="eng")] == [a["id"]] and [r["id"] for r in st.query(q="PARIS")] == [b["id"]]
    assert [r["id"] for r in st.query(ids=[a["id"]])] == [a["id"]] and st.query(status="pending") == [] and st.query(key="x") == []
    assert len(st.query(limit=1)) == 1 and len(st.query(limit=5, offset=5)) == 0
    st.update(a["id"], expires=time.time() - 5)
    assert [r["id"] for r in st.query()] == [b["id"]] and len(st.query(expired=True)) == 2
    assert st.get("zzz") is None and st.delete("zzz") is False and "embedding" not in st.get(b["id"]) and st.get(b["id"], with_vector=True)["embedding"] is None
    assert [r["id"] for r in st.purge_expired()] == [a["id"]] and st.get(a["id"]) is None
    assert st.stats() == {"total": 1, "pending": 0, "by_type": {"entity": 1}}
    assert st.search("paris", [], ["entity"], 3) == [] and st.search("paris", [U], [], 3) == []
    hit = st.search("paris", [U], ["entity"], 3)
    assert hit[0]["id"] == b["id"] and hit[0]["score"] > 0 and st.search("nothing here", [U], ["entity"], 3) == [] and st.search("paris", [U], ["entity"], 3, min_score=2) == []
    st.touch([b["id"]])
    st.touch([])
    assert st.get(b["id"])["hits"] == 1 and st.get(b["id"])["last_used"] > 0
    assert len(st.wipe("user", M.USER_ID)) == 1 and st.query() == []
    add(svc, "again")
    assert len(st.wipe("user")) == 1 and len(st.wipe()) == 0


# ------------------------------------------------------------------------------------------------ service: settings
def test_config_defaults_validation_and_secrets(svc, state):
    cfg = svc.config()
    assert cfg == DEFAULT_CONFIG and svc.public_config()["backend_label"] == "On this PC" and svc.public_config()["backend_cloud"] is False
    assert set(svc.public_config()["type_info"]) == set(M.AGENT_TYPES) and svc.public_config()["scopes"] == list(M.SCOPES)
    out = svc.set_config({"review": True, "redact_pii": False, "recall_k": 3, "recall_min_score": 0.5, "recall_budget_chars": 500, "notes_max_chars": 900,
                          "types": {"semantic": {"ttl_days": 30, "max_items": 50, "enabled": True}}, "auto_extract": "model", "notes_file": "NOTES.md",
                          "consolidation": {"similarity": 0.9, "dedupe": False, "interval_hours": 2, "merge_contradictions": False},
                          "embedding": {"provider": "mock", "model": "m"}, "extract_model": {"provider": "mock", "model": "x"}, "backend_config": {"a": "b"}, "unknown": 1, "enabled": None})
    assert out["review"] is True and out["types"]["semantic"]["ttl_days"] == 30 and out["types"]["episodic"]["ttl_days"] == 90 and out["consolidation"]["similarity"] == 0.9
    assert out["embedding"] == {"provider": "mock", "model": "m"} and out["notes_file"] == "NOTES.md" and "unknown" not in out
    bad = [{"enabled": "yes"}, {"backend": "x"}, {"backend_config": {"a": 1}}, {"backend_config": []}, {"embedding": "x"}, {"embedding": {"provider": "ghost"}}, {"embedding": {"model": "x" * 300}},
           {"types": []}, {"types": {"working": {}}}, {"types": {"semantic": []}}, {"types": {"semantic": {"zzz": 1}}}, {"types": {"semantic": {"enabled": "y"}}},
           {"types": {"semantic": {"ttl_days": -1}}}, {"types": {"semantic": {"max_items": 1}}}, {"auto_extract": "magic"}, {"recall_budget_chars": 5}, {"recall_k": 0},
           {"recall_k": 1.5}, {"recall_k": True}, {"recall_min_score": 2}, {"consolidation": []}, {"consolidation": {"x": 1}}, {"consolidation": {"dedupe": "y"}},
           {"consolidation": {"similarity": 0.1}}, {"consolidation": {"interval_hours": 0}}, {"notes_file": "../x"}, {"notes_file": 5}, {"notes_max_chars": 1}, {"secrets": "x"},
           {"secrets": {"a": 1}}]
    for patch in bad:
        with pytest.raises(MemoryError_):
            svc.set_config(patch)
    state.db.set_setting("memory_config", "not json")
    assert svc.config() == DEFAULT_CONFIG
    state.db.set_setting("memory_config", json.dumps([1]))
    assert svc.config() == DEFAULT_CONFIG
    state.db.set_setting("memory_config", json.dumps({"types": {"semantic": {"max_items": 77, "zzz": 1}, "nope": {}}, "consolidation": {"similarity": 0.8, "zzz": 1}, "backend_config": {"k": "v"}, "from_a_future_version": 1}))
    c = svc.config()
    assert c["types"]["semantic"]["max_items"] == 77 and c["consolidation"]["similarity"] == 0.8 and c["backend_config"] == {"k": "v"} and "zzz" not in c["consolidation"]


def test_cloud_secrets_are_write_only_and_checked_with_the_backend(svc, state):
    out = svc.set_config({"backend": "mem0", "backend_config": {"url": "http://127.0.0.1:8888"}, "secrets": {"api_key": "super-secret-key"}})
    assert out["secrets_set"] == ["api_key"] and out["backend_cloud"] is False and "super-secret-key" not in json.dumps(out)
    assert svc.remote().kind == "mem0"
    assert svc.set_config({"secrets": {"api_key": ""}})["secrets_set"] == []
    with pytest.raises(MemoryError_, match="Server address is required"):
        svc.set_config({"backend": "mem0", "backend_config": {}})
    assert svc.config()["backend"] == "mem0"                          # a refused change is not saved
    state.db.set_setting("memory_cloud_secrets", "bad")
    assert svc._secrets() == {}
    state.db.set_setting("memory_cloud_secrets", "[1]")
    assert svc._secrets() == {}
    assert {k["id"] for k in svc.kinds()} == {"local", "vector", "vertex", "agentcore", "mem0"} and next(k for k in svc.kinds() if k["id"] == "vertex")["cloud"] is True
    state.db.set_setting("memory_config", json.dumps({"backend": "gone"}))
    assert svc.public_config()["backend_label"] == "On this PC"


def test_health_reports_the_backend_state(svc):
    assert run(svc.health()) == {"ok": True, "detail": "Memories are kept on this PC", "backend": "local", "cloud": False}
    svc.db.set_setting("memory_config", json.dumps({"backend": "mem0", "backend_config": {}}))
    h = run(svc.health())
    assert h["ok"] is False and "Server address is required" in h["detail"] and h["backend"] == "mem0"


# ------------------------------------------------------------------------------------------------ service: saving
def test_saving_validates_and_removes_personal_data(svc):
    with pytest.raises(MemoryError_, match="kind must be"):
        add(svc, type="working")
    with pytest.raises(MemoryError_, match="scope must be"):
        add(svc, scope="moon")
    with pytest.raises(MemoryError_, match="empty"):
        add(svc, "   ")
    with pytest.raises(MemoryError_, match="too long"):
        add(svc, "x" * 2001)
    svc.set_config({"types": {"semantic": {"enabled": False}}})
    with pytest.raises(MemoryError_, match="switched off"):
        add(svc)
    svc.set_config({"types": {"semantic": {"enabled": True}}, "enabled": False})
    with pytest.raises(MemoryError_, match="switched off in Memory settings"):
        add(svc)
    svc.set_config({"enabled": True})
    r = add(svc, "Reach Bob at bob@example.com", data={"note": "card 4111 1111 1111 1111", "list": ["a@b.co", 3], "n": 1}, agent="eng", session="s1", run="r1", origin="extractor")
    assert "bob@example.com" not in r["text"] and r["redacted"] == ["email"] and r["source"] == {"agent": "eng", "session": "s1", "run": "r1", "origin": "extractor"}
    assert "4111" not in json.dumps(r["data"]) and "a@b.co" not in json.dumps(r["data"]) and r["data"]["n"] == 1 and r["duplicate"] is False
    svc.set_config({"redact_pii": False})
    assert "carol@example.com" in add(svc, "Mail carol@example.com")["text"]


def test_duplicates_and_contradictions(vsvc):
    a = add(vsvc, "The user prefers TypeScript", confidence=0.5)
    b = add(vsvc, "the user PREFERS typescript!", confidence=0.9)
    assert b["duplicate"] is True and b["id"] == a["id"] and b["confidence"] == 0.9                       # exact (normalised) duplicate: strengthened
    near = add(vsvc, "The user prefers TypeScript a lot", sid="other")                                     # a different scope is a different memory
    assert near["duplicate"] is False
    vsvc.set_config({"consolidation": {"similarity": 0.85}})
    sim = add(vsvc, "user prefers typescript", confidence=0.3)                                            # fuzzy duplicate through the embedding
    assert sim["duplicate"] is True and sim["id"] == a["id"]
    old = add(vsvc, "The user's timezone is Europe/Paris", key="user.timezone")
    new = add(vsvc, "The user's timezone is Asia/Tokyo", key="user.timezone")
    assert new["superseded"] == [old["id"]] and vsvc.get(old["id"])["status"] == "superseded" and vsvc.get(new["id"])["supersedes"] == old["id"]
    assert [r["text"] for r in run(vsvc.search("timezone", scopes=[U]))] == ["The user's timezone is Asia/Tokyo"]
    vsvc.set_config({"consolidation": {"merge_contradictions": False}})
    other = add(vsvc, "The user's timezone is Africa/Cairo", key="user.timezone")
    assert other["superseded"] == [] and vsvc.get(new["id"])["status"] == "active"
    vsvc.set_config({"consolidation": {"dedupe": False}})
    assert add(vsvc, "user prefers typescript", sid="third")["duplicate"] is False


def test_the_oldest_least_used_memories_are_evicted_when_a_kind_is_full(svc):
    svc.set_config({"types": {"semantic": {"max_items": 10}}})
    ids = [add(svc, f"fact number {i} about topic {i * 7}")["id"] for i in range(10)]
    svc.local.touch([ids[0]])                                              # used, so it survives
    last = add(svc, "the eleventh fact")
    assert last["evicted"] == 1 and svc.get(ids[0])["id"] == ids[0]
    with pytest.raises(MemoryNotFound):
        svc.get(ids[1])


def test_entities_are_updated_in_place_and_keep_their_history(vsvc):
    a = run(vsvc.save_entity(name="Alice", kind="person", attributes={"role": "CTO", "team": "core"}, scope="workspace", scope_id="w1"))
    assert a["text"] == "Alice (person): role = CTO; team = core" and a["data"]["attrs"] == {"role": "CTO", "team": "core"}
    b = run(vsvc.save_entity(name="alice", kind="", attributes={"role": "CEO", "city": "Paris"}, scope="workspace", scope_id="w1"))
    assert b["id"] == a["id"] and b["updated_in_place"] is True and b["data"]["attrs"] == {"role": "CEO", "team": "core", "city": "Paris"}
    assert b["data"]["history"][0]["attr"] == "role" and b["data"]["history"][0]["old"] == "CTO" and "Paris" in b["text"]
    assert len(vsvc.list(type="entity")) == 1
    c = run(vsvc.save_entity(name="Bob", kind="person", attributes={}, scope="user", scope_id=M.USER_ID, status="pending"))
    assert c["text"] == "Bob (person)" and c["status"] == "pending"
    vsvc.set_config({"redact_pii": True})
    d = run(vsvc.save_entity(name="Alice", kind="person", attributes={"email": "alice@example.com"}, scope="workspace", scope_id="w1"))
    assert "alice@example.com" not in d["text"]
    vsvc.set_config({"types": {"entity": {"enabled": False}}})
    with pytest.raises(MemoryError_):
        run(vsvc.save_entity(name="X", kind="thing", attributes={}, scope="user", scope_id="u"))


# ------------------------------------------------------------------------------------------------ service: review, edit, forget
def test_review_queue_approve_edit_reject(vsvc):
    p = add(vsvc, "The user's role is engineer", key="user.role", status="pending")
    assert p["status"] == "pending" and run(vsvc.search("role", scopes=[U])) == [] and vsvc.stats()["pending"] == 1
    act = add(vsvc, "The user's role is designer", key="user.role")
    ok = run(vsvc.approve(p["id"], "The user's role is engineer at carol@example.com"))
    assert ok["status"] == "active" and ok["superseded"] == [act["id"]] and "carol@example.com" not in ok["text"] and "email" in ok["redacted"] and ok["embedded"]
    with pytest.raises(MemoryError_, match="waiting for review"):
        run(vsvc.approve(p["id"]))
    q = add(vsvc, "A maybe fact", status="pending")
    assert run(vsvc.reject(q["id"]))["status"] == "rejected" and vsvc.list(status="rejected")[0]["id"] == q["id"]
    with pytest.raises(MemoryError_, match="waiting for review"):
        run(vsvc.reject(q["id"]))
    r = add(vsvc, "Another fact", status="pending")
    with pytest.raises(MemoryError_, match="too long"):
        run(vsvc.approve(r["id"], "x" * 2001))
    assert run(vsvc.approve(r["id"], "   "))["text"] == "Another fact"                     # a blank edit keeps the suggestion
    with pytest.raises(MemoryNotFound):
        run(vsvc.approve("m_missing"))
    vsvc.set_config({"redact_pii": False})
    s = add(vsvc, "Yet another fact", status="pending")
    assert run(vsvc.approve(s["id"], "Edited fact"))["text"] == "Edited fact"


def test_editing_a_memory(vsvc):
    a = add(vsvc, "Original fact")
    out = run(vsvc.update(a["id"], text="Better fact for dave@example.com", confidence=2, ttl_days=10))
    assert "dave@example.com" not in out["text"] and out["confidence"] == 1.0 and out["expires"] > time.time() and out["embedded"]
    assert run(vsvc.update(a["id"], ttl_days=0))["expires"] is None and run(vsvc.update(a["id"]))["id"] == a["id"]
    for bad in ("", "x" * 2001):
        with pytest.raises(MemoryError_, match="1 to 2000"):
            run(vsvc.update(a["id"], text=bad))
    p = add(vsvc, "Pending one", status="pending")
    assert run(vsvc.update(p["id"], text="Pending edited"))["embedded"] is False             # not embedded until it is approved
    vsvc.set_config({"redact_pii": False})
    assert run(vsvc.update(a["id"], text="Plain edit"))["text"] == "Plain edit"


def test_forgetting_by_id_by_description_and_in_dry_run(vsvc):
    a, b = add(vsvc, "The user likes green tea"), add(vsvc, "The deploy script is make ship", sid="other")
    with pytest.raises(MemoryError_, match="not allowed"):
        run(vsvc.forget(memory_id=b["id"], scopes=[U]))
    with pytest.raises(MemoryError_, match="say which"):
        run(vsvc.forget())
    dry = run(vsvc.forget(query="green tea", scopes=[U], dry_run=True))
    assert [f["id"] for f in dry["forgotten"]] == [a["id"]] and dry["dry_run"] and vsvc.get(a["id"])
    gone = run(vsvc.forget(query="green tea", scopes=[U], dry_run=False))
    assert gone["forgotten"][0]["text"] == "The user likes green tea"
    with pytest.raises(MemoryNotFound):
        vsvc.get(a["id"])
    assert run(vsvc.forget(memory_id=b["id"], dry_run=False))["forgotten"][0]["id"] == b["id"]
    assert run(vsvc.forget(query="nothing relevant at all", scopes=[U]))["forgotten"] == []
    assert run(vsvc.forget(query="x", scopes=None))["forgotten"] == []
    assert run(vsvc.delete("m_nope")) is False


def test_wipe_and_maintenance(svc):
    a = add(svc, "expires soon", ttl_days=1)
    svc.local.update(a["id"], expires=time.time() - 1)
    r = add(svc, "rejected long ago", status="pending")
    run(svc.reject(r["id"]))
    svc.local.update(r["id"], updated=time.time() - 40 * 86400)
    keep = add(svc, "keep me")
    svc.local.insert(dict(id="m_dupe1", type="semantic", scope="user", scope_id=M.USER_ID, text="keep me", created=time.time() + 1, updated=time.time()))
    assert run(svc.maintain()) == {"expired": 1, "rejected_removed": 1, "duplicates_merged": 1}
    assert [m["id"] for m in svc.list()] == [keep["id"]]
    svc.set_config({"consolidation": {"dedupe": False}})
    assert run(svc.maintain())["duplicates_merged"] == 0
    svc._last_maintenance = time.time()
    run(svc.maintain_if_due())
    svc._last_maintenance = 0
    run(svc.maintain_if_due())
    assert svc._last_maintenance > 0
    assert run(svc.wipe("agent")) == 0 and run(svc.wipe()) == 1 and svc.list() == []


def test_export_and_import_round_trip(svc, state):
    a = add(svc, "Fact one", key="k1")
    p = add(svc, "Pending two", status="pending")
    run(svc.reject(add(svc, "Rejected three", status="pending")["id"]))
    doc = svc.export()
    assert doc["format"] == "agent-studio-memories" and {m["id"] for m in doc["memories"]} >= {a["id"], p["id"]} and "embedding" not in json.dumps(doc)
    run(svc.wipe())
    res = run(svc.import_(doc))
    assert res["added"] == 2 and res["skipped"] == 1 and res["errors"] == []                 # the rejected one is not imported
    assert {m["text"] for m in svc.list()} == {"Fact one", "Pending two"} and {m["status"] for m in svc.list()} == {"active", "pending"}
    assert run(svc.import_(doc))["skipped"] == 3 and run(svc.import_(doc))["added"] == 0           # again: duplicates are skipped
    bad = run(svc.import_({"format": "agent-studio-memories", "memories": ["x", {"type": "nope", "scope": "user", "text": "t"}, {"type": "semantic", "scope": "user", "text": "", "status": "active"},
                                                                         {"type": "semantic", "scope": "user", "text": "ok", "status": "active", "confidence": "high"}]}))
    assert bad["added"] == 0 and len(bad["errors"]) == 4
    with pytest.raises(MemoryError_, match="not an Agent Studio memories file"):
        run(svc.import_({"format": "x"}))


# ------------------------------------------------------------------------------------------------ service: searching
def test_search_by_words_and_by_meaning_and_fallbacks(vsvc):
    a = add(vsvc, "The billing system is Xero")
    add(vsvc, "Deploys happen on Fridays")
    add(vsvc, "Old note", type="episodic")
    warns: list[str] = []
    hits = run(vsvc.search("what is the billing system", scopes=[U], types=["semantic"], warnings=warns))
    assert hits[0]["id"] == a["id"] and vsvc.get(a["id"])["hits"] == 1 and warns == []
    assert run(vsvc.search("billing", scopes=[U], types=["episodic"])) == [] and run(vsvc.search("   ", scopes=[U])) == []
    assert run(vsvc.search("billing", scopes=[U], types=["working"])) == []
    assert run(vsvc.search("billing system", scopes=[U], record_hits=False, min_score=0))[0]["id"] == a["id"]
    vsvc.set_config({"types": {"semantic": {"enabled": False}}})
    assert run(vsvc.search("billing", scopes=[U], types=["semantic"])) == []
    vsvc.set_config({"types": {"semantic": {"enabled": True}}, "enabled": False})
    assert run(vsvc.search("billing", scopes=[U])) == []
    vsvc.set_config({"enabled": True})

    class Capped:
        async def embed(self, *a):
            raise BudgetExceeded({"message": "cap"})

    class Broken:
        async def embed(self, *a):
            raise EmbeddingError("model gone")
    for emb, word in ((Capped(), "spend cap"), (Broken(), "model gone")):
        vsvc.embedder = emb
        w: list[str] = []
        assert run(vsvc.search("billing system", scopes=[U], warnings=w))[0]["id"] == a["id"] and word in w[0]          # words only, with a note
        assert run(vsvc.search("billing system", scopes=[U]))[0]["id"] == a["id"]                                       # no warnings list: still fine
    assert run(vsvc._embed(["x"])) == (None, "")                     # nothing to embed with... (embedder is broken) - and no warnings list to fill
    vsvc.set_config({"embedding": {"provider": "", "model": ""}})
    assert run(vsvc._embed(["x"])) == (None, "") and run(vsvc._embed([])) == (None, "")


# ------------------------------------------------------------------------------------------------ notes in the workspace
def test_workspace_notes(svc, tmp_path):
    ws = tmp_path / "proj"
    assert svc.read_notes(ws) == "" and svc.notes_path(ws).name == "AGENTS.md"
    assert svc.write_notes(ws, "# Notes\n") == 8 and svc.read_notes(ws) == "# Notes\n"
    svc.append_note(ws, "Use pnpm. Mail me at x@example.com")
    text = svc.read_notes(ws)
    assert text.startswith("# Notes\n- ") and "Use pnpm" in text and "x@example.com" not in text
    svc.write_notes(ws, "no newline")
    svc.append_note(ws, "second")
    assert svc.read_notes(ws).startswith("no newline\n- ")
    for fn in (lambda: svc.append_note(ws, "  "), lambda: svc.write_notes(ws, "x" * 200_001)):
        with pytest.raises(MemoryError_):
            fn()
    svc.set_config({"redact_pii": False})
    svc.append_note(ws, "keep y@example.com")
    assert "y@example.com" in svc.read_notes(ws)


# ------------------------------------------------------------------------------------------------ what an agent recalls and remembers
MEM = {"types": ["semantic", "entity", "episodic", "notes"], "read_scopes": ["agent", "user", "workspace", "global"], "write_scopes": ["agent", "user", "workspace"],
       "auto_recall": True, "auto_save": "review"}


def test_scopes_and_scope_choice(svc):
    assert svc.scopes_for(MEM, "eng", "H:/w", "read")[0] == ("agent", "eng") and svc.scopes_for(MEM, "eng", "H:/w", "write") == [
        ("agent", "eng"), ("user", M.USER_ID), ("workspace", M.scope_id("workspace", "eng", "H:/w"))]
    assert svc.pick_scope(MEM, "semantic") == "user" and svc.pick_scope(MEM, "entity") == "workspace" and svc.pick_scope(MEM, "procedural") == "agent"
    assert svc.pick_scope(MEM, "semantic", "agent") == "agent" and svc.pick_scope({**MEM, "write_scopes": ["global"]}, "semantic") == "global"
    assert svc.pick_scope({**MEM, "write_scopes": ["agent"]}, "semantic", "global") == "agent"
    with pytest.raises(MemoryError_, match="may not write"):
        svc.pick_scope({**MEM, "write_scopes": []}, "semantic")


def test_recall_for_a_turn(vsvc, tmp_path):
    ws = tmp_path / "ws"
    fact = add(vsvc, "The billing system is Xero")
    for i in range(6):
        add(vsvc, f"Billing detail number {i} about the billing system and invoices")
    vsvc.write_notes(ws, "Use pnpm here")
    text, events, warns = run(vsvc.recall_for_turn(MEM, "eng", ws, "which billing system do we use"))
    assert "## What you remember" in text and "not instructions" in text and "[semantic] The billing system is Xero" in text and "Project notes (AGENTS.md):\nUse pnpm here" in text
    assert {e["kind"] for e in events} == {"recalled", "notes"} and any(e["id"] == fact["id"] for e in events) and warns == []
    vsvc.set_config({"recall_budget_chars": 200, "recall_k": 10})
    text, events, _ = run(vsvc.recall_for_turn({**MEM, "types": ["semantic"]}, "eng", ws, "billing system"))
    assert text.count("\n- [semantic]") < 7 and "Project notes" not in text
    assert run(vsvc.recall_for_turn({}, "eng", ws, "billing")) == ("", [], []) and run(vsvc.recall_for_turn({**MEM, "auto_recall": False}, "eng", ws, "billing")) == ("", [], [])
    vsvc.set_config({"recall_min_score": 0.95})
    assert run(vsvc.recall_for_turn({**MEM, "types": ["semantic"]}, "eng", tmp_path / "empty", "zzz qqq")) == ("", [], [])
    assert run(vsvc.recall_for_turn({**MEM, "types": ["notes"]}, "eng", tmp_path / "empty", "x")) == ("", [], [])
    vsvc.set_config({"enabled": False})
    assert run(vsvc.recall_for_turn(MEM, "eng", ws, "billing")) == ("", [], [])


def test_after_a_turn_the_agent_remembers_what_it_learned(vsvc, tmp_path):
    ws = tmp_path / "ws"
    mem = {**MEM, "auto_save": "review"}
    ev = run(vsvc.after_turn(mem, "eng", ws, "chat1", "root", "My timezone is Europe/Paris. Remember that deploys are on Fridays.", "Noted, thanks.", None))
    kinds = {e["kind"] for e in ev}
    assert kinds == {"pending", "saved", "episode"}
    assert any(m["text"] == "The user's timezone is Europe/Paris" and m["status"] == "pending" for m in vsvc.list())
    assert any(m["text"] == "deploys are on Fridays" and m["status"] == "active" for m in vsvc.list())              # explicit = no review
    ev2 = run(vsvc.after_turn(mem, "eng", ws, "chat1", "root", "More about Acme", "Sure, here is more about Acme.", None))
    assert [e["kind"] for e in ev2] == ["episode"] and len(vsvc.list(type="episodic")) == 1
    assert "more about acme" in vsvc.list(type="episodic")[0]["text"].lower()
    ev3 = run(vsvc.after_turn({**mem, "auto_save": "auto"}, "eng", ws, "c2", "root", "My timezone is Europe/Paris", "ok", None))
    assert ev3[0]["kind"] == "duplicate"
    run(vsvc.after_turn({**mem, "auto_save": "auto"}, "eng", ws, "c3", "root", "My role is architect", "ok", None))
    assert any(m["text"] == "The user's role is architect" and m["status"] == "active" for m in vsvc.list())
    vsvc.set_config({"review": True})
    run(vsvc.after_turn({**mem, "auto_save": "auto"}, "eng", ws, "c4", "root", "My city is Lyon", "ok", None))
    assert any(m["text"] == "The user's city is Lyon" and m["status"] == "pending" for m in vsvc.list())              # the global review switch wins
    assert run(vsvc.after_turn({**mem, "auto_save": "off"}, "eng", ws, "c", "r", "remember that x", "y", None)) == [] and run(vsvc.after_turn({}, "eng", ws, "c", "r", "x", "y", None)) == []
    vsvc.set_config({"auto_extract": "off"})
    assert [e["kind"] for e in run(vsvc.after_turn(mem, "eng", ws, "c5", "r", "My city is Rome", "ok", None))] == ["episode"]
    only_sem = {**mem, "types": ["semantic"]}
    assert run(vsvc.after_turn(only_sem, "eng", ws, "c6", "r", "remember that tea is good", "ok", None))[0]["kind"] == "saved"
    assert run(vsvc.after_turn({**mem, "types": ["entity"]}, "eng", ws, "c7", "r", "remember that tea is good", "ok", None)) == []           # a kind it does not use
    no_write = {**mem, "write_scopes": []}
    assert run(vsvc.after_turn(no_write, "eng", ws, "c8", "r", "remember that x is y", "ok", None))[0]["kind"] == "skipped"
    vsvc.set_config({"types": {"episodic": {"enabled": False}}})
    assert run(vsvc.after_turn(mem, "eng", ws, "c9", "r", "hello", "reply here", None)) == []                   # the episode is skipped quietly


def test_model_assisted_extraction_and_its_failures(vsvc, tmp_path):
    vsvc.set_config({"auto_extract": "model"})
    mem = {**MEM, "auto_save": "auto", "types": [*MEM["types"], "procedural"]}
    ws = tmp_path / "ws"

    async def good(prompt):
        assert "User said" in prompt
        return json.dumps([{"type": "entity", "text": "x", "name": "Dana", "kind": "person", "attributes": {"role": "COO"}}, {"type": "procedural", "text": "Run make ship to deploy"}])
    ev = run(vsvc.after_turn(mem, "eng", ws, "m1", "root", "Dana is our COO and we deploy with make ship", "ok", good))
    assert {e["type"] for e in ev if "type" in e} >= {"entity", "procedural"} and any(m["text"].startswith("Dana (person)") for m in vsvc.list())

    async def capped(prompt):
        raise BudgetExceeded({"message": "cap"})

    async def broken(prompt):
        raise RuntimeError("model exploded")
    ev = run(vsvc.after_turn(mem, "eng", ws, "m2", "root", "hello there", "a reply", capped))
    assert any("spend cap" in e.get("text", "") for e in ev)
    ev = run(vsvc.after_turn(mem, "eng", ws, "m3", "root", "hello there", "a reply", broken))
    assert any("model exploded" in e.get("text", "") for e in ev)
    assert [e["kind"] for e in run(vsvc.after_turn(mem, "eng", ws, "m4", "root", "hello", "reply", None))] == ["episode"]
