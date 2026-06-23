"""Real-Debrid REST client (ported from the rdtui Go project's realdebrid client).

Only the read + unrestrict endpoints needed for acquisition are ported here.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from colophon.adapters.http import HTTP_RETRY
from colophon.core.models import _Base

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.real-debrid.com/rest/1.0"



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

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Real-Debrid API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


@runtime_checkable
class RealDebridSource(Protocol):
    async def user(self) -> RdUser: ...
    async def list_torrents(self, limit: int = 100) -> list[RdTorrent]: ...
    async def torrent_info(self, torrent_id: str) -> RdTorrentInfo: ...
    async def unrestrict_link(self, link: str) -> RdUnrestrictedLink: ...


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

    @HTTP_RETRY
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return await self._client.get(f"{self._base}{path}", params=params, headers=self._headers)

    @HTTP_RETRY
    async def _post(self, path: str, data: dict[str, str]) -> httpx.Response:
        return await self._client.post(f"{self._base}{path}", data=data, headers=self._headers)

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise RealDebridError(resp.status_code, resp.text[:200])

    async def user(self) -> RdUser:
        resp = await self._get("/user")
        self._raise_for_status(resp)
        return RdUser.model_validate(resp.json())

    async def list_torrents(self, limit: int = 100) -> list[RdTorrent]:
        resp = await self._get("/torrents", params={"limit": limit})
        self._raise_for_status(resp)
        return [RdTorrent.model_validate(x) for x in (resp.json() or [])]

    async def torrent_info(self, torrent_id: str) -> RdTorrentInfo:
        resp = await self._get(f"/torrents/info/{torrent_id}")
        self._raise_for_status(resp)
        return RdTorrentInfo.model_validate(resp.json())

    async def unrestrict_link(self, link: str) -> RdUnrestrictedLink:
        resp = await self._post("/unrestrict/link", {"link": link})
        self._raise_for_status(resp)
        return RdUnrestrictedLink.model_validate(resp.json())

    async def aclose(self) -> None:
        """Close the underlying httpx client. Closes whatever client this adapter
        holds, so a caller injecting a shared client should manage that client's
        lifecycle itself rather than calling this."""
        await self._client.aclose()
