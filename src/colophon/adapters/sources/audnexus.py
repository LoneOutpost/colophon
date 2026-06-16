"""Audnexus metadata source (lookup by ASIN; authoritative audiobook data)."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.sources import SourceQuery, SourceResult

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


class AudnexusSource:
    name = "audnexus"

    def __init__(self, *, region: str = "us", client: httpx.AsyncClient | None = None) -> None:
        self._region = region
        self._client = client or httpx.AsyncClient(base_url="https://api.audnex.us", timeout=15.0)

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.asin:
            return []
        try:
            resp = await self._get(query.asin)
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        return [self._to_result(resp.json() or {})]

    @_RETRY
    async def _get(self, asin: str) -> httpx.Response:
        return await self._client.get(f"/books/{asin}", params={"region": self._region})

    def _to_result(self, book: dict[str, Any]) -> SourceResult:
        series = book.get("seriesPrimary") or {}
        return SourceResult(
            provider=self.name,
            title=book.get("title"),
            authors=[a["name"] for a in book.get("authors") or [] if a.get("name")],
            narrators=[n["name"] for n in book.get("narrators") or [] if n.get("name")],
            series_name=series.get("name"),
            series_sequence=_pos_to_float(series.get("position")),
            publish_year=_year(book.get("releaseDate")),
            asin=book.get("asin"),
            cover_url=book.get("image"),
            description=book.get("summary"),
            raw=book,
        )


def _pos_to_float(value: str | int | float | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _year(release_date: str | None) -> int | None:
    if isinstance(release_date, str) and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None
