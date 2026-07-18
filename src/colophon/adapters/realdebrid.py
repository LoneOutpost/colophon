"""Real-Debrid REST client (ported from the rdtui Go project's realdebrid client).

Only the read + unrestrict endpoints needed for acquisition are ported here.
"""

from __future__ import annotations

import logging
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


# Retry transport errors + RD 429/5xx up to 5 attempts; reraise the last error.
RD_RETRY = retry(
    stop=stop_after_attempt(5),
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
    async def torrent_info(self, torrent_id: str) -> RdTorrentInfo: ...
    async def unrestrict_link(self, link: str) -> RdUnrestrictedLink: ...
    async def add_magnet(self, magnet: str) -> str: ...
    async def add_torrent_file(self, content: bytes) -> str: ...
    async def select_files(self, torrent_id: str, file_ids: str) -> None: ...


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
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        retry_after: float | None = None
        raw = resp.headers.get("Retry-After")
        if raw is not None:
            try:
                retry_after = float(raw)
            except ValueError:
                retry_after = None  # HTTP-date form: fall back to exponential backoff
        raise RealDebridError(resp.status_code, resp.text[:200], retry_after=retry_after)

    @RD_RETRY
    async def _request(
        self, method: str, path: str, *,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        resp = await self._client.request(
            method, f"{self._base}{path}", params=params, data=data,
            content=content, headers=self._headers,
        )
        self._raise_for_status(resp)  # raises inside the retried scope so 429/5xx retry
        return resp

    async def user(self) -> RdUser:
        resp = await self._request("GET", "/user")
        return RdUser.model_validate(resp.json())

    async def list_torrents(self, limit: int = 100) -> list[RdTorrent]:
        resp = await self._request("GET", "/torrents", params={"limit": limit})
        return [RdTorrent.model_validate(x) for x in (resp.json() or [])]

    async def torrent_info(self, torrent_id: str) -> RdTorrentInfo:
        resp = await self._request("GET", f"/torrents/info/{torrent_id}")
        return RdTorrentInfo.model_validate(resp.json())

    async def unrestrict_link(self, link: str) -> RdUnrestrictedLink:
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
