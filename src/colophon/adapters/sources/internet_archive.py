"""Internet Archive metadata source.

Searches the curated audiobook/spoken-word collections (LibriVox + the broader
spoken-word collection) by title/author, then pulls per-item metadata for runtime,
narrator (parsed from the free-text description), cover, and description. Keyless and
free; complements OpenLibrary (which is print-grade and carries no narrator/runtime).
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from colophon.core.sources import SourceQuery, SourceResult

# Collections that are reliably audiobooks/spoken word (precision over recall).
_COLLECTIONS = "collection:(librivoxaudio OR audio_bookspoetry)"
_SEARCH_FIELDS = ["identifier", "title", "creator", "year", "subject"]
_MAX_CANDIDATES = 5

# "Read by Jane Doe", "Narrated by A and B", "Reader: X" -> capture the name run.
_NARRATOR_RE = re.compile(
    r"(?:read by|narrated by|reader[s]?\s*[:\-])\s*(?P<names>[^.;\n<]+)",
    re.IGNORECASE,
)

_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=0.5, max=4),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)


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


def _parse_runtime(value: Any) -> int | None:
    """'7:34:27' / '58:03' -> milliseconds; None for missing/unparseable input."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not parts or not all(p.strip().isdigit() for p in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds * 1000


def _parse_narrators(description: Any) -> list[str]:
    """Best-effort narrator extraction from a free-text description. Returns [] when
    no 'read by'/'narrated by'/'reader:' cue is found (a miss beats a wrong name)."""
    if not isinstance(description, str):
        return []
    match = _NARRATOR_RE.search(description)
    if not match:
        return []
    names = re.split(r",|\band\b", match.group("names"))
    out: list[str] = []
    for raw in names:
        name = raw.strip(" \t-—·").strip()
        if name and name not in out:
            out.append(name)
    return out


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
        results: list[SourceResult] = []
        for doc in docs:
            meta = await self._metadata(doc.get("identifier"))
            results.append(self._to_result(doc, meta))
        return results

    @_RETRY
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
            narrators=_parse_narrators(meta.get("description")),
            publish_year=int(str(year)) if str(year or "").isdigit() else None,
            runtime_ms=_parse_runtime(meta.get("runtime")),
            cover_url=(
                f"https://archive.org/services/img/{identifier}" if identifier else None
            ),
            description=meta.get("description") if isinstance(meta.get("description"), str) else None,
            genres=_as_list(doc.get("subject") or meta.get("subject")),
            raw={"doc": doc, "meta": meta},
        )
