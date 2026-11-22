"""The store kinds Agent Studio can talk to, and what each needs to connect. The UI draws its forms from KINDS."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .base import ManagedStore, StoreError, VectorStore
from .bedrock_kb import BedrockKbStore, aws_clients
from .chroma import ChromaStore
from .local import LocalStore
from .pgvector import PgVectorStore
from .pinecone import PineconeStore
from .qdrant import QdrantStore
from .s3_vectors import S3VectorsStore
from .vertex_rag import VertexRagStore, google_token_provider


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    default: str = ""
    help: str = ""                                 # one or two sentences the UI shows under the field


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    description: str
    fields: tuple[Field, ...]
    factory: Callable[[dict, dict, Path], VectorStore | ManagedStore]       # (config, secrets, data dir) -> store
    docker_stack: str | None = None                          # a rag.stack id that can run this kind on this PC
    managed: bool = False                                    # the provider embeds and stores for us (no embedding model on the KB)
    ingest: str = "yes"                                      # can documents be added here: "yes" | "conditional" | "no"
    ingest_note: str = ""                                    # what decides it, in plain words
    remote: Field | None = None                              # managed kinds: the per-knowledge-base resource to attach
    validate: Callable[[dict, dict], None] | None = None     # cross-field checks beyond "required"


_FILE = re.compile(r"^[A-Za-z0-9_-]{1,40}\.sqlite$")


def _local(config: dict, secrets: dict, home: Path) -> VectorStore:
    name = config.get("file") or "local.sqlite"
    if not _FILE.match(name):
        raise StoreError("the file name must look like notes.sqlite (letters, digits, - and _)")
    return LocalStore(home / "rag" / name)


def _qdrant(config: dict, secrets: dict, home: Path) -> VectorStore:
    return QdrantStore(config.get("url", ""), secrets.get("api_key") or None)


def _pg_validate(config: dict, secrets: dict) -> None:
    if secrets.get("dsn"):
        return
    if not (config.get("host") and config.get("database") and config.get("user")):
        raise StoreError("give a connection string (DSN), or the host, database and user")


def _pgvector(config: dict, secrets: dict, home: Path) -> VectorStore:
    if secrets.get("dsn"):
        args, where = {"conninfo": secrets["dsn"]}, "the server in the connection string"
    else:
        try:
            port = int(config.get("port") or 5432)
        except (TypeError, ValueError):
            raise StoreError("the port must be a number") from None
        args = {"host": config["host"], "port": port, "dbname": config["database"], "user": config["user"],
                "sslmode": config.get("sslmode") or "prefer", **({"password": secrets["password"]} if secrets.get("password") else {})}
        where = f"{config['host']}:{port}"
    return PgVectorStore(args, where, config.get("schema") or "public")


def _chroma(config: dict, secrets: dict, home: Path) -> VectorStore:
    return ChromaStore(config.get("url", ""), secrets.get("token") or None, config.get("tenant") or "default_tenant",
                       config.get("database") or "default_database")


def _pinecone_validate(config: dict, secrets: dict) -> None:
    if not (config.get("index_host") or config.get("index_name")):
        raise StoreError("give the index host or the index name")


def _pinecone(config: dict, secrets: dict, home: Path) -> VectorStore:
    return PineconeStore(secrets.get("api_key", ""), config.get("index_host", ""), config.get("index_name", ""))


def _vertex(config: dict, secrets: dict, home: Path) -> ManagedStore:
    return VertexRagStore(config.get("project", ""), config.get("location") or "us-central1",
                          google_token_provider(secrets.get("service_account_json", "")))


def _bedrock_validate(config: dict, secrets: dict) -> None:
    if bool(secrets.get("access_key_id")) != bool(secrets.get("secret_access_key")):
        raise StoreError("set both the access key id and the secret access key, or neither")
    if config.get("s3_bucket") and not config.get("data_source_id"):
        raise StoreError("to add documents from Agent Studio, give the S3 data source id together with the bucket")


def _bedrock(config: dict, secrets: dict, home: Path) -> ManagedStore:
    clients = aws_clients(config.get("region", ""), config.get("profile", ""), secrets.get("access_key_id", ""),
                          secrets.get("secret_access_key", ""), secrets.get("session_token", ""))
    return BedrockKbStore(config.get("region", ""), clients, config.get("s3_bucket", ""),
                          config.get("s3_prefix") or "agent-studio/", config.get("data_source_id", ""))


def _s3v_validate(config: dict, secrets: dict) -> None:
    _bedrock_validate({}, secrets)                  # same rule: both keys or neither


def _s3_vectors(config: dict, secrets: dict, home: Path) -> VectorStore:
    clients = aws_clients(config.get("region", ""), config.get("profile", ""), secrets.get("access_key_id", ""),
                          secrets.get("secret_access_key", ""), secrets.get("session_token", ""))
    return S3VectorsStore(config.get("region", ""), config.get("bucket", ""), clients)


KINDS: dict[str, Kind] = {
    "local": Kind("local", "Local file", "A SQLite file on this PC. No setup, no Docker. Best for small knowledge bases.",
                  (Field("file", "File name", required=False, placeholder="local.sqlite", default="local.sqlite",
                         help="Stored under Agent Studio's data folder. Letters, digits, - and _ only."),), _local),
    "qdrant": Kind("qdrant", "Qdrant", "A Qdrant server: the Docker one Agent Studio runs, or a remote server / Qdrant Cloud.",
                   (Field("url", "URL", placeholder="http://127.0.0.1:6333", help="Include http:// or https:// and the port."),
                    Field("api_key", "API key", secret=True, required=False,
                          help="Only for servers that require one. Stored here, never shown again.")),
                   _qdrant, docker_stack="qdrant"),
    "pgvector": Kind(
        "pgvector", "PostgreSQL (pgvector)",
        "PostgreSQL with the pgvector extension: the Docker one Agent Studio runs, or any Postgres you can reach (RDS, Neon, Supabase, "
        "your own). Agent Studio embeds the text and keeps one table per knowledge base (cosine distance, HNSW index up to 2000 dimensions).",
        (Field("host", "Host", required=False, placeholder="db.example.com"),
         Field("port", "Port", required=False, placeholder="5432", default="5432"),
         Field("database", "Database", required=False),
         Field("user", "User", required=False),
         Field("password", "Password", secret=True, required=False, help="Stored here, never shown again."),
         Field("sslmode", "SSL mode", required=False, default="prefer",
               help="disable, prefer, require, verify-full. Use require or stronger for anything remote."),
         Field("schema", "Schema", required=False, default="public", help="Tables are created in this schema."),
         Field("dsn", "Connection string (DSN)", secret=True, required=False, placeholder="postgresql://user:pass@host:5432/db",
               help="Alternative to host/database/user/password. If set, the fields above are ignored. Stored here, never shown again.")),
        _pgvector, docker_stack="pgvector", validate=_pg_validate),
    "chroma": Kind(
        "chroma", "Chroma", "A Chroma server (REST API v2): the Docker one Agent Studio runs, or a remote server / Chroma Cloud. Cosine distance.",
        (Field("url", "URL", placeholder="http://127.0.0.1:8008", help="Include http:// or https:// and the port."),
         Field("token", "Token", secret=True, required=False,
               help="Bearer token for servers or proxies that require one. The local Docker server needs none."),
         Field("tenant", "Tenant", required=False, default="default_tenant"),
         Field("database", "Database", required=False, default="default_database")), _chroma, docker_stack="chroma"),
    "pinecone": Kind(
        "pinecone", "Pinecone",
        "Pinecone (remote only). Agent Studio does not create or delete indexes: use an existing serverless index with the cosine metric "
        "and the dimension of your embedding model. Each knowledge base gets its own namespace inside it.",
        (Field("api_key", "API key", secret=True, help="From the Pinecone console. Stored here, never shown again."),
         Field("index_host", "Index host", required=False, placeholder="my-index-abc123.svc.us-east-1-aws.pinecone.io",
               help="Shown on the index page of the Pinecone console. Give this or the index name."),
         Field("index_name", "Index name", required=False, placeholder="my-index",
               help="Alternative to the host: the host is looked up for you, and the metric is checked.")),
        _pinecone, validate=_pinecone_validate),
    "s3_vectors": Kind(
        "s3_vectors", "Amazon S3 Vectors",
        "Vector storage inside S3 (a vector bucket). Agent Studio embeds the text and stores the vectors, one S3 Vectors index per "
        "knowledge base (cosine). Pay-per-use and no server to run, but queries are slower than a vector database: good for large, "
        "rarely-queried knowledge bases. The bucket is created on first use if it does not exist.",
        (Field("region", "AWS region", placeholder="us-east-1", help="S3 Vectors is available in a limited set of regions."),
         Field("bucket", "Vector bucket name", placeholder="my-agent-vectors",
               help="3-63 characters: lowercase letters, digits and -. This is a vector bucket, not a normal S3 bucket."),
         Field("access_key_id", "Access key id", secret=True, required=False,
               help="Leave all credential fields empty to use the default AWS credential chain (env, ~/.aws, SSO, role). "
                    "Needs the s3vectors:* permissions."),
         Field("secret_access_key", "Secret access key", secret=True, required=False, help="Required together with the access key id. Stored here, never shown again."),
         Field("session_token", "Session token", secret=True, required=False, help="Only for temporary credentials (STS/SSO). Stored here, never shown again."),
         Field("profile", "AWS profile", required=False, help="A named profile from ~/.aws/config (ignored when keys are set).")),
        _s3_vectors, validate=_s3v_validate),
    "vertex_rag": Kind(
        "vertex_rag", "Google Vertex AI RAG Engine",
        "Google chunks, embeds and stores your documents in a RAG corpus; there is no embedding model to choose. Documents can be uploaded "
        "from Agent Studio (text, Markdown, PDF, up to 25 MB each). Search is by text and ranked by Google.",
        (Field("project", "Google Cloud project id", placeholder="my-project-123", help="The Vertex AI API must be enabled for it."),
         Field("location", "Location", required=False, default="us-central1", placeholder="us-central1",
               help="RAG Engine is available in a limited set of regions."),
         Field("service_account_json", "Service account JSON", secret=True, required=False,
               help="Paste the key file. Leave empty to use this PC's Application Default Credentials "
                    "(gcloud auth application-default login). Needs the Vertex AI User role.")),
        _vertex, managed=True, ingest="yes",
        ingest_note="Documents you add are uploaded to the corpus; Google indexes them automatically.",
        remote=Field("corpus", "Existing RAG corpus", required=False, placeholder="1234567890123456789",
                     help="Leave empty to create a new corpus for this knowledge base (it is deleted with it). "
                          "Give a corpus id to attach one you already have (it is never deleted).")),
    "bedrock_kb": Kind(
        "bedrock_kb", "Amazon Bedrock Knowledge Base",
        "Attach an existing Bedrock knowledge base: AWS does the chunking, embedding and storage. Search always works. Adding documents "
        "from Agent Studio needs an S3 bucket and data source id below; without them it is search-only.",
        (Field("region", "AWS region", placeholder="us-east-1"),
         Field("access_key_id", "Access key id", secret=True, required=False,
               help="Leave all credential fields empty to use the default AWS credential chain (env, ~/.aws, SSO, role)."),
         Field("secret_access_key", "Secret access key", secret=True, required=False, help="Required together with the access key id. Stored here, never shown again."),
         Field("session_token", "Session token", secret=True, required=False, help="Only for temporary credentials (STS/SSO). Stored here, never shown again."),
         Field("profile", "AWS profile", required=False, help="A named profile from ~/.aws/config (ignored when keys are set)."),
         Field("s3_bucket", "S3 bucket (to add documents)", required=False,
               help="The bucket behind the knowledge base's S3 data source. Optional: without it this knowledge base is search-only."),
         Field("s3_prefix", "S3 prefix", required=False, default="agent-studio/",
               help="Where uploaded documents go. Must be inside the data source's inclusion prefixes, if it has any."),
         Field("data_source_id", "Data source id", required=False, placeholder="ABCDEFGHIJ",
               help="The 10-character id of that S3 data source. Required together with the bucket.")),
        _bedrock, managed=True, ingest="conditional",
        ingest_note="Search-only unless an S3 bucket and data source id are set. Indexing runs in AWS and takes about a minute.",
        remote=Field("knowledge_base_id", "Bedrock knowledge base id", placeholder="ABCDEFGHIJ",
                     help="The 10-character id from the AWS console. Agent Studio cannot create Bedrock knowledge bases (they need an "
                          "IAM role and a vector database); it attaches yours and never deletes it."),
        validate=_bedrock_validate),
}


def kind_info(kind_id: str) -> Kind:
    if kind_id not in KINDS:
        raise StoreError(f"unknown store kind '{kind_id}' (use one of: {', '.join(KINDS)})")
    return KINDS[kind_id]


def make_store(kind_id: str, config: dict, secrets: dict, home: Path) -> VectorStore | ManagedStore:
    return kind_info(kind_id).factory(config, secrets, home)


def check_fields(kind_id: str, config: dict, secrets: dict) -> None:
    """Raise StoreError if a required field is missing or the combination of fields cannot work."""
    info = kind_info(kind_id)
    for f in info.fields:
        have = secrets.get(f.name) if f.secret else config.get(f.name)
        if f.required and not have:
            raise StoreError(f"{f.label} is required for a {info.label} store")
    if info.validate:
        info.validate(config, secrets)
