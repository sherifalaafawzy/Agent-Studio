"""Optional second homes for approved memories. Each is a RemoteIndex; the local table stays the source of truth.

API shapes were checked against primary sources on 2026-11-22:
* Vertex AI Memory Bank: the public discovery document https://aiplatform.googleapis.com/$discovery/rest?version=v1beta1 (revision 20261114):
  POST {parent}/memories (long-running), POST {parent}/memories:retrieve {scope, similaritySearchParams{searchQuery, topK}},
  DELETE {memory name}. A memory must have EXACTLY the scope given to retrieve it. `distance`: smaller = more similar.
* Amazon Bedrock AgentCore Memory: the boto3 1.43.126 service model for `bedrock-agentcore` (BatchCreateMemoryRecords,
  RetrieveMemoryRecords, DeleteMemoryRecord) and `bedrock-agentcore-control` (GetMemory).
* Mem0 (open source, Apache-2.0, self-hostable REST server): github.com/mem0ai/mem0 server/main.py: POST /memories {messages, user_id, infer,
  metadata}, POST /search {query, filters, top_k}, DELETE /memories/{id}; auth header X-API-Key.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

import httpx

from ..rag.stores.base import StoreError
from ..rag.stores.bedrock_kb import DENIED, MISSING, REGION_RE, aws_clients
from ..rag.stores.vertex_rag import google_token_provider
from .store import Kind, MemoryError_, MemoryFactory, RemoteIndex

NAMESPACE = uuid.UUID("5d3a1c1e-6b1a-4d3e-9a8f-0a5f6b1d9c11")
COLLECTION = "agent_studio_memory"


def _label(scope: str, scope_id: str) -> str:
    return f"agent-studio-{scope}-{scope_id}"


# ------------------------------------------------------------------------------------------------ a vector database you connected
class VectorMirror(RemoteIndex):
    """Stores each memory's vector in one of the knowledge-base vector stores (Qdrant, pgvector, Chroma, S3 Vectors...)."""
    kind = "vector"
    cloud = False                       # the store may be remote: the UI says where it is

    def __init__(self, store, where: str = ""):
        self.store, self.where = store, where

    async def health(self) -> dict:
        return await self.store.health()

    async def put(self, rec: dict, vector: list[float] | None) -> str:
        if not vector:
            raise MemoryError_("a vector store needs an embedding model: choose one in Memory settings")
        from ..rag.stores.base import Point
        try:
            await self.store.ensure(COLLECTION, len(vector))
            pid = str(uuid.uuid5(NAMESPACE, rec["id"]))
            await self.store.upsert(COLLECTION, [Point(pid, vector, {"doc_id": rec["id"], "scope": rec["scope"], "scope_id": rec["scope_id"],
                                                                      "type": rec["type"], "text": rec["text"][:500]})])
        except StoreError as e:
            raise MemoryError_(str(e)) from e
        return pid

    async def search(self, query: str, vector: list[float] | None, scope: str, scope_id: str, k: int) -> list[tuple[str, float]]:
        if not vector:
            return []
        try:
            hits = await self.store.search(COLLECTION, vector, max(k * 4, 20))        # the store has no scope filter: over-fetch, then filter
        except StoreError as e:
            raise MemoryError_(str(e)) from e
        return [(h.payload.get("doc_id"), h.score) for h in hits if h.payload.get("scope") == scope and h.payload.get("scope_id") == scope_id][:k]

    async def delete(self, rec: dict) -> None:
        try:
            await self.store.delete_doc(COLLECTION, rec["id"])
        except StoreError as e:
            raise MemoryError_(str(e)) from e


