"""The OpenAPI reader (3.0 / 3.1 / Swagger 2.0, JSON + YAML, $ref, limits) and imported APIs: upload/paste/URL, endpoints CRUD, refresh diff, test calls, REST API."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.workflows import core as W
from app.workflows import openapi_spec as OS
from app.workflows.sources import InUse, OpenApiSources

run = asyncio.run

PETS3 = {
    "openapi": "3.0.3", "info": {"title": "Pet Store", "version": "1.2"},
    "servers": [{"url": "https://{env}.pets.test/v1", "description": "main", "variables": {"env": {"default": "api"}}}, {"url": "/rel"}, "junk"],
    "components": {
        "securitySchemes": {"tok": {"type": "http", "scheme": "bearer"}, "key": {"type": "apiKey", "in": "query", "name": "api_key"}, "hdr": {"type": "apiKey", "in": "header", "name": "X-Key"},
                            "basic": {"type": "http", "scheme": "basic"}, "weird": {"type": "openIdConnect"}, "bad": "x"},
        "parameters": {"Limit": {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10, "enum": [5, 10, {"x": 1}]}, "description": "How many"}},
        "schemas": {"Pet": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "tag": {"type": ["string", "null"]}, "kind": {"$ref": "#/components/schemas/Kind"}}},
                    "Kind": {"type": "string", "enum": ["cat", "dog"]}, "Loop": {"$ref": "#/components/schemas/Loop"},
                    "Both": {"allOf": [{"$ref": "#/components/schemas/Pet"}, {"properties": {"age": {"type": "integer"}}, "required": ["age"]}]}},
        "requestBodies": {"PetBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}},
    },
    "paths": {
        "/pets": {"parameters": [{"$ref": "#/components/parameters/Limit"}],
                  "get": {"operationId": "listPets", "summary": "List", "tags": ["pets"], "responses": {"200": {"description": "ok"}, "default": {"description": "x"}}},
                  "post": {"operationId": "addPet", "description": "Add one", "tags": ["pets"], "requestBody": {"$ref": "#/components/requestBodies/PetBody"}, "security": [{"tok": []}]}},
        "/pets/{id}": {"get": {"operationId": "getPet", "parameters": [{"name": "id", "in": "path", "schema": {"type": "string"}}, {"name": "X-Trace", "in": "header"}, {"name": "c", "in": "cookie"}],
                               "deprecated": True},
                       "delete": {"operationId": "getPet", "summary": "duplicate id"},
                       "put": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Both"}}}}},
                       "patch": {"requestBody": {"content": {"text/plain": {"schema": {"type": "string"}}}}}},
        "/loop": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Loop"}}}}}},
        "/ext": {"post": {"parameters": [{"$ref": "https://evil.example/p.json"}, {"$ref": "#/components/nope"}], "operationId": "ext"}},
        "bad-path": {"get": {}}, "/skip": "not a dict",
    },
}
SWAGGER2 = {
    "swagger": "2.0", "info": {"title": "Old API", "version": "1"}, "host": "old.test", "basePath": "/api", "schemes": ["https", "http"],
    "securityDefinitions": {"b": {"type": "basic"}, "k": {"type": "apiKey", "in": "header", "name": "X-Api"}},
    "paths": {"/items": {"get": {"operationId": "items", "parameters": [{"name": "page", "in": "query", "type": "integer", "default": 1}, {"name": "f", "in": "formData", "type": "string"}]},
                         "post": {"operationId": "makeItem", "parameters": [{"name": "body", "in": "body", "schema": {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}, "b": {"type": "number"}}}}]}}},
}


def text(d):
    return json.dumps(d)


# ---------------------------------------------------------------------------------------------------- the reader
def test_openapi3_is_read_in_detail():
    s = OS.parse_text(text(PETS3), "pets.json")
    assert s["title"] == "Pet Store" and s["version"] == "1.2" and s["spec_version"] == "3.0.3"
    assert s["servers"] == [dict(url="https://api.pets.test/v1", description="main"), dict(url="/rel", description="")]
    assert [x["type"] for x in s["security"]] == ["bearer", "query", "header", "basic", "header"] and s["security"][1]["header"] == "api_key"
    ops = s["ops"]
    lp = ops["listpets"]
    assert lp["tags"] == ["pets"] and lp["responses"] == ["200", "default"] and lp["parameters"] == [
        dict(name="limit", **{"in": "query"}, required=False, type="integer", enum=[5, 10], default=10, description="How many")]
    assert ops["addpet"]["body"] == ["kind", "name", "tag"] and ops["addpet"]["secured"] is True and ops["addpet"]["description"] == "Add one"
    fields = {f["name"]: f for f in ops["addpet"]["body_fields"]}
    assert fields["name"]["required"] and fields["tag"]["type"] == "string" and fields["kind"]["enum"] == ["cat", "dog"]
    assert ops["getpet"]["deprecated"] and [p["name"] for p in ops["getpet"]["parameters"]] == ["id"] and ops["getpet"]["parameters"][0]["required"]
    assert "getpet_2" in ops and ops["getpet_2"]["method"] == "delete"                 # a duplicate id is renamed, not overwritten
    assert [f["name"] for f in ops["put_pets_id"]["body_fields"]] == ["age", "kind", "name", "tag"] or {f["name"] for f in ops["put_pets_id"]["body_fields"]} == {"name", "tag", "kind", "age"}
    assert ops["patch_pets_id"]["body"] == ["(body)"] and ops["patch_pets_id"]["body_type"] == "text/plain"
    assert ops["post_loop"]["body"] is None or ops["post_loop"]["body"] == ["(body)"]  # a self-referencing schema ends instead of looping forever
    assert any("external reference" in w for w in s["warnings"]) and any("points at nothing" in w for w in s["warnings"]) and any("change data" in w for w in s["warnings"])
    assert "bad-path" not in json.dumps(list(ops))


def test_swagger2_json_and_yaml():
    s = OS.parse_text(text(SWAGGER2))
    assert s["servers"] == [dict(url="https://old.test/api", description=""), dict(url="http://old.test/api", description="")] and s["spec_version"] == "2.0"
    assert [x["type"] for x in s["security"]] == ["basic", "header"]
    assert [f["name"] for f in s["ops"]["makeitem"]["body_fields"]] == ["a", "b"] and s["ops"]["makeitem"]["body_fields"][0]["required"]
    assert s["ops"]["items"]["parameters"][0]["type"] == "integer" and s["ops"]["items"]["parameters"][1]["in"] == "query"
    yml = "openapi: 3.1.0\ninfo:\n  title: Y\npaths:\n  /a:\n    get:\n      summary: A\n"
    y = OS.parse_text(yml, "y.yaml")
    assert y["title"] == "Y" and y["servers"] == [] and any("names no server" in w for w in y["warnings"]) and "get_a" in y["ops"]
    assert OS.parse_text(text({"swagger": "2.0", "basePath": "/v2", "paths": {"/a": {"get": {}}}}))["servers"] == [dict(url="/v2", description="")]
    assert OS.parse_text(text({"openapi": "3.0.0", "paths": {"/a": {"get": {}}}}), "f.json")["title"] == "f.json"


def test_rejects_hostile_or_unusable_input():
    for bad, msg in [("", "empty"), ("[1, 2", "neither valid JSON nor valid YAML"), ("x: [", "neither valid JSON nor valid YAML"), ("{}", "not an OpenAPI"), ("[]", "not an OpenAPI"),
                     (text({"openapi": "3.0.0", "paths": {}}), "no operations"), (text({"openapi": "3.0.0", "paths": {"/a": {"get": {}}} | {f"/p{i}": {} for i in range(5000)}}), "too many paths")]:
        with pytest.raises(OS.SpecError, match=msg):
            OS.parse_text(bad)
    with pytest.raises(OS.SpecError, match="larger than 5 MB"):
        OS.parse_text("a" * 5_000_001)
    many = {"openapi": "3.0.0", "paths": {f"/p{i}": {"get": {}, "post": {}} for i in range(1100)}}
    assert len(OS.parse_text(text(many))["ops"]) == 2000 and any("first 2000" in w for w in OS.parse_text(text(many))["warnings"])


def test_refs_pointer_edge_cases():
    doc = {"openapi": "3.0.0", "paths": {"/a": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/c/list/0"}}}}}}},
           "c": {"list": [{"properties": {"x~y/z": {"type": "string"}}}], "a~1b": {"properties": {"q": {"type": "integer"}}}}}
    f = OS.parse_text(text(doc))["ops"]["post_a"]["body_fields"]
    assert [x["name"] for x in f] == ["x~y/z"]
    doc["c"]["list"] = []
    doc["paths"]["/a"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {"$ref": "#/c/list/3"}
    assert OS.parse_text(text(doc))["ops"]["post_a"]["body"] == ["(body)"]
    deep = {"openapi": "3.0.0", "paths": {"/a": {"post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": "#/s/n0"}}}}}}},
            "s": {f"n{i}": {"$ref": f"#/s/n{i + 1}"} for i in range(12)}}
    assert OS.parse_text(text(deep))["ops"]["post_a"]["body"] == ["(body)"]                 # a chain deeper than the limit stops quietly
    assert OS._schema_info(OS._Refs({}, []), 5) == {"type": "any"} and OS._schema_info(OS._Refs({}, []), {"items": {"type": "string"}})["type"] == "array"
    assert OS._schema_info(OS._Refs({}, []), {"type": "array", "items": {"type": "integer"}})["items"] == "integer"
    assert OS._props(OS._Refs({}, []), {"properties": {"a": {}}}, depth=4) == ({}, set())


def test_pick_base_and_diff():
    spec = {"servers": [dict(url="/v1", description="")]}
    assert OS.pick_base(spec, "https://o.example/", "") == "https://o.example"
    assert OS.pick_base(spec, "", "https://host.test/docs/openapi.json") == "https://host.test/v1"
    with pytest.raises(OS.SpecError, match="relative"):
        OS.pick_base(spec, "", "file.json")
    with pytest.raises(OS.SpecError, match="names no server"):
        OS.pick_base({"servers": []})
    old = {"a": dict(method="get", path="/a", parameters=[]), "b": dict(method="get", path="/b", parameters=[]), "c": dict(method="get", path="/c", parameters=[], body=["x"])}
    new = {"a": dict(method="get", path="/a", parameters=[]), "c": dict(method="get", path="/c", parameters=[], body=["y"]), "d": dict(method="get", path="/d", parameters=[])}
    assert OS.diff_ops(old, new) == dict(added=["d"], removed=["b"], changed=["c"])


# ---------------------------------------------------------------------------------------------------- imported APIs
def api_transport(seen):
    def handler(req: httpx.Request):
        seen.append((req.method, str(req.url), dict(req.headers), req.content.decode()))
        if req.url.path.endswith("/text"):
            return httpx.Response(200, text="plain")
        if req.url.path.endswith("/boom"):
            raise httpx.ConnectError("no")
        if req.url.path.endswith("/big"):
            return httpx.Response(200, json={"x": "y" * 30_000})
        return httpx.Response(200, json={"ok": 1})
    return httpx.MockTransport(handler)


@pytest.fixture
def src(state):
    seen: list = []
    used: dict = {}
    s = OpenApiSources(state.db, api_transport(seen), usage=lambda sid, op=None: used.get((sid, op), used.get((sid, None), [])) if op is None else used.get((sid, op), []), clock=iter(range(1000, 2000)).__next__)
    s.seen, s.used = seen, used
    return s


def test_preview_add_from_paste_upload_and_validation(src):
    with pytest.raises(W.WorkflowError, match="upload a file"):
        src.preview()
    p = src.preview(content=text(PETS3), filename="pets.json")
    assert p["title"] == "Pet Store" and p["counts"]["total"] == len(OS.parse_text(text(PETS3))["ops"]) and p["counts"]["tags"]["pets"] == 2 and p["suggested_id"] == "pet_store"
    assert p["suggested_auth"]["type"] == "bearer" and p["base"] == "https://api.pets.test/v1" and len(p["operations"]) == p["counts"]["total"]
    assert src.preview(content=text(SWAGGER2))["suggested_auth"]["type"] == "basic"
    assert src.preview(content=text({"openapi": "3.0.0", "servers": [{"url": "https://x.test"}], "paths": {"/a": {"get": {}}}}))["suggested_auth"] == dict(type="none", name="", header="")
    with pytest.raises(W.WorkflowError, match="could not read"):
        src.preview(content="nope")
    with pytest.raises(W.WorkflowError, match="could not read"):
        src.preview(content=text({"openapi": "3.0.0", "paths": {"/a": {"get": {}}}}))             # no server and no override
    a = src.add("pets", "Pets", content=text(PETS3), filename="pets.json", token="T0K", auth={"type": "bearer"}, allow_write=True)
    assert a["origin"] == "upload" and a["has_token"] and "T0K" not in json.dumps(a) and a["auth"] == dict(type="bearer", name="Authorization") and a["title"] == "Pet Store"
    assert a["counts"]["total"] == len(a["operations"]) and a["counts"]["write"] > 0 and a["warnings"] and a["servers"][0]["url"] == "https://api.pets.test/v1"
    b = src.add("pasted", "", content=text(SWAGGER2))
    assert b["origin"] == "paste" and b["name"] == "pasted" and b["src"] == "pasted"
    with pytest.raises(W.WorkflowError, match="sign-in type"):
        src.add("x1", "", content=text(SWAGGER2), auth={"type": "magic"})
    with pytest.raises(W.WorkflowError, match="header or query-parameter name"):
        src.add("x1", "", content=text(SWAGGER2), auth={"type": "header", "name": "bad name!"})
    with pytest.raises(W.WorkflowError, match="already imported"):
        src.add("pets", "", content=text(SWAGGER2))
    with pytest.raises(W.WorkflowError, match="API id must be"):
        src.add("Bad Id", "", content=text(SWAGGER2))
    with pytest.raises(W.WorkflowError, match="give the address"):
        src.add("zz", "n")
    assert src.add("viafile", "n", content=text(SWAGGER2), origin="url")["origin"] == "url"


def test_url_and_file_import_and_default_reader(src, tmp_path, monkeypatch):
    f = tmp_path / "s.yaml"
    f.write_text("openapi: 3.0.0\ninfo: {title: F}\nservers: [{url: 'https://f.test'}]\npaths: {/a: {get: {operationId: a}}}\n")
    a = src.add("fromfile", "F", str(f))
    assert a["origin"] == "file" and a["base"] == "https://f.test"
    calls = []

    class FakeClient:
        def __init__(self, **kw): calls.append(kw)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            return httpx.Response(200, text=text({"openapi": "3.0.0", "servers": [{"url": "/api"}], "paths": {"/z": {"get": {}}}}), request=httpx.Request("GET", url))
    monkeypatch.setattr("app.workflows.sources.httpx.Client", FakeClient)
    u = src.add("fromurl", "U", "https://docs.test/openapi.json")
    assert u["origin"] == "url" and u["base"] == "https://docs.test/api" and calls[0]["follow_redirects"] is True
    monkeypatch.setattr(FakeClient, "get", lambda self, url: httpx.Response(404, request=httpx.Request("GET", url)))
    with pytest.raises(W.WorkflowError, match="could not read"):
        src.add("gone", "G", "https://docs.test/missing.json")


def test_settings_auth_duplicate_delete_and_export(src):
    src.add("pets", "Pets", content=text(PETS3), token="s3cret", auth={"type": "query", "name": "api_key"})
    u = src.update("pets", {"name": "Pets 2", "base": "https://other.test/", "auth": {"type": "basic"}, "allow_write": True})
    assert u["base"] == "https://other.test" and u["auth"]["type"] == "basic" and u["has_token"]
    with pytest.raises(W.WorkflowError, match="must start with http"):
        src.update("pets", {"base": "ftp://x"})
    with pytest.raises(KeyError):
        src.update("nope", {})
    d = src.duplicate("pets", "pets_copy")
    assert d["name"] == "Pets 2 copy" and not d["has_token"] and d["counts"] == u["counts"] and src.duplicate("pets", "pets_b", "Named")["name"] == "Named"
    with pytest.raises(W.WorkflowError, match="API id must be"):
        src.duplicate("pets", "Bad Id")
    with pytest.raises(W.WorkflowError, match="already imported"):
        src.duplicate("pets", "pets_copy")
    assert json.loads(src.export_spec("pets"))["info"]["title"] == "Pet Store"
    src.db.execute("UPDATE openapi_sources SET spec='' WHERE id='pets_b'")
    assert json.loads(src.export_spec("pets_b"))["servers"][0]["url"] == "https://other.test"
    src.used[("pets", None)] = ["wf_a"]
    with pytest.raises(InUse, match="used by wf_a") as e:
        src.delete("pets")
    assert e.value.used_by == ["wf_a"]
    src.delete("pets", force=True)
    with pytest.raises(KeyError):
        src.delete("pets")
    src.used[("pets_copy", None)] = [f"w{i}" for i in range(9)]
    with pytest.raises(InUse, match="…"):
        src.delete("pets_copy")


def test_endpoint_crud_overrides_custom_and_delete_rules(src):
    src.add("pets", "Pets", content=text(PETS3))
    ops = src.operations("pets")
    assert len(ops) == len(OS.parse_text(text(PETS3))["ops"]) and src.operations("pets", q="addpet")[0]["id"] == "addpet"
    assert [o["id"] for o in src.operations("pets", tag="pets")] == ["listpets", "addpet"] and [o["id"] for o in src.operations("pets", method="POST")][:1] == ["addpet"]
    o = src.update_operation("pets", "listpets", {"label": "All pets", "description": "d", "enabled": False, "confirm_required": True, "defaults": {"limit": 3, "x": "y", "z": 1.5}})
    assert o["summary"] == "All pets" and o["enabled"] is False and o["defaults"]["limit"] == 3 and src.operations("pets", enabled=False)[0]["id"] == "listpets"
    with pytest.raises(W.WorkflowError, match="default arguments"):
        src.update_operation("pets", "listpets", {"defaults": [1]})
    with pytest.raises(KeyError):
        src.update_operation("pets", "ghost", {})
    c = src.create_operation("pets", dict(id="health", method="get", path="/health/{region}", label="Health", parameters=[dict(name="deep", type="boolean", default=True)],
                                          body_fields=[dict(name="note", required=True, type="string")]))
    assert c["origin"] == "custom" and [p["name"] for p in c["parameters"]] == ["deep", "region"] and c["body"] == ["note"] and c["summary"] == "Health"
    c2 = src.update_operation("pets", "health", dict(method="post", path="/h2", label="H2"))
    assert c2["method"] == "post" and c2["path"] == "/h2" and c2["risk"] == "write"
    for body, msg in [(dict(id="listpets", path="/x"), "already has an endpoint"), (dict(id="e1", method="fetch", path="/x"), "method must be"), (dict(id="e1", path="x"), "plain path"),
                      (dict(id="e1", path="/a/../b"), "plain path"), (dict(id="e1", path="/a?x=1"), "plain path"), (dict(id="e1", path="/x", parameters="no"), "list of at most 40"),
                      (dict(id="e1", path="/x", parameters=[{"name": "a b"}]), "needs a name"), (dict(id="e1", path="/x", parameters=[{"name": "a", "in": "header"}]), "needs a name"),
                      (dict(id="e1", path="/x", body_fields=[{"name": "bad name"}]), "list of at most 60")]:
        with pytest.raises(W.WorkflowError, match=msg):
            src.create_operation("pets", body)
    src.used[("pets", "addpet")] = ["wf_1"]
    with pytest.raises(InUse, match="endpoint 'addpet'"):
        src.delete_operation("pets", "addpet")
    assert src.delete_operation("pets", "addpet", force=True) == dict(ok=True, removed=False, hidden=True)
    assert "addpet" not in [o["id"] for o in src.operations("pets")] and "addpet" in [o["id"] for o in src.operations("pets", hidden=True)]
    assert src.update_operation("pets", "addpet", {"hidden": False})["hidden"] is False
    assert src.delete_operation("pets", "health") == dict(ok=True, removed=True) and src.get("pets")["counts"]["hidden"] == 0
    with pytest.raises(KeyError):
        src.delete_operation("pets", "ghost")
    with pytest.raises(KeyError):
        src.operation("pets", "ghost")
    assert src.operation("pets", "listpets")["used_by"] == []
    assert src.create_operation("pets", dict(label="Ping", path="/ping"))["id"] == "ping" and src.create_operation("pets", dict(path="/zed", method="delete"))["risk"] == "delete"


def test_calls_use_auth_defaults_and_own_server(src):
    src.add("pets", "Pets", content=text(PETS3), token="T0K", auth={"type": "bearer"}, allow_write=True)
    assert run(src.call("pets", "listpets", {"limit": 2}, False)) == {"ok": True, "status": 200, "body": {"ok": 1}}
    m, url, headers, _ = src.seen[-1]
    assert m == "GET" and url == "https://api.pets.test/v1/pets?limit=2" and headers["authorization"] == "Bearer T0K"
    src.update("pets", {"auth": {"type": "bearer"}, "token": "Bearer Already"})
    run(src.call("pets", "getpet", {"id": 1}, False))
    assert src.seen[-1][2]["authorization"] == "Bearer Already"
    src.update("pets", {"auth": {"type": "basic"}, "token": "user:pw"})
    run(src.call("pets", "getpet", {"id": 1}, False))
    assert src.seen[-1][2]["authorization"].startswith("Basic dXNlcjpwdw")
    src.update("pets", {"auth": {"type": "basic"}, "token": "Basic abc"})
    run(src.call("pets", "getpet", {"id": 1}, False))
    assert src.seen[-1][2]["authorization"] == "Basic abc"
    src.update("pets", {"auth": {"type": "header", "name": "X-Key"}, "token": "k1"})
    run(src.call("pets", "getpet", {"id": 1}, False))
    assert src.seen[-1][2]["x-key"] == "k1"
    src.update("pets", {"auth": {"type": "query", "name": "api_key"}, "token": "k2"})
    run(src.call("pets", "getpet", {"id": 1}, False))
    assert "api_key=k2" in src.seen[-1][1]
    src.update("pets", {"auth": {"type": "none"}})
    run(src.call("pets", "getpet", {"id": 1}, False))
    assert "authorization" not in src.seen[-1][2] and "api_key" not in src.seen[-1][1]
    assert "changes data" in run(src.call("pets", "addpet", {"name": "x"}, False))["error"]
    assert run(src.call("pets", "addpet", {"name": "x"}, True))["ok"] and json.loads(src.seen[-1][3]) == {"name": "x"}
    src.update_operation("pets", "listpets", {"confirm_required": True, "defaults": {"limit": 9}})
    assert "needs 'I confirm'" in run(src.call("pets", "listpets", {}, False))["error"]
    run(src.call("pets", "listpets", {}, True))
    assert "limit=9" in src.seen[-1][1]
    src.update_operation("pets", "listpets", {"enabled": False})
    assert "switched off" in run(src.call("pets", "listpets", {}, True))["error"]
    assert "missing path parameter" in run(src.call("pets", "getpet", {}, False))["error"] and "not imported" in run(src.call("zz", "x", {}, False))["error"]
    assert "no operation" in run(src.call("pets", "zz", {}, False))["error"]
    src.create_operation("pets", dict(id="boom", path="/boom"))
    assert run(src.call("pets", "boom", {}, False))["error"] == "request failed: ConnectError"
    src.create_operation("pets", dict(id="txt", path="/text"))
    assert run(src.call("pets", "txt", {}, False))["body"] == "plain"
    run(src.call("pets", "getpet", {"id": "//evil.example/x"}, False))
    assert src.seen[-1][1].startswith("https://api.pets.test/")                                       # the host never changes, whatever the argument says


def test_test_call_masks_credentials_and_truncates(src):
    src.add("pets", "Pets", content=text(PETS3), token="T0K", auth={"type": "query", "name": "api_key"})
    src.create_operation("pets", dict(id="big", path="/big"))
    src.create_operation("pets", dict(id="txt", path="/text"))
    r = run(src.test("pets", "big", {}, False))
    assert r["ok"] and r["truncated"] and len(r["body"]) == 20_000 and r["request"]["query"] == {"api_key": "•••"} and "T0K" not in json.dumps(r["request"])
    assert run(src.test("pets", "txt", {}, False))["body"] == "plain"
    src.update_operation("pets", "listpets", {"enabled": False, "hidden": True})
    assert run(src.test("pets", "listpets", {}, False))["ok"]                                       # a test can run a switched-off endpoint
    assert "changes data" in run(src.test("pets", "addpet", {}, True))["error"]
    with pytest.raises(KeyError):
        run(src.test("pets", "ghost", {}, False))
    src.update("pets", {"auth": {"type": "header", "name": "X-K"}})
    r2 = run(src.test("pets", "getpet", {"id": 1}, False))
    assert r2["request"]["headers"]["X-K"] == "•••"


def test_refresh_diff_and_apply(src, state):
    src.add("pets", "Pets", content=text(PETS3), filename="pets.json")
    newer = json.loads(text(PETS3))
    del newer["paths"]["/pets"]["get"]
    newer["paths"]["/pets"]["post"]["requestBody"] = {"content": {"application/json": {"schema": {"properties": {"x": {"type": "string"}}}}}}
    newer["paths"]["/fresh"] = {"get": {"operationId": "fresh"}}
    src.used[("pets", "listpets")] = ["wf_x"]
    src.used[("pets", "addpet")] = ["wf_y", "wf_x"]
    r = src.refresh("pets", content=text(newer))
    assert r["diff"]["added"] == ["fresh"] and r["diff"]["removed"] == ["listpets"] and r["diff"]["changed"] == ["addpet"] and r["affected"] == ["wf_x", "wf_y"] and r["applied"] is False
    assert any(o["id"] == "listpets" for o in src.operations("pets"))
    assert src.refresh("pets", content=text(newer), dry_run=False)["applied"] is True and [o["id"] for o in src.operations("pets", q="fresh")] == ["fresh"]
    with pytest.raises(W.WorkflowError, match="upload or paste the new version"):
        src.refresh("pets")
    with pytest.raises(W.WorkflowError, match="could not read"):
        src.refresh("pets", content="junk")
    with pytest.raises(KeyError):
        src.refresh("ghost", content=text(newer))
    legacy = OpenApiSources(state.db, loader=lambda s, b: ({}, "https://l.test", {"o": dict(operation_id="o", method="get", path="/o", summary="", parameters=[], body=None)}))
    legacy.add("leg", "L", "https://l.test/spec.json")
    assert legacy.refresh("leg", dry_run=False)["applied"] is True and legacy.get("leg")["operations"][0]["id"] == "o"


def test_minimal_legacy_rows_still_work(src):
    src.db.execute("INSERT INTO openapi_sources(id,name,src,base,ops,token,allow_write,created) VALUES('old','Old','https://x','https://old.test',?,?,0,1)",
                   (json.dumps({"o": dict(operation_id="o", method="get", path="/o", summary="S", parameters=[], body=None)}), "Bearer legacy"))
    a = src.get("old")
    assert a["auth"]["type"] == "header" and a["origin"] == "url" and a["counts"]["total"] == 1 and a["operations"][0]["risk"] == "read" and a["updated"] == 1
    run(src.call("old", "o", {}, False))
    assert src.seen[-1][2]["authorization"] == "Bearer legacy"
    assert src.for_validation()["old"]["ops"]["o"]["method"] == "get"


# ---------------------------------------------------------------------------------------------------- the REST API
@pytest.fixture
def rest(client, state, tmp_path):
    client.put("/api/settings", json={"workspace_dir": str(tmp_path / "ws")})
    (tmp_path / "ws").mkdir(exist_ok=True)
    (tmp_path / "ws" / "pets.json").write_text(text(PETS3))
    with client:
        yield client


def test_rest_import_edit_refresh_delete_flow(rest, tmp_path):
    P = "/api/workflows/apis"
    assert rest.post(f"{P}/preview", json={}).status_code == 422
    pv = rest.post(f"{P}/preview", json={"content": text(PETS3), "filename": "pets.json"})
    assert pv.status_code == 200 and pv.json()["counts"]["total"] > 3
    assert rest.post(f"{P}/preview", json={"src": "../../etc/passwd"}).status_code == 422
    assert rest.post(f"{P}/preview", json={"src": "pets.json"}).json()["title"] == "Pet Store"
    r = rest.post(P, json={"id": "pets", "name": "Pets", "content": text(PETS3), "filename": "pets.json", "token": "s3cret", "auth": {"type": "bearer"}})
    assert r.status_code == 200 and "s3cret" not in r.text and rest.post(P, json={"id": "pets", "src": "pets.json"}).status_code == 422
    assert rest.post(P, json={"id": "zz", "src": "../../x.yaml"}).status_code == 422
    assert rest.get(f"{P}/pets").json()["title"] == "Pet Store" and rest.get(f"{P}/ghost").status_code == 404
    assert rest.put(f"{P}/pets", json={"allow_write": True}).json()["allow_write"] is True and rest.put(f"{P}/ghost", json={}).status_code == 404
    assert rest.post(f"{P}/pets/duplicate", json={"id": "pets2"}).json()["id"] == "pets2" and rest.post(f"{P}/ghost/duplicate", json={"id": "q"}).status_code == 404
    assert "Pet Store" in rest.get(f"{P}/pets/spec").text and rest.get(f"{P}/ghost/spec").status_code == 404
    ops = rest.get(f"{P}/pets/operations?q=pet&method=get&tag=pets").json()["operations"]
    assert [o["id"] for o in ops] == ["listpets"] and rest.get(f"{P}/ghost/operations").status_code == 404
    assert rest.post(f"{P}/pets/operations", json={"id": "ping", "path": "/ping"}).status_code == 200 and rest.post(f"{P}/pets/operations", json={"id": "ping", "path": "/ping"}).status_code == 422
    assert rest.get(f"{P}/pets/operations/ping").json()["origin"] == "custom" and rest.get(f"{P}/pets/operations/ghost").status_code == 404
    assert rest.put(f"{P}/pets/operations/ping", json={"label": "Ping!"}).json()["summary"] == "Ping!" and rest.put(f"{P}/pets/operations/ghost", json={}).status_code == 404
    assert rest.delete(f"{P}/pets/operations/ping").json()["removed"] is True and rest.delete(f"{P}/pets/operations/ping").status_code == 404
    assert rest.post(f"{P}/pets/refresh", json={"content": text(PETS3)}).json()["diff"] == {"added": [], "removed": [], "changed": []}
    assert rest.post(f"{P}/pets/refresh", json={"content": text(PETS3), "dry_run": False}).json()["applied"] is True and rest.post(f"{P}/pets/refresh", json={"src": "../../x"}).status_code == 422
    assert rest.post(f"{P}/pets/operations/listpets/test", json={"args": {}}).status_code in (200,) and rest.post(f"{P}/pets/operations/ghost/test", json={}).status_code == 404
    rest.post("/api/workflows", json={"id": "wf_pets", "name": "Uses pets", "nodes": [
        {"id": "start", "type": "input"}, {"id": "o", "type": "openapi", "config": {"source": "pets", "operation": "listpets", "args": {}}}], "edges": [{"source": "start", "target": "o"}]})
    d = rest.delete(f"{P}/pets")
    assert d.status_code == 409 and d.json()["used_by"] == ["wf_pets"] and "wf_pets" in d.json()["detail"]
    assert rest.get(f"{P}").json()["apis"][0]["used_by"] == ["wf_pets"]
    assert rest.delete(f"{P}/pets/operations/listpets").status_code == 409
    assert rest.delete(f"{P}/pets?force=1").status_code == 200 and rest.delete(f"{P}/pets").status_code == 404
    pr = rest.get("/api/workflows/wf_pets").json()["problems"]
    assert any("not imported" in p["message"] for p in pr)


def test_workflow_problem_when_endpoint_is_switched_off(src, state):
    src.add("pets", "Pets", content=text(PETS3))
    src.update_operation("pets", "listpets", {"enabled": False})
    doc = W.clean(dict(id="wf_x", name="x", nodes=[dict(id="s", type="input"), dict(id="o", type="openapi", config=dict(source="pets", operation="listpets"))], edges=[dict(source="s", target="o")]))
    pr = W.problems(doc, set(), src.for_validation(), set())
    assert any("switched off" in p["message"] for p in pr)
    store = W.WorkflowStore(state.db)
    store.save(doc)
    store.save(dict(id="wf_c", name="c", nodes=[dict(id="s", type="input"), dict(id="p", type="parallel", config=dict(steps=[dict(kind="openapi", source="pets", operation="x")]))],
                    edges=[dict(source="s", target="p")]))
    assert store.uses("pets") == ["wf_c", "wf_x"] and store.uses("pets", "listpets") == ["wf_x"] and store.uses("other") == []
