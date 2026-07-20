"""Real-Debrid REST client (ported from the rdtui Go project's realdebrid client).

Only the read + unrestrict endpoints needed for acquisition are ported here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from colophon.core.models import _Base

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.real-debrid.com/rest/1.0"

# Real-Debrid's front rate limiter 429s on sustained request rates, and once tripped it stays
# tripped for a cool-down window, so a fixed spacing that's even slightly too fast loses a large
# fraction of a big batch. We PROACTIVELY pace every request AND adapt the rate the way TCP does:
# multiplicatively back off the moment we see a 429 (and honor its Retry-After as a fleet-wide
# pause), then additively ease back toward the floor as requests succeed. That lets the rate
# settle at whatever RD is actually willing to serve instead of hammering at a constant rate.
_RD_MIN_INTERVAL = 0.35   # floor: ~3/sec when RD is happy
_RD_MAX_INTERVAL = 8.0    # ceiling: never crawl slower than this per request
_RD_BACKOFF_FACTOR = 2.0  # multiply the interval on each 429
_RD_RECOVERY_STEP = 0.15  # shave this off the interval per success (slow, so we don't re-trip)


class _Pacer:
    """A shared, self-adapting leaky-bucket pacer. Each `wait()` returns no sooner than the
    previous one plus the CURRENT `interval`, so concurrent callers issue requests spaced out
    instead of in a burst. The interval is not fixed: `on_throttle()` multiplies it (up to
    `max_interval`) and records any Retry-After as a fleet-wide pause; `on_success()` eases it
    back down toward `min_interval`. Only the spacing bookkeeping is locked; the interval math is
    plain (asyncio is single-threaded, so the read-modify-writes are atomic). `now`/`sleep` are
    injectable for deterministic tests."""

    def __init__(
        self, min_interval: float, *,
        max_interval: float = _RD_MAX_INTERVAL,
        backoff: float = _RD_BACKOFF_FACTOR,
        recovery: float = _RD_RECOVERY_STEP,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.backoff = backoff
        self.recovery = recovery
        self.interval = min_interval  # current spacing; grows on throttle, shrinks on success
        self._now = now
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_at = 0.0      # monotonic time the next request may start
        self._pause_until = 0.0  # a Retry-After cool-down that holds the whole fleet back

    async def wait(self) -> None:
        async with self._lock:
            now = self._now()
            start_at = max(now, self._next_at, self._pause_until)
            self._next_at = start_at + self.interval
            delay = start_at - now
        if delay > 0:
            await self._sleep(delay)

    def on_throttle(self, retry_after: float | None = None) -> None:
        """A 429 landed: slow the shared rate and, if RD gave a Retry-After, pause the fleet."""
        self.interval = min(self.max_interval, self.interval * self.backoff)
        if retry_after is not None:
            pause = self._now() + min(retry_after, _RETRY_AFTER_CAP)
            self._pause_until = max(self._pause_until, pause)

    def on_success(self) -> None:
        """A request got through: ease the rate back toward the floor a little."""
        self.interval = max(self.min_interval, self.interval - self.recovery)


# Process-wide pacer shared across every RealDebridClient instance (a fresh client is built per
# download), so total RD request rate stays bounded no matter how many downloads/workers run.
_RD_PACER = _Pacer(_RD_MIN_INTERVAL)

# RD statuses worth retrying: rate-limit (429) and transient server/hoster errors (5xx).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_RETRY_AFTER_CAP = 60.0  # never sleep longer than this even if the server asks for more
_RD_BACKOFF = wait_exponential(min=0.5, max=8)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, RealDebridError) and exc.status_code in _RETRYABLE_STATUS


def _retry_wait(retry_state: Any) -> float:
    """Honor a server-provided Retry-After when present (capped), else exponential backoff."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RealDebridError) and exc.retry_after is not None:
        return min(exc.retry_after, _RETRY_AFTER_CAP)
    return _RD_BACKOFF(retry_state)


