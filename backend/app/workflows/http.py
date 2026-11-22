"""The HTTP request box: one web call, made safely.

Safe means: only http/https; no user name or password in the address; the host is looked up first and every address it resolves to must be a public one (no loopback, private,
link-local or cloud-metadata ranges) unless the host is on the allow-list in the workflow settings; the connection goes to that exact address, so a name cannot change its mind
between the check and the call; standard ports only (80, 443) unless the host is on the allow-list; GET and HEAD may follow up to four redirects (each one re-checked), other
methods never follow one; the body read is capped; and secrets travel only as a named credential, never as plain headers.
"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from ..harness.extras import default_resolver, is_public
from .core import WorkflowError

UA = "AgentStudio/1.0 (+workflow http request)"
MAX_REDIRECTS = 4
SECRET_HEADERS = ("authorization", "proxy-authorization", "cookie", "set-cookie")
KEEP_HEADERS = ("content-type", "content-length", "location", "retry-after", "etag", "last-modified", "x-request-id")
FORBIDDEN = ("host", "content-length", "transfer-encoding", "connection", "upgrade")


class HttpClient:
    def __init__(self, config: Callable[[], dict], resolver: Callable[[str], list[str]] | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.config, self.resolver, self.transport = config, resolver or default_resolver, transport

    def _allowed(self, host: str) -> bool:
        return any(host == a or host.endswith("." + a) for a in self.config().get("http_allow_hosts", []))

    def check(self, url: str) -> tuple[str, str, str]:
        """(scheme, host, ip) for an address that may be called; raises WorkflowError."""
        u = urlsplit(url.strip())
        if u.scheme not in ("http", "https") or not u.hostname:
            raise WorkflowError("only http:// and https:// addresses can be called")
        if u.username or u.password:
            raise WorkflowError("addresses with a user name or password are not allowed: use a credential")
        host = u.hostname.lower()
        listed = self._allowed(host)
        try:
            port = u.port
        except ValueError:
            raise WorkflowError("that address has an invalid port")
        if port not in (None, 80, 443) and not listed:
            raise WorkflowError(f"port {port} is not allowed: only 80 and 443, unless {host} is allowed in the workflow settings")
        literal = re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host) or (":" in host and re.fullmatch(r"[0-9a-f:.]+", host))
        try:
            ips = [host] if literal else self.resolver(host)
        except OSError as e:
            raise WorkflowError(f"could not look up {host}: {e}")
        if not ips:
            raise WorkflowError(f"{host} did not resolve to an address")
        if not listed and not all(is_public(i) for i in ips):
            raise WorkflowError(f"{host} is not a public internet address: allow it in the workflow settings if you mean to call it")
        return u.scheme, host, ips[0]

    @staticmethod
    def _headers(headers: dict) -> dict:
        out = {}
        for k, v in headers.items():
            low = str(k).lower()
            if low in SECRET_HEADERS:
                raise WorkflowError(f"put '{k}' in a credential (workflow settings), not in the box: the box is saved with the workflow")
            if low in FORBIDDEN:
                raise WorkflowError(f"the header '{k}' is set automatically")
            out[str(k)] = str(v)
        return out

    async def request(self, method: str, url: str, query: dict, headers: dict, body: str, cred: tuple[str, str] | None, timeout_s: float, max_bytes: int) -> dict:
        method = method.upper()
        sent = {"User-Agent": UA, "Accept": "application/json, text/plain;q=0.9, */*;q=0.5", **self._headers(headers)}
        if cred:
            sent[cred[0]] = cred[1]
        if query:
            url += ("&" if "?" in url else "?") + urlencode({k: str(v) for k, v in query.items()})
        content = body.encode() if body and method not in ("GET", "HEAD") else None
        if content is not None and not any(k.lower() == "content-type" for k in sent):
            sent["Content-Type"] = "application/json" if body.lstrip()[:1] in "{[" else "text/plain; charset=utf-8"
        follow = method in ("GET", "HEAD")
        for _ in range(MAX_REDIRECTS + 1):
            scheme, host, ip = self.check(url)
            u = urlsplit(url)
            target = u._replace(netloc=(f"[{ip}]" if ":" in ip else ip) + (f":{u.port}" if u.port else "")).geturl()
            hdrs = {**sent, "Host": host + (f":{u.port}" if u.port else "")}
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 10)), transport=self.transport, follow_redirects=False, verify=True, trust_env=False) as c:
                    req = c.build_request(method, target, headers=hdrs, content=content, extensions={"sni_hostname": host})
                    resp = await c.send(req, stream=True)
                    try:
                        if follow and resp.is_redirect and resp.headers.get("location"):
                            url = urljoin(url, resp.headers["location"])
                            if cred and urlsplit(url).hostname != host:
                                sent.pop(cred[0], None)                                      # a redirect to another host never carries the credential
                            continue
                        data, truncated = b"", False
                        async for chunk in resp.aiter_bytes():
                            data += chunk
                            if len(data) > max_bytes:
                                data, truncated = data[:max_bytes], True
                                break
                        text = data.decode(resp.encoding or "utf-8", errors="replace")
                        return dict(status=resp.status_code, ok=resp.status_code < 400, headers={k: v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS}, text=text, truncated=truncated)
                    finally:
                        await resp.aclose()
            except httpx.HTTPError as e:
                raise WorkflowError(f"the request failed: {type(e).__name__}")
        raise WorkflowError("too many redirects")
