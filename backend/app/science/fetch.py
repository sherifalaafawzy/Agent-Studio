"""One careful HTTP client for every science data source: a limit per host, a short cache, retries that obey Retry-After, a size cap and an identifying User-Agent.

Read-only: only GET. The clock, the sleep and the transport are injectable so tests never touch the network or wait.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

USER_AGENT = "AgentStudio-ScienceLab/1.0 (local research tool; read-only)"
MAX_BYTES = 2_000_000
MAX_RETRY_WAIT = 30.0
TIMEOUT = 25.0


class SciError(Exception):
    """A data source could not answer. The message is plain and safe to show."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind                       # error | rate_limited | needs_key | not_found | unreachable


@dataclass
class Reply:
    text: str
    status: int
    url: str
    retrieved_at: float
    cached: bool = False
    etag: str = ""

    def json(self):
        import json
        try:
            return json.loads(self.text)
        except ValueError as e:
            raise SciError("the data source sent something that is not JSON", "error") from e


class Fetcher:
    def __init__(self, transport=None, clock=time.monotonic, sleep=time.sleep, now=time.time, retries: int = 2):
        self.transport, self.clock, self.sleep, self.now, self.retries = transport, clock, sleep, now, retries
        self._next: dict[str, float] = {}
        self._cache: dict[tuple, Reply] = {}
        self._lock = threading.Lock()
        self.calls = 0

    def _wait(self, host: str, interval: float) -> None:
        with self._lock:
            t = self.clock()
            at = max(t, self._next.get(host, 0.0))
            self._next[host] = at + interval
        if at > t:
            self.sleep(at - t)

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, interval: float = 0.34, ttl: float = 3600.0, cache: bool = True) -> Reply:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = (url, tuple(sorted((str(k), str(v)) for k, v in params.items())))
        if cache:
            hit = self._cache.get(key)
            if hit is not None and self.now() - hit.retrieved_at < ttl:
                return Reply(hit.text, hit.status, hit.url, hit.retrieved_at, True, hit.etag)
        host = httpx.URL(url).host
        last = ""
        for attempt in range(self.retries + 1):
            self._wait(host, interval)
            self.calls += 1
            try:
                with httpx.Client(transport=self.transport, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT, **(headers or {})}) as c:
                    r = c.get(url, params=params)
            except httpx.HTTPError as e:
                raise SciError(f"could not reach {host} ({type(e).__name__})", "unreachable") from e
            if r.status_code in (429, 503) and attempt < self.retries:
                try:
                    wait = float(r.headers.get("retry-after", "") or 0)
                except ValueError:
                    wait = 0.0
                self.sleep(min(max(wait, interval * 2 ** (attempt + 1)), MAX_RETRY_WAIT))
                last = f"{host} asked us to slow down"
                continue
            if r.status_code in (429, 503):
                raise SciError(f"{host} is rate-limiting requests right now; try again in a minute", "rate_limited")
            if r.status_code in (401, 403):
                raise SciError(f"{host} refused the request (a key may be missing or wrong)", "needs_key")
            if r.status_code == 404:
                raise SciError("nothing found for that identifier", "not_found")
            if r.status_code >= 400:
                raise SciError(f"{host} answered with an error ({r.status_code})", "error")
            if len(r.content) > MAX_BYTES:
                raise SciError(f"the answer from {host} is larger than {MAX_BYTES // 1_000_000} MB, so it was not used", "error")
            rep = Reply(r.text, r.status_code, str(r.url), self.now(), False, r.headers.get("etag", ""))
            if cache:
                self._cache[key] = rep
                if len(self._cache) > 500:
                    self._cache.pop(next(iter(self._cache)))
            return rep
        raise SciError(last or "the data source did not answer", "rate_limited")      # pragma: no cover - the loop always returns or raises above
