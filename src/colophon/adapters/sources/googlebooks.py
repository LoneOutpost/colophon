"""Google Books metadata source (broad, no-auth catalog fallback)."""

from __future__ import annotations

from typing import Any

import httpx

from colophon.adapters.http import HTTP_RETRY
from colophon.core.coerce import year_or_none
from colophon.core.isbn import normalize_isbn
from colophon.core.sources import SourceQuery, SourceResult


class GoogleBooksSource:
    name = "googlebooks"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url="https://www.googleapis.com", timeout=15.0
        )

    @HTTP_RETRY
    async def _get(self, q: str) -> httpx.Response:
        return await self._client.get("/books/v1/volumes", params={"q": q, "maxResults": 5})

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.title and not query.isbn:
            return []
        if query.isbn:
            q = f"isbn:{query.isbn}"
        else:
            q = f"intitle:{query.title}"
            if query.author:
                q += f"+inauthor:{query.author}"
            if query.series:
                q += f"+{query.series}"  # additive free-text term to disambiguate within a series
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
            isbn=self._pick_isbn(vol.get("industryIdentifiers")),
            cover_url=images.get("thumbnail"),
            description=vol.get("description"),
            raw=vol,
        )

    @staticmethod
    def _pick_isbn(identifiers: object) -> str | None:
        """ISBN-13 if present, else ISBN-10, from industryIdentifiers; normalized."""
        if not isinstance(identifiers, list):
            return None
        by_type = {
            ident.get("type"): ident.get("identifier")
            for ident in identifiers
            if isinstance(ident, dict)
        }
        return normalize_isbn(by_type.get("ISBN_13") or by_type.get("ISBN_10"))
