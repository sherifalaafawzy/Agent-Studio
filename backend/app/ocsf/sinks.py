"""Where OCSF events go: a rotating NDJSON file, an HTTPS endpoint (generic webhook or Splunk HEC), an S3 bucket, or syslog.

Each sink gets batches of finished OCSF events and either sends them or raises SinkError (the service retries, then counts the drop).
Credentials are passed in separately and never appear in an error message. Amazon Security Lake wants Parquet files and a registered custom
source: that is NOT supported here (the S3 sink writes gzip-compressed NDJSON, which many pipelines can read).
"""
from __future__ import annotations

import gzip
import json
import re
import socket
import ssl
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..rag.stores.bedrock_kb import REGION_RE, aws_clients

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9!_.*'()/-]{0,200}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
FORMATS = ("ndjson", "json_array", "splunk_hec")
PROTOCOLS = ("udp", "tcp", "tls")
SAMPLE_NOTE = "Agent Studio OCSF connection test"


class SinkError(Exception):
    """The sink cannot send, or its settings are wrong. Safe to show: it never contains a credential."""


def ndjson(events: list[dict]) -> bytes:
    return ("\n".join(json.dumps(e, separators=(",", ":"), ensure_ascii=False) for e in events) + "\n").encode("utf-8")


class Sink(ABC):
    kind = ""

    @abstractmethod
    def send(self, events: list[dict]) -> None: ...

    @abstractmethod
    def where(self) -> str:
        """A short description of the destination for the UI (no credentials)."""