# ------------------------------------------------------------------------------------------------ Vertex AI Memory Bank
class VertexMemoryBank(RemoteIndex):
    kind = "vertex"
    cloud = True

    def __init__(self, bank: str, token: callable, transport: httpx.AsyncBaseTransport | None = None, poll: float = 1.0, timeout: float = 30):
        m = re.match(r"^projects/[^/]+/locations/([a-z0-9-]+)/(reasoningEngines|memoryBanks)/[^/]+$", bank or "")
        if not m:
            raise MemoryError_("the memory bank must look like projects/PROJECT/locations/LOCATION/reasoningEngines/ID (or .../memoryBanks/ID)")
        self.bank, self._token, self._transport, self._poll, self._timeout = bank, token, transport, poll, timeout
        self._base = f"https://{m.group(1)}-aiplatform.googleapis.com/v1beta1"

    async def _req(self, method: str, path: str, json: dict | None = None):
        try:
            tok = await asyncio.to_thread(self._token)
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, headers={"Authorization": f"Bearer {tok}"}) as c:
                r = await c.request(method, f"{self._base}/{path}", json=json)
        except StoreError as e:
            raise MemoryError_(str(e)) from e
        except httpx.HTTPError as e:
            raise MemoryError_(f"cannot reach Vertex AI: {type(e).__name__}") from e
        except Exception as e:  # noqa: BLE001 - google-auth errors (no credentials, bad key) are explained, not shown as traces
            raise MemoryError_(f"Google credentials are not usable: {type(e).__name__}: {str(e)[:200]}") from e
        if r.status_code in (401, 403):
            raise MemoryError_("Google rejected the credentials or they lack the Vertex AI User role")
        if r.status_code >= 400:
            raise MemoryError_(f"Vertex AI error {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def _finish(self, op: dict) -> dict:
        """Wait for a long-running operation (a few quick polls), then return its response."""
        for _ in range(10):
            if op.get("done"):
                break
            if "name" not in op:
                raise MemoryError_("Vertex AI sent a reply this version does not understand")
            await asyncio.sleep(self._poll)
            op = await self._req("GET", op["name"])
        if not op.get("done"):
            raise MemoryError_("Vertex AI is still processing: try again in a moment")
        if op.get("error"):
            raise MemoryError_(f"Vertex AI: {str(op['error'].get('message', op['error']))[:300]}")
        return op.get("response") or {}

    @staticmethod
    def _scope(scope: str, scope_id: str) -> dict:
        return {"agent_studio_scope": scope, "agent_studio_id": scope_id}

    async def health(self) -> dict:
        try:
            await self._req("GET", f"{self.bank}/memories?pageSize=1")
        except MemoryError_ as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"Vertex AI Memory Bank reachable ({self.bank.split('/')[-1]})"}

    async def put(self, rec: dict, vector: list[float] | None) -> str:
        op = await self._req("POST", f"{self.bank}/memories", {"fact": rec["text"], "displayName": rec["id"], "scope": self._scope(rec["scope"], rec["scope_id"])})
        return (await self._finish(op)).get("name", "")

    async def search(self, query: str, vector: list[float] | None, scope: str, scope_id: str, k: int) -> list[tuple[str, float]]:
        got = await self._req("POST", f"{self.bank}/memories:retrieve", {"scope": self._scope(scope, scope_id), "similaritySearchParams": {"searchQuery": query, "topK": k}})
        out = []
        for item in got.get("retrievedMemories") or []:
            mem = item.get("memory") or {}
            out.append((mem.get("displayName") or mem.get("name", ""), max(0.0, 1.0 - float(item.get("distance", 1.0)))))   # smaller distance = closer
        return out

    async def delete(self, rec: dict) -> None:
        if rec.get("remote_id"):
            await self._finish(await self._req("DELETE", rec["remote_id"]))


