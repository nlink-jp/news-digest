"""The single place this tool talks to the network.

Collectors parse bytes; they never fetch. Keeping every request here means the
politeness rules — conditional GET, a size cap, backoff, the refusal to follow
a redirect out of HTTP — hold for every source type, and adding a collector
cannot route around them.

Standard library only.
"""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Callable, Mapping

DEFAULT_USER_AGENT = "news-digest/0.1 (+https://github.com/nlink-jp/news-digest)"

# A feed larger than this is not a feed. The cap also bounds the damage a
# hostile source can do by streaming forever.
MAX_BYTES = 16 * 1024 * 1024

DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2

# Transient by nature: worth one more try. Everything else is answered.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

NOT_MODIFIED = 304


class HttpError(Exception):
    """A request that did not produce a body.

    `kind` is a stable identifier a caller can record in per-source state and
    a digest can report without re-deriving it from a message.
    """

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        """HTTP 304. Distinct from an empty feed: the source has not changed,
        which is a normal outcome and not an anomaly."""
        return self.status == NOT_MODIFIED


class _NoRedirectOutOfHttp(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but only to http(s).

    A feed that redirects to `file:` or `ftp:` is either broken or hostile,
    and urllib will happily open both.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme not in ("http", "https"):
            raise HttpError("bad_redirect", f"refusing redirect to {scheme}: {newurl}", code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode_body(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower().strip()
    try:
        if encoding == "gzip":
            return gzip.decompress(raw)
        if encoding in ("deflate", "zlib"):
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error) as exc:
        raise HttpError("bad_encoding", f"cannot decode {encoding} body: {exc}") from exc
    return raw


class HttpClient:
    """Fetches one document, politely.

    `sleep` and `opener` are injected so the retry path is testable without
    waiting and without a network.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        max_bytes: int = MAX_BYTES,
        retries: int = DEFAULT_RETRIES,
        backoff_base: float = 1.0,
        opener: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.retries = retries
        self.backoff_base = backoff_base
        self.sleep = sleep
        self.opener = opener or urllib.request.build_opener(_NoRedirectOutOfHttp())

    # --- public ---

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        accept_language: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        """GET `url`, revalidating when the caller holds a validator.

        Returns a `Response` whose `not_modified` is true for 304. Raises
        `HttpError` when no body was produced; the caller decides whether one
        failing source stops the run (it does not).
        """
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise HttpError("bad_scheme", f"refusing to fetch a {scheme or 'schemeless'} URL: {url}")

        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                      "application/json, text/xml, text/html;q=0.8, */*;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        }
        if accept_language:
            request_headers["Accept-Language"] = accept_language
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified
        if headers:
            request_headers.update(headers)

        last: HttpError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._attempt(url, request_headers)
            except HttpError as exc:
                last = exc
                if not self._should_retry(exc) or attempt == self.retries:
                    raise
                self.sleep(self._backoff(attempt, exc))
        raise last  # unreachable, but keeps the contract explicit

    # --- internals ---

    def _attempt(self, url: str, headers: dict[str, str]) -> Response:
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return self._read(url, response)
        except urllib.error.HTTPError as exc:
            if exc.code == NOT_MODIFIED:
                return Response(url=url, status=NOT_MODIFIED, body=b"")
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            error = HttpError("http_status", f"HTTP {exc.code} for {url}", exc.code)
            error.retry_after = _parse_retry_after(retry_after)  # type: ignore[attr-defined]
            raise error from exc
        except HttpError:
            raise
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            kind = "timeout" if "timed out" in str(reason).lower() else "unreachable"
            raise HttpError(kind, f"{reason} for {url}") from exc
        except (OSError, ValueError) as exc:
            raise HttpError("transport", f"{exc} for {url}") from exc

    def _read(self, url: str, response) -> Response:
        # Read one byte past the cap: a body that fills it exactly is fine, a
        # body that exceeds it is refused rather than truncated, because a
        # truncated feed parses into a plausible but wrong set of articles.
        raw = response.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise HttpError("too_large", f"body exceeds {self.max_bytes} bytes for {url}")
        info = response.headers
        body = _decode_body(raw, info.get("Content-Encoding", ""))
        return Response(
            url=response.geturl() or url,
            status=getattr(response, "status", 200) or 200,
            body=body,
            etag=info.get("ETag"),
            last_modified=info.get("Last-Modified"),
        )

    def _should_retry(self, exc: HttpError) -> bool:
        if exc.kind in ("timeout", "unreachable", "transport"):
            return True
        return exc.kind == "http_status" and exc.status in RETRY_STATUSES

    def _backoff(self, attempt: int, exc: HttpError) -> float:
        """Exponential, but a server that stated a delay is obeyed.

        Ignoring Retry-After on a 429 is how a polite client becomes the
        reason a feed blocks it.
        """
        stated = getattr(exc, "retry_after", None)
        if stated is not None:
            return min(float(stated), 120.0)
        return self.backoff_base * (2 ** attempt)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        # The HTTP-date form. Treating it as "wait the default" is honest:
        # this client does not have a trustworthy clock offset to the server.
        return None
