"""In-memory fakes of PostgreSQL+pgvector, Chroma, Pinecone, Vertex AI RAG Engine and Amazon Bedrock Knowledge Bases.

Each speaks just the calls its adapter makes. No network, no cloud, no containers.
"""
from __future__ import annotations

import json
import re

import httpx

from tests.rag_fakes import DIM, FakeQdrant


def _overlap(query: str, text: str) -> float:
    q, t = set(re.findall(r"[a-z0-9]+", query.lower())), set(re.findall(r"[a-z0-9]+", text.lower()))
    return len(q & t) / (len(q) or 1)


# ---------------------------------------------------------------------------------------------------------- PostgreSQL
class PgError(Exception):
    pass


class PgOperationalError(PgError):
    pass


class _Cursor:
    def __init__(self, rows=None, rowcount=0, owner=None):
        self._rows, self.rowcount, self.owner = rows or [], rowcount, owner

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def executemany(self, sql, rows):
        self.owner.run_many(sql, rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Txn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakePg:
    """An in-memory PostgreSQL + pgvector that understands exactly the SQL PgVectorStore sends. It is also the `driver`
    module object (connect / Error / OperationalError). Every statement is recorded as (sql, params)."""
    Error, OperationalError = PgError, PgOperationalError

    def __init__(self, password: str | None = None, extension_available: bool = True, version: str = "PostgreSQL 17.6 on x86_64-pc-linux-gnu"):
        self.tables: dict[str, dict] = {}
        self.statements: list[tuple[str, tuple]] = []
        self.connects: list[dict] = []
        self.password, self.extension_available, self.version = password, extension_available, version
        self.ext: str | None = None
        self.down: str | None = None
        self.fail_on: tuple[str, str] | None = None          # (sql fragment, error message)

    def connect(self, **kw):
        self.connects.append(kw)
        if self.down:
            raise PgOperationalError(self.down)
        if self.password is not None and kw.get("password") != self.password:
            raise PgOperationalError('connection failed: FATAL:  password authentication failed for user "agentstudio"')
        return _Conn(self)

    def run(self, sql: str, params: tuple = ()) -> _Cursor:
        self.statements.append((sql, tuple(params)))
        if self.fail_on and self.fail_on[0] in sql:
            raise PgError(self.fail_on[1] + "\nDETAIL: more")
        s = " ".join(sql.split())
        tbl = re.search(r'("[^"]+"\."[^"]+")', s)
        t = tbl.group(1) if tbl else None
        if s == "SELECT version()":
            return _Cursor([(self.version,)])
        if s.startswith("SELECT extversion"):
            return _Cursor([(self.ext,)] if self.ext else [])
        if s.startswith("CREATE EXTENSION"):
            if not self.extension_available:
                raise PgError('extension "vector" is not available')
            self.ext = "0.8.6"
            return _Cursor()
        if s.startswith("CREATE SCHEMA"):
            return _Cursor()
        if s.startswith("SELECT to_regclass"):
            return _Cursor([(params[0] if params[0] in self.tables else None,)])
        if s.startswith("SELECT atttypmod"):
            return _Cursor([(self.tables[params[0]]["dim"],)] if params[0] in self.tables else [])
        if s.startswith("CREATE TABLE"):
            self.tables[t] = {"dim": int(re.search(r"vector\((\d+)\)", s).group(1)), "rows": {}, "indexes": []}
            return _Cursor()
        if s.startswith("CREATE INDEX"):
            self.tables[t]["indexes"].append(s)
            return _Cursor()
        if s.startswith("DROP TABLE"):
            self.tables.pop(t, None)
            return _Cursor()
        if s.startswith("SELECT COUNT"):
            return _Cursor([(len(self.tables[t]["rows"]),)])
        if s.startswith("DELETE FROM"):
            rows = self.tables[t]["rows"]
            gone = [i for i, r in rows.items() if r[0] == params[0]]
            for i in gone:
                del rows[i]
            return _Cursor(rowcount=len(gone))
        if s.startswith("SELECT id::text"):
            q = json.loads(params[0])
            scored = sorted(((FakeQdrant._cos(q, r[1]), i, r[2]) for i, r in self.tables[t]["rows"].items()), key=lambda x: -x[0])
            return _Cursor([(i, payload, sc) for sc, i, payload in scored[:params[2]]])
        raise AssertionError(f"FakePg got SQL it does not know: {s}")

    def run_many(self, sql: str, rows) -> None:
        self.statements.append((sql, ("<many>", len(rows))))
        table = self.tables[re.search(r'("[^"]+"\."[^"]+")', sql).group(1)]
        for pid, doc_id, vec, payload in rows:
            v = json.loads(vec)
            if len(v) != table["dim"]:
                raise PgError(f"expected {table['dim']} dimensions, not {len(v)}")
            table["rows"][pid] = (doc_id, v, json.loads(payload))


class _Conn:
    def __init__(self, pg: FakePg):
        self.pg = pg

    def execute(self, sql, params=()):
        return self.pg.run(sql, params)

    def cursor(self):
        return _Cursor(owner=self.pg)

    def transaction(self):
        return _Txn()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------------------------------------------- Chroma
class FakeChroma:
    """An in-memory Chroma server (REST API v2). Use .transport with ChromaStore."""

    def __init__(self, token: str | None = None, tenant: str = "default_tenant", database: str = "default_database"):
        self.cols: dict[str, dict] = {}
        self.token, self.requests = token, []
        self.base = f"/api/v2/tenants/{tenant}/databases/{database}/collections"
        self.transport = httpx.MockTransport(self.handle)

    @staticmethod
    def _err(code, msg):
        return httpx.Response(code, json={"error": "Err", "message": msg})

    @staticmethod
    def _meta(c):
        return {"id": c["id"], "name": c["name"], "dimension": c["dimension"], "configuration_json": {"hnsw": {"space": c["space"]}}}

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        if self.token and req.headers.get("authorization") != f"Bearer {self.token}":
            return self._err(401, "Unauthorized")
        body = json.loads(req.content) if req.content else {}
        p = req.url.path
        if p == "/api/v2/heartbeat":
            return httpx.Response(200, json={"nanosecond heartbeat": 1})
        if p == "/api/v2/version":
            return httpx.Response(200, json="1.5.9")
        if not p.startswith(self.base):
            return self._err(404, "no such tenant or database")
        tail = p[len(self.base):].strip("/")
        if tail == "":
            if req.method == "GET":
                return httpx.Response(200, json=[self._meta(c) for c in self.cols.values()])
            name = body["name"]
            if name not in self.cols:
                self.cols[name] = {"id": f"id-{name}", "name": name, "dimension": None, "space": body["configuration"]["hnsw"]["space"], "rows": {}}
            return httpx.Response(200, json=self._meta(self.cols[name]))
        parts = tail.split("/")
        if len(parts) == 1:
            col = self.cols.get(parts[0])
            if col is None:
                return self._err(404, f"Collection [{parts[0]}] does not exist")
            if req.method == "DELETE":
                del self.cols[parts[0]]
                return httpx.Response(200, json=None)
            return httpx.Response(200, json=self._meta(col))
        col = next((c for c in self.cols.values() if c["id"] == parts[0]), None)
        if col is None:
            return self._err(404, "Collection does not exist")
        op = parts[1]
        if op == "count":
            return httpx.Response(200, json=len(col["rows"]))
        if op == "upsert":
            for i, vec, doc, meta in zip(body["ids"], body["embeddings"], body["documents"], body["metadatas"]):
                if col["dimension"] not in (None, len(vec)):
                    return self._err(400, f"Collection expecting embedding with dimension of {col['dimension']}, got {len(vec)}")
                col["dimension"] = len(vec)
                col["rows"][i] = (vec, doc, meta)
            return httpx.Response(200, json={})
        if op == "query":
            q = body["query_embeddings"][0]
            scored = sorted(((1.0 - FakeQdrant._cos(q, r[0]), i, r) for i, r in col["rows"].items()), key=lambda x: x[0])[:body["n_results"]]
            return httpx.Response(200, json={"ids": [[i for _, i, _ in scored]], "documents": [[r[1] for _, _, r in scored]],
                                             "metadatas": [[r[2] for _, _, r in scored]], "distances": [[d for d, _, _ in scored]]})
        if op == "get":
            want = body["where"]["doc_id"]
            return httpx.Response(200, json={"ids": [i for i, r in col["rows"].items() if r[2].get("doc_id") == want]})
        if op == "delete":
            for i in body["ids"]:
                col["rows"].pop(i, None)
            return httpx.Response(200, json={})
        return httpx.Response(500, text="unhandled route in FakeChroma")


# ------------------------------------------------------------------------------------------------------------ Pinecone
class FakePinecone:
    """An in-memory Pinecone: the control plane (index lookup) and one index's data plane. Use .transport with PineconeStore."""
    HOST = "idx-abc123.svc.us-east-1.pinecone.io"

    def __init__(self, api_key: str = "pc-key", dim: int = DIM, metric: str = "cosine", page: int = 2):
        self.api_key, self.dim, self.metric, self.page = api_key, dim, metric, page
        self.ns: dict[str, dict[str, dict]] = {}
        self.requests = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.host, req.url.path))
        if req.headers.get("api-key") != self.api_key:
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
        body = json.loads(req.content) if req.content else {}
        p = req.url.path
        if req.url.host == "api.pinecone.io":
            if p == "/indexes/my-index":
                return httpx.Response(200, json={"name": "my-index", "host": self.HOST, "dimension": self.dim, "metric": self.metric})
            return httpx.Response(404, json={"error": {"message": "index not found"}})
        if p == "/describe_index_stats":
            return httpx.Response(200, json={"dimension": self.dim, "totalVectorCount": sum(len(v) for v in self.ns.values()),
                                             "namespaces": {n: {"vectorCount": len(v)} for n, v in self.ns.items()}})
        ns = body.get("namespace") or req.url.params.get("namespace")
        if p == "/vectors/upsert":
            for v in body["vectors"]:
                if len(v["values"]) != self.dim:
                    return httpx.Response(400, json={"message": f"Vector dimension {len(v['values'])} does not match the dimension of the index {self.dim}"})
                self.ns.setdefault(ns, {})[v["id"]] = v
            return httpx.Response(200, json={"upsertedCount": len(body["vectors"])})
        if p == "/query":
            rows = self.ns.get(ns, {}).values()
            scored = sorted(((FakeQdrant._cos(body["vector"], r["values"]), r) for r in rows), key=lambda x: -x[0])[:body["topK"]]
            return httpx.Response(200, json={"matches": [{"id": r["id"], "score": s, "metadata": r["metadata"]} for s, r in scored]})
        if p == "/vectors/list":
            if ns not in self.ns:
                return httpx.Response(404, json={"message": "namespace not found"})
            ids = sorted(i for i in self.ns[ns] if i.startswith(req.url.params["prefix"]))
            start = int(req.url.params.get("paginationToken", 0))
            out = {"vectors": [{"id": i} for i in ids[start:start + self.page]]}
            if start + self.page < len(ids):
                out["pagination"] = {"next": str(start + self.page)}
            return httpx.Response(200, json=out)
        if p == "/vectors/delete":
            if body.get("deleteAll"):
                if ns not in self.ns:
                    return httpx.Response(404, json={"message": "namespace not found"})
                del self.ns[ns]
            else:
                for i in body["ids"]:
                    self.ns.get(ns, {}).pop(i, None)
            return httpx.Response(200, json={})
        return httpx.Response(500, text="unhandled route in FakePinecone")


