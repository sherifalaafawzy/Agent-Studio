"""Google Vertex AI RAG Engine over REST (managed RAG: Google chunks, embeds and stores the files).

A knowledge base here is one RAG corpus. Leave "existing corpus" empty and Agent Studio creates a corpus for it (and deletes
it with the knowledge base); give a corpus id and that corpus is attached, never deleted. Documents are uploaded as plain
text files (ragFiles:upload, limit 25 MB) named after our document id, so replacing/removing a document is exact.

Auth: a short-lived OAuth token from `token_provider` (service-account JSON or Application Default Credentials).
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

import httpx

from .base import Hit, ManagedRef, ManagedStore, StoreError

LOCATION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,99}$")
CORPUS_RE = re.compile(r"^(projects/[^/]+/locations/[^/]+/ragCorpora/)?[A-Za-z0-9_-]{1,64}$")
POLL_TRIES = 90
POLL_SECONDS = 2.0


class VertexRagStore(ManagedStore):
    kind = "vertex_rag"

    def __init__(self, project: str, location: str, token_provider: Callable[[], str],
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 120, sleep=asyncio.sleep):
        if not PROJECT_RE.match(project or ""):
            raise StoreError("the Google Cloud project id looks wrong (for example my-project-123)")
        if not LOCATION_RE.match(location or ""):
            raise StoreError("the location must look like us-central1 or europe-west4")
        self._parent = f"projects/{project}/locations/{location}"
        self._api = f"https://{location}-aiplatform.googleapis.com"
        self._token, self._transport, self._timeout, self._sleep = token_provider, transport, timeout, sleep

    @property
    def can_ingest(self) -> bool:
        return True

    async def _req(self, method: str, url: str, body=None, files=None, ok_404: bool = False, params=None, upload: bool = False):
        try:
            token = await asyncio.to_thread(self._token)
        except Exception as e:  # noqa: BLE001 - google-auth raises many types; the user needs one clear message
            raise StoreError(f"could not sign in to Google Cloud ({type(e).__name__}): check the service account or run "
                             "'gcloud auth application-default login'") from e
        headers = {"Authorization": f"Bearer {token}", **({"X-Goog-Upload-Protocol": "multipart"} if upload else {})}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, headers=headers) as c:
                r = await c.request(method, self._api + url, json=body, files=files, params=params)
        except httpx.HTTPError as e:
            raise StoreError(f"cannot reach Vertex AI at {self._api}: {type(e).__name__}") from e
        if r.status_code == 404 and ok_404:
            return None
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        message = ((data.get("error") or {}).get("message") if isinstance(data, dict) else None) or r.text
        if r.status_code == 401:
            raise StoreError("Google rejected the credentials (expired or wrong account)")
        if r.status_code == 403:
            raise StoreError("Google denied access: the account needs the Vertex AI User role, and the Vertex AI API must be "
                             f"enabled for the project ({str(message)[:200]})")
        if r.status_code >= 400:
            raise StoreError(f"Vertex AI error {r.status_code}: {str(message)[:300]}")
        if isinstance(data, dict) and data.get("error"):
            raise StoreError(f"Vertex AI error: {str(message)[:300]}")
        return data

    async def _corpora(self) -> list[dict]:
        out, token = [], None
        while True:
            got = await self._req("GET", f"/v1/{self._parent}/ragCorpora", params={"pageSize": 100, **({"pageToken": token} if token else {})})
            out += got.get("ragCorpora") or []
            token = got.get("nextPageToken")
            if not token:
                return out

    async def health(self) -> dict:
        try:
            n = len(await self._corpora())
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"Vertex AI RAG Engine ({self._parent}): {n} corpus{'es' if n != 1 else ''}"}

    async def _wait(self, operation: dict) -> dict:
        for _ in range(POLL_TRIES):
            if operation.get("done"):                      # a failed operation carries an "error", which _req already raised
                return operation.get("response") or {}
            await self._sleep(POLL_SECONDS)
            operation = await self._req("GET", f"/v1/{operation['name']}")
        raise StoreError("Vertex AI is still working on it after 3 minutes; try again in a moment")

    async def ensure(self, name: str, remote: str) -> ManagedRef:
        if remote:
            if not CORPUS_RE.match(remote):
                raise StoreError("the corpus must be a corpus id or projects/.../ragCorpora/<id>")
            full = remote if remote.startswith("projects/") else f"{self._parent}/ragCorpora/{remote}"
            got = await self._req("GET", f"/v1/{full}", ok_404=True)
            if got is None:
                raise StoreError(f"RAG corpus '{remote}' was not found in {self._parent}")
            return ManagedRef(got["name"], False)
        if any(c.get("displayName") == name for c in await self._corpora()):
            raise StoreError(f"a corpus named '{name}' already exists in this project: attach it with its id, or pick another knowledge base id")
        op = await self._req("POST", f"/v1/{self._parent}/ragCorpora",
                             {"displayName": name, "description": "Created by Agent Studio"})
        corpus = (await self._wait(op)).get("name")
        if not corpus:
            raise StoreError("Vertex AI created the corpus but did not return its name; find it in the console and attach it by id")
        return ManagedRef(corpus, True)

    async def search(self, collection: str, query: str, k: int) -> list[Hit]:
        got = await self._req("POST", f"/v1/{self._parent}:retrieveContexts", {
            "vertexRagStore": {"ragResources": [{"ragCorpus": collection}]},
            "query": {"text": query, "ragRetrievalConfig": {"topK": k}}}, ok_404=True)
        hits = []
        for i, c in enumerate(((got or {}).get("contexts") or {}).get("contexts") or []):
            score = c.get("score")
            if score is None:
                score = 1.0 - float(c.get("distance", 1.0))
            name = c.get("sourceDisplayName") or ""
            hits.append(Hit(c.get("sourceUri") or f"{name}:{i}", float(score),
                            {"doc_id": name, "source": name or c.get("sourceUri") or "", "text": c.get("text") or ""}))
        return hits

    async def ingest(self, collection: str, doc_id: str, source: str, text: str) -> str:
        meta = json.dumps({"ragFile": {"displayName": doc_id, "description": source[:500]}})
        await self._req("POST", f"/upload/v1/{collection}/ragFiles:upload", upload=True, files={
            "metadata": (None, meta, "application/json"), "file": (f"{doc_id}.txt", text.encode("utf-8"), "text/plain")})
        return ""

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        names, token = [], None
        while True:
            got = await self._req("GET", f"/v1/{collection}/ragFiles", ok_404=True,
                                  params={"pageSize": 100, **({"pageToken": token} if token else {})})
            if got is None:
                return 0
            names += [f["name"] for f in got.get("ragFiles") or [] if f.get("displayName") == doc_id]
            token = got.get("nextPageToken")
            if not token:
                break
        for n in names:
            await self._req("DELETE", f"/v1/{n}", ok_404=True)
        return len(names)

    async def drop(self, collection: str) -> None:
        await self._req("DELETE", f"/v1/{collection}", ok_404=True, params={"force": "true"})


def google_token_provider(service_account_json: str = "") -> Callable[[], str]:
    """A function returning a fresh access token: from a service-account JSON, or Application Default Credentials."""
    import google.auth
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    if service_account_json:
        try:
            creds = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=scopes)
        except (ValueError, KeyError) as e:
            raise StoreError("the service account JSON is not valid") from e
    else:
        creds = None

    def token() -> str:
        nonlocal creds
        if creds is None:
            creds, _ = google.auth.default(scopes=scopes)
        if not creds.valid:
            creds.refresh(Request())
        return creds.token
    return token
