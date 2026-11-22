"""Where artifacts are kept. Several storages can be on at once; each artifact is copied to the ones its rule names.

ArtifactStorage is the abstract interface (put / get / delete / health). Implementations: a folder on this PC (always available),
Amazon S3, any S3-compatible service (MinIO, Cloudflare R2, DigitalOcean Spaces, Backblaze B2...), Google Cloud Storage, Azure Blob
Storage (with a SAS token) and WebDAV (Nextcloud, ownCloud...). Credentials are passed in separately and never appear in an error.

The cloud calls were written against the services' own references (S3 put/get/delete object, the GCS JSON API
upload/storage/v1/b/BUCKET/o?uploadType=media, Azure Put Blob with x-ms-blob-type: BlockBlob, WebDAV PUT/MKCOL/GET/DELETE) and are tested
against in-memory fakes only.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from ..rag.stores.bedrock_kb import REGION_RE

BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9!_.*'()/-]{0,200}$")
ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){1,62}$")
TIMEOUT = 60.0
GCS_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
AZURE_VERSION = "2023-11-03"


class StorageError(Exception):
    """The storage cannot do this, or its settings are wrong. Safe to show: it never contains a credential."""


class ArtifactStorage(ABC):
    kind = ""
    cloud = False                                   # true = the bytes leave this PC

    @abstractmethod
    def put(self, key: str, data: bytes, mime: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def health(self) -> dict: ...

    @abstractmethod
    def where(self) -> str: ...


def _prefix(cfg: dict) -> str:
    p = cfg.get("prefix") if cfg.get("prefix") is not None else "agent-studio/artifacts/"
    if not PREFIX_RE.match(p) or ".." in p:
        raise StorageError("the prefix contains characters object keys should not use")
    return p if not p or p.endswith("/") else p + "/"


def _https_url(url: str, what: str, allow_http: bool = False) -> str:
    u = urlsplit(url or "")
    if u.scheme not in (("https", "http") if allow_http else ("https",)) or not u.hostname:
        raise StorageError(f"{what} must be a full https:// address")
    return url.rstrip("/")


# ---------------------------------------------------------------------------------------------------- this PC
class LocalStorage(ArtifactStorage):
    kind = "local"

    def __init__(self, cfg: dict, secrets: dict, home: Path):
        folder = (cfg.get("folder") or "").strip()
        self.root = Path(folder).expanduser() if folder else Path(home) / "artifacts"
        if not self.root.is_absolute():
            raise StorageError("the folder must be a full path (for example D:\\agent-artifacts)")

    def where(self) -> str:
        return str(self.root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()) or ".." in key.split("/"):
            raise StorageError("that key is outside the artifacts folder")
        return p

    def put(self, key: str, data: bytes, mime: str) -> None:
        p = self._path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        except OSError as e:
            raise StorageError(f"cannot write to {self.root}: {type(e).__name__}") from e

    def get(self, key: str) -> bytes:
        p = self._path(key)
        try:
            return p.read_bytes()
        except FileNotFoundError as e:
            raise StorageError("the file is not in this folder any more") from e
        except OSError as e:
            raise StorageError(f"cannot read from {self.root}: {type(e).__name__}") from e

    def delete(self, key: str) -> None:
        p = self._path(key)
        try:
            p.unlink(missing_ok=True)
            d = p.parent                                                 # remove the artifact's own (now empty) folder
            if d.is_dir() and d != self.root.resolve() and not any(d.iterdir()):
                d.rmdir()
        except OSError as e:
            raise StorageError(f"cannot delete from {self.root}: {type(e).__name__}") from e

    def health(self) -> dict:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".write-test"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as e:
            return {"ok": False, "detail": f"cannot write to {self.root}: {type(e).__name__}"}
        return {"ok": True, "detail": f"{self.root} is writable"}


# ---------------------------------------------------------------------------------------------------- S3 and S3-compatible
def s3_clients(region: str, profile: str, key_id: str, secret: str, token: str, endpoint: str, path_style: bool):
    """service name -> boto3 client. Explicit keys, else a profile, else the default credential chain."""
    import boto3
    from botocore.config import Config
    kw: dict = {"region_name": region}
    if key_id:
        kw.update(aws_access_key_id=key_id, aws_secret_access_key=secret, **({"aws_session_token": token} if token else {}))
    elif profile:
        kw["profile_name"] = profile
    try:
        session = boto3.Session(**kw)
    except Exception as e:  # noqa: BLE001 - e.g. ProfileNotFound
        raise StorageError(f"AWS settings are not usable: {type(e).__name__}") from e
    cache: dict = {}

    def client(service: str):
        if service not in cache:
            cache[service] = session.client(service, endpoint_url=endpoint or None, config=Config(s3={"addressing_style": "path" if path_style else "auto"}))
        return cache[service]
    return client


class S3Storage(ArtifactStorage):
    kind = "s3"
    cloud = True

    def __init__(self, cfg: dict, secrets: dict, home: Path, clients=None):
        endpoint = (cfg.get("endpoint") or "").strip()
        self.compatible = self.kind == "s3_compatible"
        if self.compatible:
            endpoint = _https_url(endpoint, "the service address", allow_http=True)
        region = (cfg.get("region") or ("us-east-1" if self.compatible else "")).strip()
        if not REGION_RE.match(region) and not (self.compatible and region == "auto"):
            raise StorageError("the region must look like us-east-1" + (" (or 'auto' for Cloudflare R2)" if self.compatible else ""))
        if not BUCKET_RE.match(cfg.get("bucket") or ""):
            raise StorageError("the bucket name looks wrong")
        self.region, self.bucket, self.endpoint, self.prefix = region, cfg["bucket"], endpoint, _prefix(cfg)
        self._clients = clients or s3_clients(region, cfg.get("profile", ""), secrets.get("access_key_id", ""), secrets.get("secret_access_key", ""),
                                              secrets.get("session_token", ""), endpoint, bool(cfg.get("path_style")) or self.compatible)

    def where(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}" + (f" at {urlsplit(self.endpoint).hostname}" if self.endpoint else f" in {self.region}")

    def _call(self, op: str, **kw):
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
        try:
            return getattr(self._clients("s3"), op)(Bucket=self.bucket, **kw)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "error")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise StorageError("the object is not in the bucket any more") from e
            if code in ("AccessDenied", "403", "InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken"):
                raise StorageError(f"the storage refused the credentials or the permission ({code})") from e
            raise StorageError(f"the storage answered an error ({code})") from e
        except NoCredentialsError as e:
            raise StorageError("no credentials found: set an access key, a profile, or sign in with the AWS CLI") from e
        except BotoCoreError as e:
            raise StorageError(f"cannot reach the storage: {type(e).__name__}") from e

    def put(self, key: str, data: bytes, mime: str) -> None:
        self._call("put_object", Key=self.prefix + key, Body=data, ContentType=mime)

    def get(self, key: str) -> bytes:
        return self._call("get_object", Key=self.prefix + key)["Body"].read()

    def delete(self, key: str) -> None:
        self._call("delete_object", Key=self.prefix + key)

    def health(self) -> dict:
        try:
            self._call("head_bucket")
        except StorageError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"bucket '{self.bucket}' is reachable"}


class S3CompatibleStorage(S3Storage):
    kind = "s3_compatible"


# ---------------------------------------------------------------------------------------------------- HTTP-based storages
class _Http:
    """Shared plumbing: one httpx client (a MockTransport in tests) and the same error wording everywhere."""

    def __init__(self, transport: httpx.BaseTransport | None):
        self._transport = transport

    def _send(self, method: str, url: str, host: str, ok: tuple[int, ...] = (200,), **kw) -> httpx.Response:
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT) as c:
                r = c.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise StorageError(f"cannot reach {host}: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise StorageError(f"{host} refused the credentials or the permission (HTTP {r.status_code})")
        if r.status_code == 404 and method != "MKCOL":
            raise StorageError("the object is not in the storage any more")
        if r.status_code not in ok:
            raise StorageError(f"{host} answered HTTP {r.status_code}")
        return r


class GcsStorage(_Http, ArtifactStorage):
    kind = "gcs"
    cloud = True

    def __init__(self, cfg: dict, secrets: dict, home: Path, transport: httpx.BaseTransport | None = None, token=None):
        _Http.__init__(self, transport)
        if not BUCKET_RE.match(cfg.get("bucket") or ""):
            raise StorageError("the bucket name looks wrong")
        self.bucket, self.prefix = cfg["bucket"], _prefix(cfg)
        self._sa = secrets.get("service_account_json", "")
        self._token = token or self._real_token

    def where(self) -> str:
        return f"gs://{self.bucket}/{self.prefix}"

    def _real_token(self) -> str:
        import json
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import service_account
        try:
            if self._sa:
                creds = service_account.Credentials.from_service_account_info(json.loads(self._sa), scopes=[GCS_SCOPE])
            else:
                creds, _ = google.auth.default(scopes=[GCS_SCOPE])
            creds.refresh(google.auth.transport.requests.Request())
        except Exception as e:  # noqa: BLE001 - bad JSON, no default credentials, token refused
            raise StorageError(f"Google credentials are not usable: {type(e).__name__}") from e
        return creds.token

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    def _obj(self, key: str) -> str:
        return f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{quote(self.prefix + key, safe='')}"

    def put(self, key: str, data: bytes, mime: str) -> None:
        self._send("POST", f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o?uploadType=media&name={quote(self.prefix + key, safe='')}",
                   "Google Cloud Storage", content=data, headers={**self._h(), "Content-Type": mime})

    def get(self, key: str) -> bytes:
        return self._send("GET", self._obj(key) + "?alt=media", "Google Cloud Storage", headers=self._h()).content

    def delete(self, key: str) -> None:
        try:
            self._send("DELETE", self._obj(key), "Google Cloud Storage", ok=(200, 204), headers=self._h())
        except StorageError as e:
            if "not in the storage" not in str(e):                    # deleting something already gone is fine
                raise

    def health(self) -> dict:
        try:
            self._send("GET", f"https://storage.googleapis.com/storage/v1/b/{self.bucket}", "Google Cloud Storage", headers=self._h())
        except StorageError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"bucket '{self.bucket}' is reachable"}


class AzureBlobStorage(_Http, ArtifactStorage):
    kind = "azure_blob"
    cloud = True

    def __init__(self, cfg: dict, secrets: dict, home: Path, transport: httpx.BaseTransport | None = None):
        _Http.__init__(self, transport)
        if not ACCOUNT_RE.match(cfg.get("account") or ""):
            raise StorageError("the storage account name is 3-24 lowercase letters and digits")
        if not CONTAINER_RE.match(cfg.get("container") or ""):
            raise StorageError("the container name looks wrong (lowercase letters, digits and dashes)")
        sas = (secrets.get("sas_token") or "").lstrip("?")
        if not sas:
            raise StorageError("a SAS token is needed (create one for the container with read, write, delete and list permission)")
        self.account, self.container, self.prefix = cfg["account"], cfg["container"], _prefix(cfg)
        self.base = _https_url(cfg.get("endpoint") or f"https://{self.account}.blob.core.windows.net", "the service address")
        self._sas = sas

    def where(self) -> str:
        return f"{self.base}/{self.container}/{self.prefix}"

    def _url(self, key: str | None = None, extra: str = "") -> str:
        path = f"/{self.container}" + (f"/{quote(self.prefix + key, safe='/')}" if key else "")
        return f"{self.base}{path}?{extra}{self._sas}"

    _H = {"x-ms-version": AZURE_VERSION}

    def put(self, key: str, data: bytes, mime: str) -> None:
        self._send("PUT", self._url(key), "Azure Blob Storage", ok=(201,), content=data, headers={**self._H, "x-ms-blob-type": "BlockBlob", "Content-Type": mime})

    def get(self, key: str) -> bytes:
        return self._send("GET", self._url(key), "Azure Blob Storage", headers=self._H).content

    def delete(self, key: str) -> None:
        try:
            self._send("DELETE", self._url(key), "Azure Blob Storage", ok=(200, 202), headers=self._H)
        except StorageError as e:
            if "not in the storage" not in str(e):
                raise

    def health(self) -> dict:
        try:
            self._send("GET", self._url(None, "restype=container&"), "Azure Blob Storage", headers=self._H)
        except StorageError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"container '{self.container}' is reachable"}


class WebDavStorage(_Http, ArtifactStorage):
    kind = "webdav"
    cloud = True

    def __init__(self, cfg: dict, secrets: dict, home: Path, transport: httpx.BaseTransport | None = None):
        _Http.__init__(self, transport)
        self.base = _https_url(cfg.get("url") or "", "the WebDAV address", allow_http=bool(cfg.get("allow_http")))
        self.prefix = _prefix(cfg)
        self._auth = (cfg.get("username") or "", secrets.get("password", "")) if cfg.get("username") else None
        self._host = urlsplit(self.base).hostname or "the WebDAV server"

    def where(self) -> str:
        return f"{self.base}/{self.prefix}"

    def _u(self, rel: str) -> str:
        return f"{self.base}/{quote(rel.strip('/'), safe='/')}"

    def _req(self, method: str, rel: str, ok: tuple[int, ...], **kw) -> httpx.Response:
        return self._send(method, self._u(rel), self._host, ok=ok, auth=self._auth, **kw)

    def put(self, key: str, data: bytes, mime: str) -> None:
        parts = (self.prefix + key).split("/")[:-1]
        for i in range(1, len(parts) + 1):                            # create each folder on the way (405 = it already exists)
            self._req("MKCOL", "/".join(parts[:i]), ok=(201, 405, 301))
        self._req("PUT", self.prefix + key, ok=(200, 201, 204), content=data, headers={"Content-Type": mime})

    def get(self, key: str) -> bytes:
        return self._req("GET", self.prefix + key, ok=(200,)).content

    def delete(self, key: str) -> None:
        try:
            self._req("DELETE", self.prefix + key, ok=(200, 204))
        except StorageError as e:
            if "not in the storage" not in str(e):
                raise

    def health(self) -> dict:
        try:
            self._req("PROPFIND", self.prefix.strip("/"), ok=(200, 207), headers={"Depth": "0"})
        except StorageError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"{self._host} is reachable"}


# (name, label, secret, required, placeholder)
KINDS: dict[str, dict] = {
    "local": dict(label="A folder on this PC", cls=LocalStorage, cloud=False, fields=[("folder", "Folder (empty = Agent Studio's data folder)", False, False, "D:\\agent-artifacts")],
                  note="The default. Nothing leaves this PC."),
    "s3": dict(label="Amazon S3", cls=S3Storage, cloud=True, note="Leave the credentials empty to use your default AWS sign-in. Files are uploaded to your bucket.",
               fields=[("region", "AWS region", False, True, "us-east-1"), ("bucket", "Bucket", False, True, ""), ("prefix", "Folder inside the bucket", False, False, "agent-studio/artifacts/"),
                       ("profile", "AWS profile", False, False, ""), ("access_key_id", "Access key id", True, False, ""), ("secret_access_key", "Secret access key", True, False, ""),
                       ("session_token", "Session token", True, False, "")]),
    "s3_compatible": dict(label="S3-compatible (MinIO, Cloudflare R2, DigitalOcean Spaces, Backblaze B2...)", cls=S3CompatibleStorage, cloud=True,
                          note="Any service that speaks the S3 API. Give its address, for example https://<account>.r2.cloudflarestorage.com (region 'auto') or https://nyc3.digitaloceanspaces.com.",
                          fields=[("endpoint", "Service address", False, True, "https://minio.example.com"), ("region", "Region", False, False, "us-east-1"), ("bucket", "Bucket", False, True, ""),
                                  ("prefix", "Folder inside the bucket", False, False, "agent-studio/artifacts/"), ("access_key_id", "Access key id", True, True, ""),
                                  ("secret_access_key", "Secret access key", True, True, "")]),
    "gcs": dict(label="Google Cloud Storage", cls=GcsStorage, cloud=True, note="Leave the key empty to use Application Default Credentials (gcloud auth application-default login).",
                fields=[("bucket", "Bucket", False, True, ""), ("prefix", "Folder inside the bucket", False, False, "agent-studio/artifacts/"),
                        ("service_account_json", "Service account key (JSON)", True, False, "")]),
    "azure_blob": dict(label="Azure Blob Storage", cls=AzureBlobStorage, cloud=True, note="Uses a SAS token for one container (read, write, delete, list). Shared-key signing is not supported.",
                       fields=[("account", "Storage account", False, True, ""), ("container", "Container", False, True, ""), ("prefix", "Folder inside the container", False, False, "agent-studio/artifacts/"),
                               ("endpoint", "Service address (empty = the public cloud)", False, False, ""), ("sas_token", "SAS token", True, True, "")]),
    "webdav": dict(label="WebDAV (Nextcloud, ownCloud...)", cls=WebDavStorage, cloud=True, note="The address of the WebDAV folder, for example https://cloud.example.com/remote.php/dav/files/you.",
                   fields=[("url", "WebDAV address", False, True, "https://cloud.example.com/remote.php/dav/files/you"), ("prefix", "Folder", False, False, "agent-studio/artifacts/"),
                           ("username", "User name", False, False, ""), ("password", "Password or app password", True, False, "")]),
}


def build_storage(kind: str, cfg: dict, secrets: dict, home: Path, **kw) -> ArtifactStorage:
    if kind not in KINDS:
        raise StorageError(f"unknown storage '{kind}' (use one of: {', '.join(KINDS)})")
    return KINDS[kind]["cls"](cfg, secrets, home, **kw)