# Retry transport errors + RD 429/5xx up to 8 attempts; reraise the last error. The extra
# attempts (over the old 5) give a link a better chance to outlast a throttle cool-down window
# before it's recorded as a retryable failure.
RD_RETRY = retry(
    stop=stop_after_attempt(8),
    wait=_retry_wait,
    retry=retry_if_exception(_is_retryable),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


class RdTorrent(_Base):
    id: str
    filename: str = ""
    bytes: int = 0
    status: str = ""
    progress: float = 0.0
    links: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance


class RdTorrentFile(_Base):
    id: int
    path: str = ""
    bytes: int = 0
    selected: bool = False


class RdTorrentInfo(RdTorrent):
    files: list[RdTorrentFile] = []  # noqa: RUF012 - pydantic field default, copied per instance


class RdUnrestrictedLink(_Base):
    filename: str = ""
    filesize: int = 0
    mime_type: str | None = None
    download: str = ""


class RdUser(_Base):
    id: int
    username: str = ""
    premium: int = 0
    expiration: str | None = None


class RealDebridError(Exception):
    """A 4xx/5xx response from the Real-Debrid API."""

    def __init__(self, status_code: int, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(f"Real-Debrid API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after


@runtime_checkable
class RealDebridSource(Protocol):
    async def user(self) -> RdUser: ...
    async def list_torrents(self, limit: int = 100) -> list[RdTorrent]: ...
    async def torrent_info(self, torrent_id: str, *, force: bool = False) -> RdTorrentInfo: ...
    async def unrestrict_link(self, link: str, *, force: bool = False) -> RdUnrestrictedLink: ...
    async def add_magnet(self, magnet: str) -> str: ...
    async def add_torrent_file(self, content: bytes) -> str: ...
    async def select_files(self, torrent_id: str, file_ids: str) -> None: ...
    async def aclose(self) -> None: ...


class RealDebridClient:
    name = "realdebrid"

    def __init__(
        self, token: str, *, client: httpx.AsyncClient | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float | None:
        """The Retry-After header as seconds, or None (absent, or an HTTP-date we don't parse)."""
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None  # HTTP-date form: fall back to exponential backoff

    @classmethod
    def _raise_for_status(cls, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        raise RealDebridError(
            resp.status_code, resp.text[:200], retry_after=cls._parse_retry_after(resp))

    @RD_RETRY
    async def _request(
        self, method: str, path: str, *,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        await _RD_PACER.wait()  # proactively pace to stay under RD's adaptive rate limit
        resp = await self._client.request(
            method, f"{self._base}{path}", params=params, data=data,
            content=content, headers=self._headers,
        )
        # Feed the outcome back so the shared pacer adapts: a 429 backs the whole fleet off (and
        # honors its Retry-After); any success eases the rate back toward the floor.
        if resp.status_code == 429:
            _RD_PACER.on_throttle(self._parse_retry_after(resp))
        elif resp.is_success:
            _RD_PACER.on_success()
        self._raise_for_status(resp)  # raises inside the retried scope so 429/5xx retry
        return resp

    async def user(self) -> RdUser:
        resp = await self._request("GET", "/user")
        return RdUser.model_validate(resp.json())

    async def list_torrents(self, limit: int = 100) -> list[RdTorrent]:
        resp = await self._request("GET", "/torrents", params={"limit": limit})
        return [RdTorrent.model_validate(x) for x in (resp.json() or [])]

    async def torrent_info(self, torrent_id: str, *, force: bool = False) -> RdTorrentInfo:
        # force: accepted for Protocol parity; this client always fetches live.
        resp = await self._request("GET", f"/torrents/info/{torrent_id}")
        return RdTorrentInfo.model_validate(resp.json())

    async def unrestrict_link(self, link: str, *, force: bool = False) -> RdUnrestrictedLink:
        # force: accepted for Protocol parity; this client always fetches live.
        resp = await self._request("POST", "/unrestrict/link", data={"link": link})
        return RdUnrestrictedLink.model_validate(resp.json())

    async def add_magnet(self, magnet: str) -> str:
        resp = await self._request("POST", "/torrents/addMagnet", data={"magnet": magnet})
        return str((resp.json() or {}).get("id", ""))

    async def add_torrent_file(self, content: bytes) -> str:
        """Upload a raw .torrent file's bytes; returns the new torrent id."""
        resp = await self._request("PUT", "/torrents/addTorrent", content=content)
        return str((resp.json() or {}).get("id", ""))

    async def select_files(self, torrent_id: str, file_ids: str) -> None:
        await self._request("POST", f"/torrents/selectFiles/{torrent_id}", data={"files": file_ids})

    async def aclose(self) -> None:
        """Close the underlying httpx client. Closes whatever client this adapter
        holds, so a caller injecting a shared client should manage that client's
        lifecycle itself rather than calling this."""
        await self._client.aclose()
