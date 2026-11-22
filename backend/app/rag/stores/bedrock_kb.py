"""Amazon Bedrock Knowledge Bases (managed RAG: AWS chunks, embeds and stores the documents).

Bedrock cannot create a knowledge base without an IAM role and a vector database of its own, so Agent Studio ATTACHES an
existing knowledge base (its 10-character id) and never creates or deletes it.

Search always works (Retrieve API). Ingestion works only if you also give an S3 bucket + the id of the S3 data source of
that knowledge base: documents are written to s3://bucket/<prefix><kb>/<doc id>.txt and an ingestion job is started.
Bedrock runs one job at a time and indexes in the background, so a new document may take a minute to become searchable
(use Sync to re-run it). Without a bucket the knowledge base is search-only.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from .base import Hit, ManagedRef, ManagedStore, StoreError

KB_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
DS_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9!_.*'()/-]{0,200}$")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")
DENIED = {"AccessDeniedException", "AccessDenied", "UnrecognizedClientException", "InvalidSignatureException",
          "ExpiredTokenException", "ExpiredToken", "InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidClientTokenId"}
MISSING = {"ResourceNotFoundException", "NoSuchKey", "NoSuchBucket", "NotFound", "404"}


def aws_clients(region: str, profile: str = "", access_key_id: str = "", secret_access_key: str = "",
                session_token: str = "") -> Callable[[str], object]:
    """service name -> boto3 client, using explicit keys, a named profile, or the default credential chain."""
    import boto3
    kw = {"region_name": region}
    if access_key_id:
        kw.update(aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key, **({"aws_session_token": session_token} if session_token else {}))
    elif profile:
        kw["profile_name"] = profile
    try:
        session = boto3.Session(**kw)
    except Exception as e:  # noqa: BLE001 - e.g. ProfileNotFound
        raise StoreError(f"AWS settings are not usable: {type(e).__name__}: {str(e)[:200]}") from e
    cache: dict[str, object] = {}

    def client(service: str):
        if service not in cache:
            cache[service] = session.client(service)
        return cache[service]
    return client


class BedrockKbStore(ManagedStore):
    kind = "bedrock_kb"

    def __init__(self, region: str, clients: Callable[[str], object], bucket: str = "", prefix: str = "agent-studio/",
                 data_source_id: str = ""):
        if not REGION_RE.match(region or ""):
            raise StoreError("the AWS region must look like us-east-1")
        if bucket and not BUCKET_RE.match(bucket):
            raise StoreError("the S3 bucket name looks wrong")
        if bucket and not DS_ID_RE.match(data_source_id or ""):
            raise StoreError("to add documents, also give the id of the knowledge base's S3 data source (10 characters)")
        if not PREFIX_RE.match(prefix):
            raise StoreError("the S3 prefix contains characters S3 keys should not use")
        self.region, self._clients, self.bucket, self.data_source_id = region, clients, bucket, data_source_id
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        if not bucket:
            self.ingest_note = ("this knowledge base is search-only: documents live in the Bedrock data source. Add them in AWS, "
                                "or set an S3 bucket and data source id on the store to upload from here")

    @property
    def can_ingest(self) -> bool:
        return bool(self.bucket)

    # ------------------------------------------------------------ plumbing
    def _call_sync(self, service: str, op: str, ok_missing: bool = False, **kw):
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
        try:
            return getattr(self._clients(service), op)(**kw)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            message = str(e.response.get("Error", {}).get("Message", ""))[:300]
            if code in MISSING:
                if ok_missing:
                    return None
                raise StoreError(f"not found in AWS ({code}): {message}") from e
            if code in DENIED:
                raise StoreError(f"AWS rejected the credentials or they lack permission ({code}): {message}") from e
            raise StoreError(f"AWS error {code}: {message}") from e
        except NoCredentialsError as e:
            raise StoreError("no AWS credentials found: set keys on the store, a profile, or sign in with the AWS CLI") from e
        except BotoCoreError as e:
            raise StoreError(f"cannot reach Amazon Bedrock in {self.region}: {type(e).__name__}") from e

    async def _call(self, service: str, op: str, **kw):
        return await asyncio.to_thread(lambda: self._call_sync(service, op, **kw))

    async def health(self) -> dict:
        try:
            got = await self._call("bedrock-agent", "list_knowledge_bases", maxResults=50)
        except StoreError as e:
            return {"ok": False, "detail": str(e)}
        n = len(got.get("knowledgeBaseSummaries") or [])
        return {"ok": True, "detail": f"Amazon Bedrock Knowledge Bases in {self.region}: {n} knowledge base{'s' if n != 1 else ''} visible"
                                      + ("" if self.bucket else " (search-only)")}

    async def ensure(self, name: str, remote: str) -> ManagedRef:
        if not KB_ID_RE.match(remote or ""):
            raise StoreError("enter the Bedrock knowledge base id (10 letters/digits, from the AWS console): Agent Studio cannot "
                             "create Bedrock knowledge bases, it attaches an existing one")
        got = await self._call("bedrock-agent", "get_knowledge_base", ok_missing=True, knowledgeBaseId=remote)
        if got is None:
            raise StoreError(f"Bedrock knowledge base '{remote}' was not found in {self.region}")
        status = (got.get("knowledgeBase") or {}).get("status")
        if status != "ACTIVE":
            raise StoreError(f"the Bedrock knowledge base is {status}, not ACTIVE yet")
        if self.bucket:
            ds = await self._call("bedrock-agent", "get_data_source", ok_missing=True,
                                  knowledgeBaseId=remote, dataSourceId=self.data_source_id)
            if ds is None:
                raise StoreError(f"data source '{self.data_source_id}' was not found in this knowledge base")
            arn = (((ds.get("dataSource") or {}).get("dataSourceConfiguration") or {}).get("s3Configuration") or {}).get("bucketArn")
            if arn != f"arn:aws:s3:::{self.bucket}":
                raise StoreError(f"that data source does not read from the bucket '{self.bucket}'")
        return ManagedRef(remote, False)

    async def search(self, collection: str, query: str, k: int) -> list[Hit]:
        got = await self._call("bedrock-agent-runtime", "retrieve", ok_missing=True, knowledgeBaseId=collection,
                               retrievalQuery={"text": query},
                               retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": k}})
        hits = []
        mine = f"s3://{self.bucket}/{self.prefix}{collection}/" if self.bucket else None
        for i, r in enumerate((got or {}).get("retrievalResults") or []):
            loc = r.get("location") or {}
            uri = (loc.get("s3Location") or {}).get("uri") or (loc.get("webLocation") or {}).get("url") or ""
            doc_id = uri[len(mine):].removesuffix(".txt") if mine and uri.startswith(mine) else None
            hits.append(Hit(uri or str(i), float(r.get("score") or 0.0), {"doc_id": doc_id, "source": uri, "text": (r.get("content") or {}).get("text") or ""}))
        return hits

    # ------------------------------------------------------------ ingestion
    def _key(self, collection: str, doc_id: str) -> str:
        return f"{self.prefix}{collection}/{doc_id}.txt"

    async def _start_job(self, collection: str) -> str:
        try:
            await self._call("bedrock-agent", "start_ingestion_job", knowledgeBaseId=collection, dataSourceId=self.data_source_id)
        except StoreError as e:
            if "ConflictException" in str(e):
                return "another sync is already running: run Sync again in a minute so this document is picked up"
            raise
        return "AWS is indexing the document in the background (about a minute before it can be found)"

    async def ingest(self, collection: str, doc_id: str, source: str, text: str) -> str:
        if not self.bucket:
            raise StoreError(self.ingest_note)
        await self._call("s3", "put_object", Bucket=self.bucket, Key=self._key(collection, doc_id),
                         Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8")
        return await self._start_job(collection)

    async def delete_doc(self, collection: str, doc_id: str) -> int:
        if not self.bucket:
            raise StoreError(self.ingest_note)
        await self._call("s3", "delete_object", Bucket=self.bucket, Key=self._key(collection, doc_id))
        await self._start_job(collection)
        return 1

    async def sync(self, collection: str) -> str:
        if not self.bucket:
            raise StoreError(self.ingest_note)
        return await self._start_job(collection)

    async def drop(self, collection: str) -> None:
        """We never own the Bedrock knowledge base, so there is nothing to delete."""
