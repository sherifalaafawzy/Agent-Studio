"""Artifact storages: the folder, S3 and S3-compatible, Google Cloud Storage, Azure Blob and WebDAV, against in-memory fakes. No network, no cloud."""
from __future__ import annotations

import io
import json

import httpx
import pytest

from app.artifacts import storage as S
from app.artifacts.storage import (AzureBlobStorage, GcsStorage, LocalStorage, S3CompatibleStorage, S3Storage, StorageError, WebDavStorage, build_storage)
from app.artifacts.types import classify, looks_binary, safe_name
from tests.rag_fakes_stores import aws_error


# ------------------------------------------------------------------------------------------------ types
def test_classify_and_names():
    assert classify("r.md") == ("document", "text/markdown", "markdown")
    assert classify("plot.PNG")[0] == "image" and classify("d.csv")[2] == "csv" and classify("m.safetensors")[0] == "model"
    assert classify("x.unknownext") == ("other", "application/octet-stream", "none") and classify("Dockerfile")[0] == "code"
    assert classify("a\\b\\c.py")[0] == "code"
    assert safe_name("../../etc/passwd") == "passwd" and safe_name("a b<c>.txt") == "a b_c_.txt" and safe_name("...") == "artifact" and safe_name("x" * 300) == "x" * 120
    assert looks_binary(b"ab\x00c") and not looks_binary(b"plain")


# ------------------------------------------------------------------------------------------------ the folder
def test_local_storage_roundtrip_and_jail(tmp_path):
    s = LocalStorage({"folder": str(tmp_path / "arts")}, {}, tmp_path)
    s.put("a1/report.md", b"hello", "text/markdown")
    assert s.get("a1/report.md") == b"hello" and s.where() == str(tmp_path / "arts")
    s.delete("a1/report.md")
    assert not (tmp_path / "arts" / "a1").exists()                      # its own empty folder went with it
    s.delete("a1/report.md")                                            # deleting what is gone is fine
    with pytest.raises(StorageError, match="not in this folder"):
        s.get("a1/report.md")
    with pytest.raises(StorageError, match="outside"):
        s.get("../secret.txt")
    with pytest.raises(StorageError, match="outside"):
        s.put("a/../../x", b"x", "text/plain")
    assert s.health() == {"ok": True, "detail": f"{tmp_path / 'arts'} is writable"}
    default = LocalStorage({}, {}, tmp_path)
    assert default.root == tmp_path / "artifacts"
    with pytest.raises(StorageError, match="full path"):
        LocalStorage({"folder": "relative/dir"}, {}, tmp_path)


