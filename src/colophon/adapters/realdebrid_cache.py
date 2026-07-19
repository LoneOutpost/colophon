"""A Real-Debrid source that persists responses to take load off the API.

Wraps an inner `RealDebridSource` plus an `RdCacheRepo`: completed-torrent info and
per-link unrestrict results are cached and served without an API call. Freshness is
lazy — pass `force=True` to bypass, and `list_torrents` prunes torrents no longer on RD.
In-progress torrents are never cached (their file lists still change)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from colophon.adapters.realdebrid import (
    RdTorrent,
    RdTorrentInfo,
    RdUnrestrictedLink,
    RdUser,
    RealDebridSource,
)
from colophon.adapters.repository import RdCacheRepo

logger = logging.getLogger(__name__)

# Statuses whose file list + links are final, so their torrent_info is safe to cache.
_READY_STATUSES = frozenset({"downloaded", "uploading"})


@dataclass
class CachingRealDebridSource:
    inner: RealDebridSource
    cache: RdCacheRepo

    async def user(self) -> RdUser:
        return await self.inner.user()

    async def list_torrents(self, limit: int = 100) -> list[RdTorrent]:
        torrents = await self.inner.list_torrents(limit)
        self.cache.evict_torrents({t.id for t in torrents})
        return torrents

    async def torrent_info(self, torrent_id: str, *, force: bool = False) -> RdTorrentInfo:
        if not force:
            cached = self.cache.get_torrent_info(torrent_id)
            if cached is not None:
                return cached
        info = await self.inner.torrent_info(torrent_id, force=force)
        if info.status in _READY_STATUSES:
            self.cache.put_torrent_info(info)
        return info

    async def unrestrict_link(self, link: str, *, force: bool = False) -> RdUnrestrictedLink:
        if not force:
            cached = self.cache.get_link(link)
            if cached is not None:
                return cached
        unr = await self.inner.unrestrict_link(link)
        self.cache.put_link(link, unr)
        return unr

    async def add_magnet(self, magnet: str) -> str:
        return await self.inner.add_magnet(magnet)

    async def add_torrent_file(self, content: bytes) -> str:
        return await self.inner.add_torrent_file(content)

    async def select_files(self, torrent_id: str, file_ids: str) -> None:
        await self.inner.select_files(torrent_id, file_ids)

    async def aclose(self) -> None:
        aclose = getattr(self.inner, "aclose", None)
        if aclose is not None:
            await aclose()
        else:
            logger.debug("inner RD source has no aclose(); nothing to close")
