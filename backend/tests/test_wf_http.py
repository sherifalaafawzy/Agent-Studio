"""The HTTP request box's client: what it refuses (private addresses, odd ports, secrets in plain headers), what it follows (redirects, with care), what it keeps."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.workflows.core import WorkflowError
from app.workflows.http import HttpClient

run = asyncio.run
PUBLIC = lambda host: ["93.184.216.34"]    # noqa: E731


def client(handler=None, allow=(), resolver=PUBLIC):
    return HttpClient(lambda: dict(http_allow_hosts=list(allow)), resolver=resolver, transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200, text="ok"))))


def call(c, method="GET", url="https://api.test/x", query=None, headers=None, body="", cred=None, timeout=5, cap=10_000):
    return run(c.request(method, url, query or {}, headers or {}, body, cred, timeout, cap))


def test_what_is_refused():
    c = client()
    for url, msg in [("ftp://api.test", "only http"), ("https://", "only http"), ("https://u:p@api.test", "user name or password"), ("https://api.test:8443/x", "port 8443 is not allowed"), ("https://api.test:bad/x", "invalid port")]:
        with pytest.raises(WorkflowError, match=msg):
            c.check(url)
    for literal in ("http://127.0.0.1/x", "http://10.1.2.3/x", "http://169.254.169.254/latest", "http://[::1]/x", "http://192.168.0.9/x"):
        with pytest.raises(WorkflowError, match="not a public internet address"):
            c.check(literal)
    with pytest.raises(WorkflowError, match="not a public internet address"):
        client(resolver=lambda h: ["93.184.216.34", "10.0.0.1"]).check("https://mixed.test")                 # every address a name resolves to must be public
    with pytest.raises(WorkflowError, match="could not look up"):
        client(resolver=lambda h: (_ for _ in ()).throw(OSError("no dns"))).check("https://nowhere.test")
    with pytest.raises(WorkflowError, match="did not resolve"):
        client(resolver=lambda h: []).check("https://empty.test")
    assert c.check("https://api.test/x") == ("https", "api.test", "93.184.216.34") and c.check("http://8.8.8.8/x") == ("http", "8.8.8.8", "8.8.8.8") and c.check("https://[2606:4700::1111]/x")[2] == "2606:4700::1111"


def test_an_allow_list_opens_private_hosts_and_ports():
    c = client(allow=["internal.test"], resolver=lambda h: ["10.0.0.5"])
    assert c.check("https://internal.test:8443/x") == ("https", "internal.test", "10.0.0.5") and c.check("https://svc.internal.test/x")[2] == "10.0.0.5"
    with pytest.raises(WorkflowError, match="not a public internet address"):
        c.check("https://other.test/x")


def test_secrets_never_travel_as_plain_headers():
    c = client()
    for h in ("Authorization", "cookie", "Proxy-Authorization"):
        with pytest.raises(WorkflowError, match="credential"):
            call(c, headers={h: "x"})
    for h in ("Host", "Content-Length", "Connection"):
        with pytest.raises(WorkflowError, match="set automatically"):
            call(c, headers={h: "x"})


def test_requests_go_to_the_checked_address_and_come_back_trimmed():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"a": 1}, headers={"x-request-id": "r1", "set-cookie": "s=1", "x-other": "o"})
    c = client(handler)
    res = call(c, url="https://api.test/items?a=1", query={"b": "two words", "c": 3}, headers={"X-A": "b"}, cred=("X-Key", "k"))
    assert res["status"] == 200 and res["ok"] and res["text"] == '{"a":1}' and res["headers"]["x-request-id"] == "r1" and "set-cookie" not in res["headers"] and "x-other" not in res["headers"] and not res["truncated"]
    r = seen[0]
    assert r.url.host == "93.184.216.34" and r.headers["host"] == "api.test" and r.url.params["b"] == "two words" and r.url.params["a"] == "1" and r.headers["x-key"] == "k" and r.headers["x-a"] == "b" and "agentstudio" in r.headers["user-agent"].lower()
    call(c, url="https://api.test:443/x")
    assert seen[-1].headers["host"] == "api.test:443"
    call(c, url="http://[2606:4700::1111]/x")
    assert seen[-1].url.host == "2606:4700::1111"


def test_bodies_and_content_types():
    seen = []
    c = client(lambda req: (seen.append(req), httpx.Response(201, text="made"))[1])
    call(c, "POST", body='{"a": 1}')
    call(c, "PUT", body="plain words")
    call(c, "PATCH", body="[1]", headers={"Content-Type": "application/x-custom"})
    call(c, "GET", body="ignored")
    call(c, "DELETE")
    assert seen[0].headers["content-type"] == "application/json" and seen[0].content == b'{"a": 1}' and seen[1].headers["content-type"].startswith("text/plain") and seen[2].headers["content-type"] == "application/x-custom"
    assert seen[3].content == b"" and seen[4].method == "DELETE" and seen[4].content == b""


def test_redirects_are_followed_with_care():
    seen = []

    def handler(req):
        seen.append((str(req.url), req.headers.get("x-key")))
        if req.url.path == "/hop":
            return httpx.Response(302, headers={"location": "/landed"})
        if req.url.path == "/away":
            return httpx.Response(302, headers={"location": "https://elsewhere.test/in"})
        if req.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        return httpx.Response(200, text="there")
    c = client(handler)
    assert call(c, url="https://api.test/hop")["text"] == "there" and seen[-1][0].endswith("/landed")
    call(c, url="https://api.test/away", cred=("X-Key", "k"))
    assert seen[-2][1] == "k" and seen[-1][1] is None                  # a redirect to another host never carries the credential
    with pytest.raises(WorkflowError, match="too many redirects"):
        call(c, url="https://api.test/loop")
    res = call(c, "POST", url="https://api.test/hop", body="x")
    assert res["status"] == 302 and res["headers"]["location"] == "/landed"                                              # other methods never follow a redirect
    assert call(c, "HEAD", url="https://api.test/hop")["text"] == "there"


def test_size_caps_and_failures():
    c = client(lambda req: httpx.Response(200, text="x" * 5000))
    res = call(c, cap=100)
    assert res["truncated"] is True and len(res["text"]) == 100 and call(c, cap=10_000)["truncated"] is False
    bad = client(lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(WorkflowError, match="the request failed: ConnectError"):
        call(bad)
    assert call(client(lambda req: httpx.Response(404, text="nope")))["ok"] is False