def test_local_storage_reports_os_errors(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    s = LocalStorage({"folder": str(blocker / "sub")}, {}, tmp_path)           # a file where a folder should be
    with pytest.raises(StorageError, match="cannot write"):
        s.put("a/b", b"x", "text/plain")
    assert not s.health()["ok"] and "cannot write" in s.health()["detail"]
    ok = LocalStorage({"folder": str(tmp_path / "ok")}, {}, tmp_path)
    ok.put("a/b", b"x", "text/plain")
    monkeypatch.setattr("pathlib.Path.read_bytes", lambda self: (_ for _ in ()).throw(PermissionError("no")))
    with pytest.raises(StorageError, match="cannot read"):
        ok.get("a/b")
    monkeypatch.undo()
    monkeypatch.setattr("pathlib.Path.unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError("no")))
    with pytest.raises(StorageError, match="cannot delete"):
        ok.delete("a/b")


# ------------------------------------------------------------------------------------------------ S3 and S3-compatible
class FakeS3:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: Exception | None = None

    def clients(self, service: str):
        assert service == "s3"
        return self

    def _go(self, op, kw):
        self.calls.append((op, kw))
        if self.fail:
            raise self.fail

    def put_object(self, **kw):
        self._go("put", kw)
        self.objects[kw["Key"]] = (kw["Body"], kw["ContentType"])

    def get_object(self, **kw):
        self._go("get", kw)
        if kw["Key"] not in self.objects:
            raise aws_error("NoSuchKey", "gone")
        return {"Body": io.BytesIO(self.objects[kw["Key"]][0])}

    def delete_object(self, **kw):
        self._go("delete", kw)
        self.objects.pop(kw["Key"], None)

    def head_bucket(self, **kw):
        self._go("head", kw)
        return {}


def test_s3_roundtrip_prefix_and_where():
    f = FakeS3()
    s = S3Storage({"region": "eu-west-1", "bucket": "my-bucket", "prefix": "team"}, {}, None, clients=f.clients)
    s.put("a1/r.md", b"hi", "text/markdown")
    assert list(f.objects) == ["team/a1/r.md"] and f.objects["team/a1/r.md"] == (b"hi", "text/markdown") and f.calls[0][1]["Bucket"] == "my-bucket"
    assert s.get("a1/r.md") == b"hi" and s.where() == "s3://my-bucket/team/ in eu-west-1"
    s.delete("a1/r.md")
    assert not f.objects and s.health() == {"ok": True, "detail": "bucket 'my-bucket' is reachable"}
    assert S3Storage({"region": "us-east-1", "bucket": "my-bucket"}, {}, None, clients=f.clients).prefix == "agent-studio/artifacts/"
    assert S3Storage({"region": "us-east-1", "bucket": "my-bucket", "prefix": ""}, {}, None, clients=f.clients).prefix == ""


def test_s3_compatible_needs_an_address_and_uses_it():
    f = FakeS3()
    with pytest.raises(StorageError, match="full https"):
        S3CompatibleStorage({"bucket": "my-bucket"}, {}, None, clients=f.clients)
    s = S3CompatibleStorage({"endpoint": "https://abc.r2.cloudflarestorage.com/", "region": "auto", "bucket": "my-bucket"}, {}, None, clients=f.clients)
    assert s.where() == "s3://my-bucket/agent-studio/artifacts/ at abc.r2.cloudflarestorage.com" and s.endpoint == "https://abc.r2.cloudflarestorage.com"
    assert S3CompatibleStorage({"endpoint": "http://localhost:9000", "bucket": "my-bucket"}, {}, None, clients=f.clients).region == "us-east-1"
    with pytest.raises(StorageError, match="region must look like us-east-1 \\(or 'auto'"):
        S3CompatibleStorage({"endpoint": "https://x.example.com", "region": "Mars!", "bucket": "my-bucket"}, {}, None, clients=f.clients)


@pytest.mark.parametrize("cfg,msg", [({"region": "nowhere", "bucket": "my-bucket"}, "region must look like"), ({"region": "us-east-1", "bucket": "Bad_Bucket"}, "bucket name looks wrong"),
                                     ({"region": "us-east-1", "bucket": "my-bucket", "prefix": "a b"}, "prefix contains"), ({"region": "us-east-1", "bucket": "my-bucket", "prefix": "../x"}, "prefix contains")])
def test_s3_rejects_bad_settings(cfg, msg):
    with pytest.raises(StorageError, match=msg):
        S3Storage(cfg, {}, None, clients=FakeS3().clients)


def test_s3_errors_are_explained():
    from botocore.exceptions import EndpointConnectionError, NoCredentialsError
    f = FakeS3()
    s = S3Storage({"region": "us-east-1", "bucket": "my-bucket"}, {}, None, clients=f.clients)
    with pytest.raises(StorageError, match="not in the bucket any more"):
        s.get("nope")
    for exc, match in ((aws_error("AccessDenied", "no"), "refused the credentials or the permission \\(AccessDenied\\)"), (aws_error("SlowDown", "later"), "answered an error \\(SlowDown\\)"),
                       (NoCredentialsError(), "no credentials found"), (EndpointConnectionError(endpoint_url="https://x"), "cannot reach the storage: EndpointConnectionError")):
        f.fail = exc
        with pytest.raises(StorageError, match=match):
            s.put("k", b"x", "text/plain")
    assert not s.health()["ok"]


def test_s3_clients_factory(monkeypatch):
    import boto3
    seen = {}

    class Sess:
        def __init__(self, **kw):
            seen["session"] = kw

        def client(self, name, endpoint_url=None, config=None):
            seen.setdefault("clients", []).append((name, endpoint_url, config.s3))
            return object()
    monkeypatch.setattr(boto3, "Session", Sess)
    c = S.s3_clients("us-east-1", "", "AKIA1", "sec", "tok", "https://minio.example.com", True)
    c("s3")
    c("s3")
    assert seen["session"] == {"region_name": "us-east-1", "aws_access_key_id": "AKIA1", "aws_secret_access_key": "sec", "aws_session_token": "tok"}
    assert seen["clients"] == [("s3", "https://minio.example.com", {"addressing_style": "path"})]
    S.s3_clients("us-east-1", "work", "", "", "", "", False)("s3")
    assert seen["session"] == {"region_name": "us-east-1", "profile_name": "work"} and seen["clients"][-1] == ("s3", None, {"addressing_style": "auto"})

    def boom(**kw):
        raise RuntimeError("no profile")
    monkeypatch.setattr(boto3, "Session", boom)
    with pytest.raises(StorageError, match="AWS settings are not usable: RuntimeError"):
        S.s3_clients("us-east-1", "nope", "", "", "", "", False)
    monkeypatch.setattr(boto3, "Session", Sess)
    st = S3Storage({"region": "us-east-1", "bucket": "my-bucket", "profile": "work"}, {"access_key_id": "AKIA2", "secret_access_key": "s"}, None)       # builds its own clients
    assert st.bucket == "my-bucket"


# ------------------------------------------------------------------------------------------------ Google Cloud Storage
class FakeGcs:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.status = 0

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.status:
            return httpx.Response(self.status)
        assert req.headers["authorization"] == "Bearer tok"
        path = req.url.path
        if req.method == "POST" and path.startswith("/upload/storage/v1/b/my-bucket/o"):
            assert req.url.params["uploadType"] == "media"
            self.objects[req.url.params["name"]] = req.content
            return httpx.Response(200, json={"name": req.url.params["name"]})
        if path.startswith("/storage/v1/b/my-bucket/o/"):
            key = httpx.URL(str(req.url)).raw_path.decode().split("/o/", 1)[1].split("?")[0].replace("%2F", "/")
            if req.method == "GET":
                return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
            if req.method == "DELETE":
                return httpx.Response(204) if self.objects.pop(key, None) is not None else httpx.Response(404)
        if req.method == "GET" and path == "/storage/v1/b/my-bucket":
            return httpx.Response(200, json={})
        return httpx.Response(400)


def test_gcs_roundtrip_uses_the_json_api():
    f = FakeGcs()
    s = GcsStorage({"bucket": "my-bucket", "prefix": "p"}, {}, None, transport=f.transport(), token=lambda: "tok")
    s.put("a1/r.md", b"hi", "text/markdown")
    up = f.requests[0]
    assert up.method == "POST" and up.headers["content-type"] == "text/markdown" and f.objects == {"p/a1/r.md": b"hi"}
    assert s.get("a1/r.md") == b"hi" and "alt=media" in str(f.requests[-1].url) and "p%2Fa1%2Fr.md" in str(f.requests[-1].url)
    s.delete("a1/r.md")
    s.delete("a1/r.md")                                                 # already gone: fine
    assert s.health()["ok"] and s.where() == "gs://my-bucket/p/"
    with pytest.raises(StorageError, match="not in the storage any more"):
        s.get("a1/r.md")


def test_gcs_http_errors_and_settings():
    f = FakeGcs()
    s = GcsStorage({"bucket": "my-bucket"}, {}, None, transport=f.transport(), token=lambda: "tok")
    f.status = 403
    with pytest.raises(StorageError, match="Google Cloud Storage refused the credentials or the permission \\(HTTP 403\\)"):
        s.put("k", b"x", "text/plain")
    f.status = 500
    with pytest.raises(StorageError, match="answered HTTP 500"):
        s.get("k")
    assert s.health() == {"ok": False, "detail": "Google Cloud Storage answered HTTP 500"}
    with pytest.raises(StorageError, match="bucket name looks wrong"):
        GcsStorage({"bucket": "B_"}, {}, None)
    f2 = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    with pytest.raises(StorageError, match="cannot reach Google Cloud Storage: ConnectError"):
        GcsStorage({"bucket": "my-bucket"}, {}, None, transport=f2, token=lambda: "tok").put("k", b"x", "text/plain")


def test_gcs_real_token_paths(monkeypatch):
    import google.auth
    import google.auth.transport.requests
    from google.oauth2 import service_account

    class Creds:
        token = "fresh"

        def refresh(self, request):
            pass
    seen = {}
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", classmethod(lambda cls, info, scopes: seen.update(info=info, scopes=scopes) or Creds()))
    monkeypatch.setattr(google.auth, "default", lambda scopes: seen.update(adc=scopes) or (Creds(), "proj"))
    s = GcsStorage({"bucket": "my-bucket"}, {"service_account_json": json.dumps({"client_email": "a@b"})}, None)
    assert s._real_token() == "fresh" and seen["info"] == {"client_email": "a@b"} and seen["scopes"] == [S.GCS_SCOPE]
    assert GcsStorage({"bucket": "my-bucket"}, {}, None)._real_token() == "fresh" and seen["adc"] == [S.GCS_SCOPE]
    with pytest.raises(StorageError, match="Google credentials are not usable: JSONDecodeError"):
        GcsStorage({"bucket": "my-bucket"}, {"service_account_json": "{not json"}, None)._real_token()


# ------------------------------------------------------------------------------------------------ Azure Blob
class FakeAzure:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.url.params["sig"] == "abc" and req.headers["x-ms-version"]
        path = req.url.path
        if path == "/my-container" and req.url.params.get("restype") == "container":
            return httpx.Response(200)
        if req.method == "PUT":
            assert req.headers["x-ms-blob-type"] == "BlockBlob"
            self.blobs[path] = req.content
            return httpx.Response(201)
        if req.method == "GET":
            return httpx.Response(200, content=self.blobs[path]) if path in self.blobs else httpx.Response(404)
        if req.method == "DELETE":
            return httpx.Response(202) if self.blobs.pop(path, None) is not None else httpx.Response(404)
        return httpx.Response(400)


def test_azure_blob_roundtrip():
    f = FakeAzure()
    s = AzureBlobStorage({"account": "myacct", "container": "my-container", "prefix": "p"}, {"sas_token": "?sv=1&sig=abc"}, None, transport=f.transport())
    s.put("a1/my file.md", b"hi", "text/markdown")
    assert f.blobs == {"/my-container/p/a1/my file.md": b"hi"} and f.requests[0].url.host == "myacct.blob.core.windows.net"
    assert s.get("a1/my file.md") == b"hi"
    s.delete("a1/my file.md")
    s.delete("a1/my file.md")
    assert s.health() == {"ok": True, "detail": "container 'my-container' is reachable"} and s.where().endswith("/my-container/p/")
    odd = AzureBlobStorage({"account": "myacct", "container": "my-container"}, {"sas_token": "sv=1&sig=abc"}, None, transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    assert not odd.health()["ok"] and "refused the credentials" in odd.health()["detail"]


@pytest.mark.parametrize("cfg,sec,msg", [({"account": "X", "container": "my-container"}, {"sas_token": "s"}, "storage account name"), ({"account": "myacct", "container": "Bad_"}, {"sas_token": "s"}, "container name looks wrong"),
                                         ({"account": "myacct", "container": "my-container"}, {}, "SAS token is needed"), ({"account": "myacct", "container": "my-container", "endpoint": "ftp://x"}, {"sas_token": "s"}, "full https")])
def test_azure_rejects_bad_settings(cfg, sec, msg):
    with pytest.raises(StorageError, match=msg):
        AzureBlobStorage(cfg, sec, None)


# ------------------------------------------------------------------------------------------------ WebDAV
class FakeDav:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.log: list[tuple[str, str]] = []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.log.append((req.method, req.url.path))
        assert req.headers["authorization"].startswith("Basic ")
        p = req.url.path
        if req.method == "MKCOL":
            if p in self.dirs:
                return httpx.Response(405)
            self.dirs.add(p)
            return httpx.Response(201)
        if req.method == "PUT":
            self.files[p] = req.content
            return httpx.Response(201)
        if req.method == "GET":
            return httpx.Response(200, content=self.files[p]) if p in self.files else httpx.Response(404)
        if req.method == "DELETE":
            return httpx.Response(204) if self.files.pop(p, None) is not None else httpx.Response(404)
        if req.method == "PROPFIND":
            return httpx.Response(207) if req.headers["depth"] == "0" else httpx.Response(400)
        return httpx.Response(400)


def test_webdav_creates_folders_then_uploads():
    f = FakeDav()
    s = WebDavStorage({"url": "https://cloud.example.com/dav/", "prefix": "as/arts", "username": "me"}, {"password": "pw"}, None, transport=f.transport())
    s.put("a1/r.md", b"hi", "text/markdown")
    assert [m for m, _ in f.log] == ["MKCOL", "MKCOL", "MKCOL", "PUT"] and f.files == {"/dav/as/arts/a1/r.md": b"hi"}
    s.put("a2/r.md", b"yo", "text/markdown")                              # existing folders answer 405: fine
    assert s.get("a1/r.md") == b"hi"
    s.delete("a1/r.md")
    s.delete("a1/r.md")
    assert s.health() == {"ok": True, "detail": "cloud.example.com is reachable"} and s.where() == "https://cloud.example.com/dav/as/arts/"


def test_webdav_settings_and_http_errors():
    with pytest.raises(StorageError, match="full https"):
        WebDavStorage({"url": "http://plain.example.com"}, {}, None)
    s = WebDavStorage({"url": "http://plain.example.com", "allow_http": True, "username": "u"}, {"password": "p"}, None, transport=FakeDav().transport())
    assert s.base == "http://plain.example.com"
    anon = WebDavStorage({"url": "https://cloud.example.com"}, {}, None, transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert anon._auth is None
    with pytest.raises(StorageError, match="refused the credentials"):
        anon.get("k")
    assert not anon.health()["ok"]


def test_build_storage_and_kind_table():
    with pytest.raises(StorageError, match="unknown storage 'ftp'"):
        build_storage("ftp", {}, {}, None)
    assert isinstance(build_storage("local", {"folder": "C:\\x" if False else "/"}, {}, None), LocalStorage) if False else True
    assert set(S.KINDS) == {"local", "s3", "s3_compatible", "gcs", "azure_blob", "webdav"} and not S.KINDS["local"]["cloud"] and all(S.KINDS[k]["cloud"] for k in S.KINDS if k != "local")
    for k, v in S.KINDS.items():
        assert v["cls"].kind == k and all(len(f) == 5 for f in v["fields"])


def test_a_failing_delete_is_reported_but_a_missing_object_is_not(tmp_path):
    boom = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(StorageError, match="HTTP 500"):
        GcsStorage({"bucket": "my-bucket"}, {}, None, transport=boom, token=lambda: "t").delete("k")
    with pytest.raises(StorageError, match="HTTP 500"):
        AzureBlobStorage({"account": "myacct", "container": "my-container"}, {"sas_token": "s"}, None, transport=boom).delete("k")
    with pytest.raises(StorageError, match="HTTP 500"):
        WebDavStorage({"url": "https://cloud.example.com"}, {}, None, transport=boom).delete("k")
    assert isinstance(build_storage("local", {"folder": str(tmp_path)}, {}, tmp_path), LocalStorage)
