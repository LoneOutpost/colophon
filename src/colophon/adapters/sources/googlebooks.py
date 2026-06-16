"""Google Books metadata source (broad, no-auth catalog fallback)."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.coerce import year_or_none
from colophon.core.sources import SourceQuery, SourceResult

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


class GoogleBooksSource:
    name = "googlebooks"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url="https://www.googleapis.com", timeout=15.0
        )

    @_RETRY
    async def _get(self, q: str) -> httpx.Response:
        return await self._client.get("/books/v1/volumes", params={"q": q, "maxResults": 5})

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.title:
            return []
        q = f"intitle:{query.title}"
        if query.author:
            q += f"+inauthor:{query.author}"
        try:
            resp = await self._get(q)
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        items = (resp.json() or {}).get("items") or []
        return [self._to_result(item.get("volumeInfo") or {}) for item in items]

    def _to_result(self, vol: dict[str, Any]) -> SourceResult:
        images = vol.get("imageLinks") or {}
        return SourceResult(
            provider=self.name,
            title=vol.get("title"),
            authors=[a for a in (vol.get("authors") or []) if isinstance(a, str)],
            publish_year=year_or_none(vol.get("publishedDate")),
            cover_url=images.get("thumbnail"),
            description=vol.get("description"),
            raw=vol,
        )