# --------------------------------------------------------------------------------------------------------- Vertex RAG
class FakeVertexRag:
    """An in-memory Vertex AI RAG Engine (corpora, files, long-running create, retrieveContexts). Use .transport."""
    PARENT = "projects/demo-proj/locations/us-central1"

    def __init__(self, token: str = "tok", pending_polls: int = 1, create_error: str | None = None, page: int = 1):
        self.token, self.pending, self.create_error, self.page = token, pending_polls, create_error, page
        self.corpora: dict[str, dict] = {}                 # full name -> {displayName, files: {file name: {displayName, text}}}
        self.ops: dict[str, int] = {}
        self.requests: list[tuple[str, str]] = []
        self.next_id = 1000
        self.nameless = False
        self.creating = ""
        self.transport = httpx.MockTransport(self.handle)

    def add_corpus(self, cid: str, display: str, files: dict | None = None) -> str:
        name = f"{self.PARENT}/ragCorpora/{cid}"
        self.corpora[name] = {"displayName": display, "files": files or {}}
        return name

    @staticmethod
    def _json(code, data):
        return httpx.Response(code, json=data)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append((req.method, req.url.path))
        if req.headers.get("authorization") != f"Bearer {self.token}":
            return self._json(401, {"error": {"message": "invalid token"}})
        p = req.url.path
        if p == f"/v1/{self.PARENT}/ragCorpora" and req.method == "GET":
            names = sorted(self.corpora)
            start = int(req.url.params.get("pageToken", 0))
            out = {"ragCorpora": [{"name": n, "displayName": self.corpora[n]["displayName"]} for n in names[start:start + self.page]]}
            if start + self.page < len(names):
                out["nextPageToken"] = str(start + self.page)
            return self._json(200, out)
        if p == f"/v1/{self.PARENT}/ragCorpora" and req.method == "POST":
            self.next_id += 1
            op = f"{self.PARENT}/operations/{self.next_id}"
            self.ops[op] = self.pending
            self.creating = json.loads(req.content)["displayName"]
            return self._json(200, {"name": op, "done": False})
        if "/operations/" in p:
            op = p[len("/v1/"):]
            self.ops[op] -= 1
            if self.ops[op] > 0:
                return self._json(200, {"name": op, "done": False})
            if self.create_error:
                return self._json(200, {"name": op, "done": True, "error": {"message": self.create_error}})
            name = "" if self.nameless else self.add_corpus(str(self.next_id), self.creating)
            return self._json(200, {"name": op, "done": True, "response": {"name": name} if name else {}})
        if p == f"/v1/{self.PARENT}:retrieveContexts":
            body = json.loads(req.content)
            cname = body["vertexRagStore"]["ragResources"][0]["ragCorpus"]
            if cname not in self.corpora:
                return self._json(404, {"error": {"message": "corpus not found"}})
            scored = sorted(((_overlap(body["query"]["text"], f["text"]), f) for f in self.corpora[cname]["files"].values()), key=lambda x: -x[0])
            ctx = [{"sourceUri": f"gs://demo/{f['displayName']}.txt", "sourceDisplayName": f["displayName"], "text": f["text"], "score": s}
                   for s, f in scored[:body["query"]["ragRetrievalConfig"]["topK"]]]
            return self._json(200, {"contexts": {"contexts": ctx}})
        m = re.fullmatch(r"/upload/v1/(.+)/ragFiles:upload", p)
        if m:
            corpus = m.group(1)
            if corpus not in self.corpora:
                return self._json(404, {"error": {"message": "corpus not found"}})
            if req.headers.get("x-goog-upload-protocol") != "multipart":
                return self._json(400, {"error": {"message": "bad upload protocol"}})
            raw = req.content.decode("utf-8", "replace")
            display = re.search(r'"displayName": "([^"]+)"', raw).group(1)
            text = raw.split("Content-Type: text/plain\r\n\r\n", 1)[1].rsplit("\r\n--", 1)[0]
            if "REJECT" in text:
                return self._json(200, {"error": {"message": "unsupported file"}})
            fname = f"{corpus}/ragFiles/{len(self.corpora[corpus]['files']) + 1}{display}"
            self.corpora[corpus]["files"][fname] = {"displayName": display, "text": text}
            return self._json(200, {"ragFile": {"name": fname}})
        m = re.fullmatch(r"/v1/(.+)/ragFiles", p)
        if m:
            if m.group(1) not in self.corpora:
                return self._json(404, {"error": {"message": "corpus not found"}})
            files = sorted(self.corpora[m.group(1)]["files"].items())
            start = int(req.url.params.get("pageToken", 0))
            out = {"ragFiles": [{"name": n, "displayName": f["displayName"]} for n, f in files[start:start + self.page]]}
            if start + self.page < len(files):
                out["nextPageToken"] = str(start + self.page)
            return self._json(200, out)
        m = re.fullmatch(r"/v1/(.+)/ragFiles/[^/]+", p)
        if m and req.method == "DELETE":
            self.corpora[m.group(1)]["files"].pop(p[len("/v1/"):], None)
            return self._json(200, {"name": "op"})
        if p.startswith("/v1/") and "/ragCorpora/" in p:
            name = p[len("/v1/"):]
            if name not in self.corpora:
                return self._json(404, {"error": {"message": "corpus not found"}})
            if req.method == "DELETE":
                assert req.url.params.get("force") == "true"
                del self.corpora[name]
                return self._json(200, {"name": "op"})
            return self._json(200, {"name": name, "displayName": self.corpora[name]["displayName"]})
        return httpx.Response(500, text="unhandled route in FakeVertexRag")


