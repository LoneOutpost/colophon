"""Audnexus metadata source.

Looks a book up by ASIN on Audnexus (api.audnex.us — authoritative, audiobook-
specific data: narrators, series, runtime). Audnexus has no title search, so when
only a title/author is known the source first resolves candidate ASINs through
Audible's public catalog keyword search, then enriches each through Audnexus.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from colophon.adapters.http import HTTP_RETRY
from colophon.core.coerce import to_float, year_or_none
from colophon.core.models import Chapter, _Base
from colophon.core.sources import SourceQuery, SourceResult

_AUDNEX_BASE = "https://api.audnex.us"
_AUDIBLE_BASE = "https://api.audible.com/1.0"



class ChapterFetch(_Base):
    """Named chapters fetched from Audnexus plus the reported total runtime."""

    chapters: list[Chapter] = []  # noqa: RUF012 - pydantic default, copied per instance
    runtime_ms: int = 0


class AudnexusSource:
    name = "audnexus"

    def __init__(
        self,
        *,
        region: str = "us",
        max_results: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._region = region
        self._max_results = max_results
        # Absolute URLs are used per request (two hosts), so the client needs no
        # base_url.
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if query.asin:
            asins = [query.asin]
        elif query.title:
            asins = await self._search_asins(query)
        else:
            return []
        if not asins:
            return []
        books = await asyncio.gather(*(self._fetch_book(a) for a in asins))
        return [self._to_result(b) for b in books if b is not None]

    async def _search_asins(self, query: SourceQuery) -> list[str]:
        """Resolve candidate ASINs for a title/author via Audible's catalog
        keyword search, capped to `max_results` (best-relevance first)."""
        keywords = " ".join(filter(None, [query.title, query.author]))
        try:
            resp = await self._catalog_get(keywords)
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        products = (resp.json() or {}).get("products") or []
        asins = [p["asin"] for p in products if isinstance(p, dict) and p.get("asin")]
        return asins[: self._max_results]

    async def _fetch_book(self, asin: str) -> dict[str, Any] | None:
        """Fetch and normalize one book from Audnexus, or None when unavailable
        (e.g. a delisted ASIN returns 404)."""
        try:
            resp = await self._book_get(asin)
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        return resp.json() or None

    @HTTP_RETRY
    async def _book_get(self, asin: str) -> httpx.Response:
        return await self._client.get(
            f"{_AUDNEX_BASE}/books/{asin}", params={"region": self._region}
        )

    async def fetch_chapters(self, asin: str) -> ChapterFetch | None:
        """GET /books/{asin}/chapters -> named chapters + total runtime. None on
        404 / transport error; an empty chapter list when the body has no usable
        chapters."""
        try:
            resp = await self._chapters_get(asin)
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        data = resp.json() or {}
        raw = data.get("chapters")
        chapters: list[Chapter] = []
        if isinstance(raw, list):
            for n, c in enumerate(raw, start=1):
                if not isinstance(c, dict):
                    continue
                start = c.get("startOffsetMs")
                length = c.get("lengthMs")
                if not isinstance(start, int) or not isinstance(length, int):
                    continue
                title = (c.get("title") or "").strip() or f"Chapter {n}"
                chapters.append(Chapter(title=title, start_ms=start, end_ms=start + length))
        runtime = data.get("runtimeLengthMs")
        return ChapterFetch(
            chapters=chapters, runtime_ms=runtime if isinstance(runtime, int) else 0
        )

    @HTTP_RETRY
    async def _chapters_get(self, asin: str) -> httpx.Response:
        return await self._client.get(
            f"{_AUDNEX_BASE}/books/{asin}/chapters", params={"region": self._region}
        )

    @HTTP_RETRY
    async def _catalog_get(self, keywords: str) -> httpx.Response:
        return await self._client.get(
            f"{_AUDIBLE_BASE}/catalog/products",
            params={
                "keywords": keywords,
                "num_results": self._max_results,
                "products_sort_by": "Relevance",
                "response_groups": "contributors,product_desc",
            },
        )

    def _to_result(self, book: dict[str, Any]) -> SourceResult:
        series = book.get("seriesPrimary") or {}
        runtime_min = book.get("runtimeLengthMin")
        runtime_ms = int(runtime_min) * 60000 if isinstance(runtime_min, (int, float)) and runtime_min > 0 else None
        fmt = book.get("formatType")
        abridged = {"abridged": True, "unabridged": False}.get(fmt.lower()) if isinstance(fmt, str) else None
        return SourceResult(
            provider=self.name,
            title=book.get("title"),
            subtitle=book.get("subtitle"),
            authors=[a["name"] for a in book.get("authors") or [] if a.get("name")],
            narrators=[n["name"] for n in book.get("narrators") or [] if n.get("name")],
            series_name=series.get("name"),
            series_sequence=to_float(series.get("position")),
            publish_year=year_or_none(book.get("releaseDate")),
            asin=book.get("asin"),
            cover_url=book.get("image"),
            description=book.get("summary"),
            genres=[g["name"] for g in book.get("genres") or [] if g.get("name") and g.get("type") == "genre"],
            tags=[g["name"] for g in book.get("genres") or [] if g.get("name") and g.get("type") == "tag"],
            runtime_ms=runtime_ms,
            abridged=abridged,
            raw=book,
        )


