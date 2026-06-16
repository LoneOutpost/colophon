"""OpenLibrary metadata source (search by title/author)."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.sources import SourceQuery, SourceResult

_FIELDS = "title,author_name,first_publish_year,cover_i,key"

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
        if not query.title:
            return []
        params: dict[str, object] = {"title": query.title, "limit": 5, "fields": _FIELDS}
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
            cover_url=f"https://covers.openlibrary.org/b/id/{cover}-L.jpg" if cover else None,
            raw=doc,
        )