class FileSink(Sink):
    kind = "file"

    def __init__(self, cfg: dict, secrets: dict, home: Path):
        name = cfg.get("file") or "agent-studio-ocsf.ndjson"
        if not NAME_RE.match(name) or name in (".", ".."):
            raise SinkError("the file name may use letters, digits, . - and _ only (it is saved in Agent Studio's data folder)")
        self.max_bytes = _int(cfg.get("max_mb", 10), 1, 1024, "max size (MB)") * 1024 * 1024
        self.keep = _int(cfg.get("keep", 5), 1, 50, "files to keep")
        self.path = Path(home) / "ocsf" / name

    def where(self) -> str:
        return f"file ocsf/{self.path.name} (keeps {self.keep} files of up to {self.max_bytes // 1024 // 1024} MB)"

    def send(self, events: list[dict]) -> None:
        data = ndjson(events)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size + len(data) > self.max_bytes:
                self._rotate()
            with self.path.open("ab") as f:
                f.write(data)
        except OSError as e:
            raise SinkError(f"cannot write {self.path.name}: {e.strerror or type(e).__name__}") from e

    def _rotate(self) -> None:
        oldest = self.path.with_name(f"{self.path.name}.{self.keep}")
        oldest.unlink(missing_ok=True)
        for i in range(self.keep - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            if src.exists():
                src.replace(self.path.with_name(f"{self.path.name}.{i + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))


class HttpsSink(Sink):
    kind = "https"

    def __init__(self, cfg: dict, secrets: dict, home: Path, transport: httpx.BaseTransport | None = None):
        url = (cfg.get("url") or "").strip()
        parts, host = None, ""
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
        except ValueError:
            host = ""
        if not (parts and parts.scheme == "https" and host) and not (parts and parts.scheme == "http" and host in ("127.0.0.1", "localhost", "::1")):
            raise SinkError("the address must be https:// (plain http is allowed only for localhost)")
        self.url, self.format = url, cfg.get("format") or "ndjson"
        if self.format not in FORMATS:
            raise SinkError(f"format must be one of: {', '.join(FORMATS)}")
        token = secrets.get("token", "")
        scheme = "Splunk" if self.format == "splunk_hec" else "Bearer"
        self._headers = {"Authorization": f"{scheme} {token}"} if token else {}
        self._transport = transport
        self._host = host

    def where(self) -> str:
        return f"{self.format} to https://{self._host}"

    def _body(self, events: list[dict]) -> tuple[bytes, str]:
        if self.format == "json_array":
            return json.dumps(events, separators=(",", ":")).encode(), "application/json"
        if self.format == "splunk_hec":
            return b"".join(json.dumps({"time": e["time"] / 1000, "sourcetype": "ocsf", "source": "agent-studio", "event": e}).encode() for e in events), "application/json"
        return ndjson(events), "application/x-ndjson"

    def send(self, events: list[dict]) -> None:
        body, ctype = self._body(events)
        try:
            with httpx.Client(transport=self._transport, timeout=15, headers={"Content-Type": ctype, **self._headers}) as c:
                r = c.post(self.url, content=body)
        except httpx.HTTPError as e:
            raise SinkError(f"cannot reach {self._host}: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise SinkError(f"{self._host} rejected the credentials (HTTP {r.status_code})")
        if r.status_code >= 300:
            raise SinkError(f"{self._host} answered HTTP {r.status_code}")


class S3Sink(Sink):
    kind = "s3"

    def __init__(self, cfg: dict, secrets: dict, home: Path, clients=None):
        if not REGION_RE.match(cfg.get("region") or ""):
            raise SinkError("the AWS region must look like us-east-1")
        if not BUCKET_RE.match(cfg.get("bucket") or ""):
            raise SinkError("the S3 bucket name looks wrong")
        prefix = cfg.get("prefix") if cfg.get("prefix") is not None else "agent-studio/ocsf/"
        if not PREFIX_RE.match(prefix):
            raise SinkError("the S3 prefix contains characters S3 keys should not use")
        self.region, self.bucket = cfg["region"], cfg["bucket"]
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"
        self._clients = clients or aws_clients(self.region, cfg.get("profile", ""), secrets.get("access_key_id", ""), secrets.get("secret_access_key", ""), secrets.get("session_token", ""))

    def where(self) -> str:
        return f"s3://{self.bucket}/{self.prefix} (gzip NDJSON, one object per batch)"

    def send(self, events: list[dict]) -> None:
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
        now = time.gmtime()
        key = f"{self.prefix}{time.strftime('%Y/%m/%d', now)}/{time.strftime('%H%M%S', now)}-{uuid.uuid4().hex[:8]}.ndjson.gz"
        try:
            self._clients("s3").put_object(Bucket=self.bucket, Key=key, Body=gzip.compress(ndjson(events)), ContentType="application/x-ndjson", ContentEncoding="gzip")
        except ClientError as e:
            raise SinkError(f"AWS refused the upload ({e.response.get('Error', {}).get('Code', 'error')})") from e
        except NoCredentialsError as e:
            raise SinkError("no AWS credentials found: set keys, a profile, or sign in with the AWS CLI") from e
        except BotoCoreError as e:
            raise SinkError(f"cannot reach S3 in {self.region}: {type(e).__name__}") from e


class SyslogSink(Sink):
    kind = "syslog"

    def __init__(self, cfg: dict, secrets: dict, home: Path, connect=None):
        host = cfg.get("host") or ""
        if not HOST_RE.match(host):
            raise SinkError("give the syslog server's host name or address")
        self.host, self.port = host, _int(cfg.get("port", 514), 1, 65535, "port")
        self.protocol = cfg.get("protocol") or "udp"
        if self.protocol not in PROTOCOLS:
            raise SinkError(f"protocol must be one of: {', '.join(PROTOCOLS)}")
        self._connect = connect or self._real_connect
        self._name = socket.gethostname()

    def where(self) -> str:
        return f"syslog {self.protocol}://{self.host}:{self.port}" + (" (not encrypted)" if self.protocol != "tls" else "")

    def _real_connect(self):
        if self.protocol == "udp":
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw = socket.create_connection((self.host, self.port), timeout=10)
        return ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.protocol == "tls" else raw

    def send(self, events: list[dict]) -> None:
        sev = {1: 6, 2: 5, 3: 4, 4: 3, 5: 2, 6: 1}
        lines = []
        for e in events:
            pri = 13 * 8 + sev.get(e.get("severity_id", 1), 6)               # facility 13 (log audit)
            lines.append(f"<{pri}>1 {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e['time'] / 1000))} {self._name} agent-studio - {e['class_uid']} - "
                         f"{json.dumps(e, separators=(',', ':'))}")
        try:
            s = self._connect()
            try:
                if self.protocol == "udp":
                    for line in lines:
                        s.sendto(line.encode()[:60000], (self.host, self.port))
                else:
                    s.sendall(("\n".join(lines) + "\n").encode())
            finally:
                s.close()
        except (OSError, ssl.SSLError) as e:
            raise SinkError(f"cannot send to {self.host}:{self.port}: {type(e).__name__}") from e


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
        raise SinkError(f"{what} must be a whole number between {lo} and {hi}")
    return int(v)


KINDS = {
    "file": dict(label="Rotating file", cls=FileSink, fields=[("file", "File name", False, False, "agent-studio-ocsf.ndjson"), ("max_mb", "Rotate at (MB)", False, False, "10"), ("keep", "Files to keep", False, False, "5")],
                 note="Saved in Agent Studio's data folder under ocsf/. One JSON event per line (NDJSON)."),
    "https": dict(label="HTTPS webhook or Splunk HEC", cls=HttpsSink, fields=[("url", "URL", False, True, "https://siem.example.com/events"), ("format", "Format", False, False, "ndjson"),
                                                                              ("token", "Token", True, False, "")],
                  note="Formats: ndjson, json_array, splunk_hec. The token is sent as a bearer token (Splunk HEC: 'Splunk <token>')."),
    "s3": dict(label="Amazon S3 bucket", cls=S3Sink, fields=[("region", "AWS region", False, True, "us-east-1"), ("bucket", "Bucket", False, True, ""), ("prefix", "Prefix", False, False, "agent-studio/ocsf/"),
                                                              ("profile", "AWS profile", False, False, ""), ("access_key_id", "Access key id", True, False, ""),
                                                              ("secret_access_key", "Secret access key", True, False, ""), ("session_token", "Session token", True, False, "")],
              note="Writes gzip-compressed NDJSON. Amazon Security Lake needs Parquet and a registered custom source: not supported here. Leave credentials empty to use the default AWS credential chain."),
    "syslog": dict(label="Syslog", cls=SyslogSink, fields=[("host", "Host", False, True, ""), ("port", "Port", False, False, "514"), ("protocol", "Protocol", False, False, "udp")],
                   note="RFC 5424 messages with the OCSF JSON as the message. UDP and TCP are not encrypted: use TLS for anything that leaves your network."),
}


def build_sink(kind: str, cfg: dict, secrets: dict, home: Path, **kw) -> Sink:
    if kind not in KINDS:
        raise SinkError(f"unknown sink '{kind}' (use one of: {', '.join(KINDS)})")
    return KINDS[kind]["cls"](cfg, secrets, home, **kw)