# ---------------------------------------------------------------------------------------------- Bedrock Knowledge Bases
def aws_error(code: str, message: str = "boom", op: str = "Op"):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": message}}, op)


class FakeAws:
    """In-memory bedrock-agent, bedrock-agent-runtime and s3. `clients` is what BedrockKbStore takes as its client factory.
    Objects only become searchable when an ingestion job runs, like the real service."""

    def __init__(self, kb_id: str = "KBKBKBKB01", ds_id: str = "DSDSDSDS01", bucket: str = "my-bucket", status: str = "ACTIVE"):
        self.kb_id, self.ds_id, self.bucket, self.status = kb_id, ds_id, bucket, status
        self.s3: dict[tuple[str, str], bytes] = {}
        self.indexed: dict[tuple[str, str], bytes] = {}
        self.native = [("s3://other-bucket/handbook.pdf", "The staff handbook covers parental leave and holidays.")]
        self.jobs = 0
        self.fail: dict[str, Exception] = {}
        self.calls: list[tuple[str, str]] = []

    def clients(self, service: str):
        return _AwsClient(self, service)


class _AwsClient:
    def __init__(self, aws: FakeAws, service: str):
        self.aws, self.service = aws, service

    def __getattr__(self, op):
        def call(**kw):
            self.aws.calls.append((self.service, op))
            if op in self.aws.fail:
                raise self.aws.fail[op]
            return getattr(self, "_" + op)(**kw)
        return call

    def _list_knowledge_bases(self, maxResults):
        return {"knowledgeBaseSummaries": [{"knowledgeBaseId": self.aws.kb_id}]}

    def _get_knowledge_base(self, knowledgeBaseId):
        if knowledgeBaseId != self.aws.kb_id:
            raise aws_error("ResourceNotFoundException", "no such knowledge base")
        return {"knowledgeBase": {"knowledgeBaseId": knowledgeBaseId, "status": self.aws.status}}

    def _get_data_source(self, knowledgeBaseId, dataSourceId):
        if dataSourceId != self.aws.ds_id:
            raise aws_error("ResourceNotFoundException", "no such data source")
        return {"dataSource": {"dataSourceConfiguration": {"s3Configuration": {"bucketArn": f"arn:aws:s3:::{self.aws.bucket}"}}}}

    def _start_ingestion_job(self, knowledgeBaseId, dataSourceId):
        self.aws.jobs += 1
        self.aws.indexed = dict(self.aws.s3)
        return {"ingestionJob": {"status": "STARTING"}}

    def _put_object(self, Bucket, Key, Body, ContentType):
        assert ContentType.startswith("text/plain")
        self.aws.s3[(Bucket, Key)] = Body

    def _delete_object(self, Bucket, Key):
        self.aws.s3.pop((Bucket, Key), None)

    def _retrieve(self, knowledgeBaseId, retrievalQuery, retrievalConfiguration):
        if knowledgeBaseId != self.aws.kb_id:
            raise aws_error("ResourceNotFoundException", "no such knowledge base")
        docs = [(f"s3://{b}/{k}", v.decode()) for (b, k), v in self.aws.indexed.items()] + self.aws.native
        scored = sorted(((_overlap(retrievalQuery["text"], t), u, t) for u, t in docs), key=lambda x: -x[0])
        n = retrievalConfiguration["vectorSearchConfiguration"]["numberOfResults"]
        results = [{"content": {"type": "TEXT", "text": t}, "location": {"type": "S3", "s3Location": {"uri": u}}, "score": s}
                   for s, u, t in scored[:n]]
        return {"retrievalResults": results + [{"content": {}, "location": {"type": "WEB", "webLocation": {"url": "https://x.test"}}}]}
