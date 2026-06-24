"""OpenLibrary metadata source (search by title/author)."""

from __future__ import annotations

from typing import Any

import httpx

from colophon.adapters.http import get_json_list
from colophon.core.isbn import normalize_isbn
from colophon.core.sources import SourceQuery, SourceResult

_FIELDS = "title,author_name,first_publish_year,cover_i,key,isbn"



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
        docs = await get_json_list(self._client, "/search.json", params=params, key="docs")
        return [self._to_result(doc) for doc in docs]

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
