"""Internet Archive metadata source.

Searches the curated audiobook/spoken-word collections (LibriVox + the broader
spoken-word collection) by title/author, then pulls per-item metadata for runtime,
narrator (parsed from the free-text description), cover, and description. Keyless and
free; complements OpenLibrary (which is print-grade and carries no narrator/runtime).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from colophon.adapters.http import HTTP_RETRY
from colophon.core.sources import SourceQuery, SourceResult
from colophon.core.textparse import parse_narrators, parse_runtime_ms

# Collections that are reliably audiobooks/spoken word (precision over recall).
_COLLECTIONS = "collection:(librivoxaudio OR audio_bookspoetry)"
_SEARCH_FIELDS = ["identifier", "title", "creator", "year", "subject"]
_MAX_CANDIDATES = 5


def _quote(value: str) -> str:
    """A Solr phrase term: drop embedded quotes, wrap in quotes."""
    return '"' + value.replace('"', " ").strip() + '"'


def _as_list(value: Any) -> list[str]:
    """Normalize a field that may be a string, list, or None into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]


class InternetArchiveSource:
    name = "internetarchive"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url="https://archive.org", timeout=15.0
        )

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.title:
            return []
        q = f"{_COLLECTIONS} AND title:({_quote(query.title)})"
        if query.author:
            q += f" AND creator:({_quote(query.author)})"
        params: dict[str, object] = {
            "q": q,
            "fl[]": _SEARCH_FIELDS,
            "rows": _MAX_CANDIDATES,
            "output": "json",
        }
        try:
            resp = await self._search(params)
        except httpx.HTTPError:
            return []
        if resp.status_code >= 400:
            return []
        docs = (((resp.json() or {}).get("response")) or {}).get("docs") or []
        # Fetch each candidate's per-item metadata concurrently rather than in a
        # serial await loop (the search returns up to _MAX_CANDIDATES docs).
        metas = await asyncio.gather(*(self._metadata(d.get("identifier")) for d in docs))
        return [self._to_result(doc, meta) for doc, meta in zip(docs, metas, strict=True)]

    @HTTP_RETRY
    async def _search(self, params: dict[str, object]) -> httpx.Response:
        return await self._client.get("/advancedsearch.php", params=params)

    async def _metadata(self, identifier: str | None) -> dict[str, Any]:
        """Per-item metadata, or {} on any failure (the search doc still yields a
        usable, if thinner, candidate)."""
        if not identifier:
            return {}
        try:
            resp = await self._client.get(f"/metadata/{identifier}")
            if resp.status_code >= 400:
                return {}
            return (resp.json() or {}).get("metadata") or {}
        except httpx.HTTPError:
            return {}

    def _to_result(self, doc: dict[str, Any], meta: dict[str, Any]) -> SourceResult:
        identifier = doc.get("identifier")
        year = doc.get("year") or meta.get("year")
        return SourceResult(
            provider=self.name,
            title=doc.get("title") or meta.get("title"),
            authors=_as_list(doc.get("creator") or meta.get("creator")),
            narrators=parse_narrators(meta.get("description")),
            publish_year=int(str(year)) if str(year or "").isdigit() else None,
            runtime_ms=parse_runtime_ms(meta.get("runtime")),
            cover_url=(
                f"https://archive.org/services/img/{identifier}" if identifier else None
            ),
            description=meta.get("description") if isinstance(meta.get("description"), str) else None,
            genres=_as_list(doc.get("subject") or meta.get("subject")),
            raw={"doc": doc, "meta": meta},
        )