# ------------------------------------------------------------------------------------------------ Amazon Bedrock AgentCore Memory
class AgentCoreMemory(RemoteIndex):
    kind = "agentcore"
    cloud = True

    def __init__(self, region: str, memory_id: str, clients):
        if not REGION_RE.match(region or ""):
            raise MemoryError_("the AWS region must look like us-east-1")
        if not re.match(r"^[A-Za-z][A-Za-z0-9_-]{0,99}-[A-Za-z0-9]{10}$", memory_id or ""):
            raise MemoryError_("the AgentCore memory id looks wrong (it ends in 10 letters or digits, like my_memory-AbC123XyZ9)")
        self.region, self.memory_id, self._clients = region, memory_id, clients

    def _call_sync(self, service: str, op: str, **kw):
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ParamValidationError
        try:
            return getattr(self._clients(service), op)(**kw)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            msg = str(e.response.get("Error", {}).get("Message", ""))[:300]
            if code in MISSING | {"ResourceNotFoundException"}:
                raise MemoryError_(f"not found in AWS ({code}): {msg}") from e
            if code in DENIED:
                raise MemoryError_(f"AWS rejected the credentials or they lack permission ({code}): {msg}") from e
            raise MemoryError_(f"AWS error {code}: {msg}") from e
        except NoCredentialsError as e:
            raise MemoryError_("no AWS credentials found: set keys, a profile, or sign in with the AWS CLI") from e
        except ParamValidationError as e:
            raise MemoryError_(f"AgentCore rejected the request: {str(e)[:200]}") from e
        except BotoCoreError as e:
            raise MemoryError_(f"cannot reach AgentCore in {self.region}: {type(e).__name__}") from e

    async def _call(self, service: str, op: str, **kw):
        return await asyncio.to_thread(lambda: self._call_sync(service, op, **kw))

    @staticmethod
    def namespace(scope: str, scope_id: str) -> str:
        return f"/agent-studio/{scope}/{scope_id}"

    async def health(self) -> dict:
        try:
            got = await self._call("bedrock-agentcore-control", "get_memory", memoryId=self.memory_id)
        except MemoryError_ as e:
            return {"ok": False, "detail": str(e)}
        status = (got.get("memory") or {}).get("status", "?")
        return {"ok": status == "ACTIVE", "detail": f"AgentCore memory {self.memory_id} is {status}"}

    async def put(self, rec: dict, vector: list[float] | None) -> str:
        got = await self._call("bedrock-agentcore", "batch_create_memory_records", memoryId=self.memory_id, records=[{
            "requestIdentifier": rec["id"], "namespaces": [self.namespace(rec["scope"], rec["scope_id"])], "content": {"text": rec["text"]},
            "timestamp": datetime.fromtimestamp(rec["updated"], tz=timezone.utc), "metadata": {"memory_id": {"stringValue": rec["id"]}}}])
        if got.get("failedRecords"):
            f = got["failedRecords"][0]
            raise MemoryError_(f"AgentCore refused the memory: {f.get('errorMessage') or f.get('errorCode') or 'unknown reason'}")
        return (got.get("successfulRecords") or [{}])[0].get("memoryRecordId", "")

    async def search(self, query: str, vector: list[float] | None, scope: str, scope_id: str, k: int) -> list[tuple[str, float]]:
        got = await self._call("bedrock-agentcore", "retrieve_memory_records", memoryId=self.memory_id, namespace=self.namespace(scope, scope_id),
                               searchCriteria={"searchQuery": query, "topK": k}, maxResults=k)
        out = []
        for s in got.get("memoryRecordSummaries") or []:
            ref = ((s.get("metadata") or {}).get("memory_id") or {}).get("stringValue") or s["memoryRecordId"]
            out.append((ref, float(s.get("score") or 0.0)))
        return out

    async def delete(self, rec: dict) -> None:
        if rec.get("remote_id"):
            await self._call("bedrock-agentcore", "delete_memory_record", memoryId=self.memory_id, memoryRecordId=rec["remote_id"])


