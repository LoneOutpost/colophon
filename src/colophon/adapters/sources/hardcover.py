"""Hardcover (hardcover.app) metadata source — GraphQL, bearer-token auth."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.sources import SourceQuery, SourceResult

_QUERY = """
query ColophonBookSearch($q: String!) {
  books(where: {title: {_ilike: $q}}, limit: 5) {
    title
    release_year
    description
    contributions { author { name } }
    image { url }
  }
}
""".strip()

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


class HardcoverSource:
    name = "hardcover"

    def __init__(self, *, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(
            base_url="https://api.hardcover.app",
            headers=self._headers,
            timeout=15.0,
        )

    @_RETRY
    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        return await self._client.post("/v1/graphql", json=payload, headers=self._headers)

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.title:
            return []
        payload = {"query": _QUERY, "variables": {"q": f"%{query.title}%"}}
        try:
            resp = await self._post(payload)
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        body = resp.json() or {}
        if body.get("errors"):
            return []
        books = ((body.get("data") or {}).get("books")) or []
        return [self._to_result(book) for book in books]

    def _to_result(self, book: dict[str, Any]) -> SourceResult:
        authors = [
            c["author"]["name"]
            for c in (book.get("contributions") or [])
            if isinstance(c.get("author"), dict) and c["author"].get("name")
        ]
        image = book.get("image") or {}
        year = book.get("release_year")
        # ISBN not yet wired for Hardcover: its GraphQL editions/ISBN schema is unconfirmed.
        return SourceResult(
            provider=self.name,
            title=book.get("title"),
            subtitle=book.get("subtitle"),
            authors=authors,
            publish_year=year if isinstance(year, int) else None,
            cover_url=image.get("url"),
            description=book.get("description"),
            raw=book,
        )
