"""Hardcover (hardcover.app) metadata source — GraphQL, bearer-token auth."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.isbn import normalize_isbn
from colophon.core.sources import SourceQuery, SourceResult

# Title search: ISBN lives on editions, so pull the default print/ebook edition's
# ISBN alongside the book (audiobook editions carry ASIN, not ISBN).
_QUERY = """
query ColophonBookSearch($q: String!) {
  books(where: {title: {_ilike: $q}}, limit: 5) {
    title
    release_year
    description
    contributions { author { name } }
    image { url }
    default_physical_edition { isbn_13 isbn_10 }
    default_ebook_edition { isbn_13 isbn_10 }
  }
}
""".strip()

# ISBN search: match an edition by either ISBN form, then map back to its book.
_ISBN_QUERY = """
query ColophonEditionByIsbn($isbn: String!) {
  editions(where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]}, limit: 5) {
    isbn_13
    isbn_10
    book {
      title
      release_year
      description
      contributions { author { name } }
      image { url }
    }
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
        if not query.title and not query.isbn:
            return []
        if query.isbn:
            payload = {"query": _ISBN_QUERY, "variables": {"isbn": query.isbn}}
        else:
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
        data = body.get("data") or {}
        if query.isbn:
            editions = data.get("editions") or []
            return [
                self._build(e["book"], _edition_isbn(e))
                for e in editions
                if isinstance(e.get("book"), dict)
            ]
        return [self._build(book, _book_isbn(book)) for book in (data.get("books") or [])]

    def _build(self, book: dict[str, Any], isbn: str | None) -> SourceResult:
        authors = [
            c["author"]["name"]
            for c in (book.get("contributions") or [])
            if isinstance(c.get("author"), dict) and c["author"].get("name")
        ]
        image = book.get("image") or {}
        year = book.get("release_year")
        return SourceResult(
            provider=self.name,
            title=book.get("title"),
            subtitle=book.get("subtitle"),
            authors=authors,
            publish_year=year if isinstance(year, int) else None,
            isbn=isbn,
            cover_url=image.get("url"),
            description=book.get("description"),
            raw=book,
        )


def _edition_isbn(edition: dict[str, Any]) -> str | None:
    """ISBN-13 if present on the matched edition, else ISBN-10, normalized."""
    return normalize_isbn(edition.get("isbn_13") or edition.get("isbn_10"))


def _book_isbn(book: dict[str, Any]) -> str | None:
    """ISBN from a book's default print edition, falling back to its ebook edition."""
    for key in ("default_physical_edition", "default_ebook_edition"):
        edition = book.get(key) or {}
        isbn = edition.get("isbn_13") or edition.get("isbn_10")
        if isbn:
            return normalize_isbn(isbn)
    return None