# ------------------------------------------------------------------------------------------------ Mem0 (open source, self-hosted)
class Mem0(RemoteIndex):
    kind = "mem0"
    cloud = False                       # your own server; the UI shows its address and whether it is on this PC

    def __init__(self, url: str, api_key: str = "", transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30):
        if not re.match(r"^https?://[^/\s]+", url or ""):
            raise MemoryError_("the Mem0 server address must start with http:// or https://")
        self.url, self._headers = url.rstrip("/"), ({"X-API-Key": api_key} if api_key else {})
        self._transport, self._timeout = transport, timeout

    async def _req(self, method: str, path: str, json: dict | None = None, params: dict | None = None):
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, headers=self._headers) as c:
                r = await c.request(method, self.url + path, json=json, params=params)
        except httpx.HTTPError as e:
            raise MemoryError_(f"cannot reach the Mem0 server at {self.url}: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise MemoryError_("the Mem0 server rejected the API key")
        if r.status_code >= 400:
            raise MemoryError_(f"Mem0 error {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def health(self) -> dict:
        try:
            await self._req("GET", "/memories", params={"user_id": "agent-studio-health", "top_k": 1})
        except MemoryError_ as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"Mem0 server reachable at {self.url}"}

    async def put(self, rec: dict, vector: list[float] | None) -> str:
        got = await self._req("POST", "/memories", {"messages": [{"role": "user", "content": rec["text"]}], "user_id": _label(rec["scope"], rec["scope_id"]),
                                                    "infer": False, "metadata": {"memory_id": rec["id"], "type": rec["type"]}})
        results = got.get("results") or []
        if not results:
            raise MemoryError_("the Mem0 server did not store the memory")
        return str(results[0].get("id", ""))

    async def search(self, query: str, vector: list[float] | None, scope: str, scope_id: str, k: int) -> list[tuple[str, float]]:
        got = await self._req("POST", "/search", {"query": query, "filters": {"user_id": _label(scope, scope_id)}, "top_k": k})
        return [((r.get("metadata") or {}).get("memory_id") or str(r.get("id")), float(r.get("score") or 0.0)) for r in got.get("results") or []]

    async def delete(self, rec: dict) -> None:
        if rec.get("remote_id"):
            await self._req("DELETE", f"/memories/{rec['remote_id']}")


# ------------------------------------------------------------------------------------------------ the factory
def _vector(config: dict, secrets: dict, ctx: dict):
    rag = ctx.get("rag")
    sid = config.get("store_id", "")
    if rag is None:
        raise MemoryError_("knowledge-base stores are not available")
    try:
        store = rag._store(sid)
    except Exception as e:  # noqa: BLE001 - unknown store / unusable settings: say which
        raise MemoryError_(f"vector store '{sid}': {e}") from e
    return VectorMirror(store, sid)


def _vertex(config: dict, secrets: dict, ctx: dict):
    return VertexMemoryBank(config.get("bank", ""), google_token_provider(secrets.get("service_account_json", "")), ctx.get("transport"))


def _agentcore(config: dict, secrets: dict, ctx: dict):
    clients = aws_clients(config.get("region", ""), config.get("profile", ""), secrets.get("access_key_id", ""), secrets.get("secret_access_key", ""),
                          secrets.get("session_token", ""))
    return AgentCoreMemory(config.get("region", ""), config.get("memory_id", ""), clients)


def _mem0(config: dict, secrets: dict, ctx: dict):
    return Mem0(config.get("url", ""), secrets.get("api_key", ""), ctx.get("transport"))


F = lambda name, label, secret=False, required=True, help="": (name, label, secret, required, help)       # noqa: E731

FACTORY = MemoryFactory()
FACTORY.register(Kind("local", "On this PC", "Memories live in Agent Studio's own database. Works offline. Nothing leaves this PC.", False))
FACTORY.register(Kind("vector", "A vector database you connected", "Also keeps each memory's vector in a knowledge-base store (Qdrant, pgvector, Chroma, S3 Vectors...), so large "
                      "memories stay fast to search. Needs an embedding model.", False, (F("store_id", "Knowledge-base store", help="An id from Knowledge > Stores."),), _vector))
FACTORY.register(Kind("vertex", "Google Vertex AI Memory Bank", "Google keeps a copy and searches it for you. Memory text is sent to Google Cloud.", True,
                      (F("bank", "Memory bank resource name", help="projects/PROJECT/locations/LOCATION/reasoningEngines/ID (or .../memoryBanks/ID)."),
                       F("service_account_json", "Service account JSON", True, False, "Leave empty to use this PC's Application Default Credentials. Needs the Vertex AI User role.")), _vertex,
                      "Uses the v1beta1 API: it may change.", "aiplatform.googleapis.com discovery document, revision 20261114"))
FACTORY.register(Kind("agentcore", "Amazon Bedrock AgentCore Memory", "AWS keeps a copy and searches it for you. Memory text is sent to AWS.", True,
                      (F("region", "AWS region", help="For example us-east-1."), F("memory_id", "Memory id", help="Create the memory in the AWS console first."),
                       F("profile", "AWS profile", False, False), F("access_key_id", "Access key id", True, False, "Leave all credentials empty to use the default AWS credential chain."),
                       F("secret_access_key", "Secret access key", True, False), F("session_token", "Session token", True, False)), _agentcore,
                      "Records are stored under the namespace /agent-studio/<scope>/<id>.", "boto3 1.43.126 service models bedrock-agentcore and bedrock-agentcore-control"))
FACTORY.register(Kind("mem0", "Mem0 (open source)", "Your own Mem0 server (Apache-2.0). Memories are stored as written; Mem0 does the searching.", False,
                      (F("url", "Server address", help="For example http://127.0.0.1:8888."), F("api_key", "API key", True, False, "Sent as X-API-Key, if your server needs one.")), _mem0,
                      "Self-hosted: the text goes to the server you name.", "github.com/mem0ai/mem0 server/main.py"))
