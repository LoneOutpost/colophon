"""OpenLibrary metadata source (search by title/author)."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.isbn import normalize_isbn
from colophon.core.sources import SourceQuery, SourceResult

_FIELDS = "title,author_name,first_publish_year,cover_i,key,isbn"

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


class OpenLibrarySource:
    name = "openlibrary"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url="https://openlibrary.org", timeout=15.0)

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.title and not query.isbn:
            return []
        if query.isbn:
            params: dict[str, object] = {"isbn": query.isbn, "limit": 5, "fields": _FIELDS}
        else:
            params = {"title": query.title, "limit": 5, "fields": _FIELDS}
            if query.author:
                params["author"] = query.author
        try:
            resp = await self._get(params)
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        docs = (resp.json() or {}).get("docs") or []
        return [self._to_result(doc) for doc in docs]

    @_RETRY
    async def _get(self, params: dict[str, object]) -> httpx.Response:
        return await self._client.get("/search.json", params=params)

    def _to_result(self, doc: dict[str, Any]) -> SourceResult:
        cover = doc.get("cover_i")
        return SourceResult(
            provider=self.name,
            title=doc.get("title"),
            authors=list(doc.get("author_name") or []),
            publish_year=doc.get("first_publish_year"),
            isbn=self._pick_isbn(doc.get("isbn")),
            cover_url=f"https://covers.openlibrary.org/b/id/{cover}-L.jpg" if cover else None,
            raw=doc,
        )

    @staticmethod
    def _pick_isbn(isbns: object) -> str | None:
        """First ISBN from the doc's list, preferring an ISBN-13, normalized."""
        if not isinstance(isbns, list) or not isbns:
            return None
        candidates = [i for i in isbns if isinstance(i, str)]
        if not candidates:
            return None
        preferred = next((i for i in candidates if len(normalize_isbn(i) or "") == 13), candidates[0])
        return normalize_isbn(preferred)
