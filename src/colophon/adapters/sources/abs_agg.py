"""abs-agg metadata source.

abs-agg (https://github.com/Vito0912/abs-agg) is a self-hosted aggregator that
exposes many audiobook metadata providers behind one HTTP API, speaking the
Audiobookshelf custom-metadata-provider format. Each provider is registered as a
separate Colophon source; this class is parameterized by the provider id.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from colophon.adapters.http import get_json_list
from colophon.core.coerce import to_float, year_or_none
from colophon.core.isbn import normalize_isbn
from colophon.core.people import split_people
from colophon.core.sources import SourceQuery, SourceResult

logger = logging.getLogger(__name__)



def _str_or_none(value: Any) -> str | None:
    """Pass through a non-empty string; everything else becomes None."""
    return value if isinstance(value, str) and value.strip() else None


# Known per-provider name conventions. A provider listed here splits on exactly
# these delimiters; unlisted providers fall back to split_people's auto
# heuristic. Confirm provider-id keys against the live abs-agg /providers
# response before adding (unconfirmed providers safely stay on auto).
_PROVIDER_SEPARATORS: dict[str, list[str]] = {
    "hardcover": [","],  # "First Last, First Last" (issue #86); never "Last, First"
}


class AbsAggSource:
    def __init__(
        self, *, provider: str, label: str, client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
    ) -> None:
        self.name = provider
        self.label = label
        self._separators = _PROVIDER_SEPARATORS.get(provider)
        self._client = client or httpx.AsyncClient(base_url=base_url or "", timeout=15.0)

    async def search(self, query: SourceQuery) -> list[SourceResult]:
        if not query.title:
            return []
        params: dict[str, object] = {"title": query.title}
        if query.author:
            params["author"] = query.author
        matches = await get_json_list(
            self._client, f"/{self.name}/search", params=params, key="matches"
        )
        return [self._to_result(m) for m in matches]

    def _to_result(self, m: dict[str, Any]) -> SourceResult:
        series = m.get("series") or []
        first = series[0] if series and isinstance(series[0], dict) else {}
        duration = m.get("duration")
        return SourceResult(
            provider=self.name,
            title=m.get("title"),
            subtitle=m.get("subtitle"),
            authors=split_people(_str_or_none(m.get("author")), separators=self._separators),
            narrators=split_people(_str_or_none(m.get("narrator")), separators=self._separators),
            series_name=first.get("series"),
            series_sequence=to_float(first.get("sequence")),
            publish_year=year_or_none(m.get("publishedYear")),
            asin=m.get("asin"),
            isbn=normalize_isbn(m.get("isbn")),
            publisher=m.get("publisher"),
            language=m.get("language"),
            cover_url=m.get("cover"),
            description=m.get("description") if isinstance(m.get("description"), str) else None,
            genres=[g for g in (m.get("genres") or []) if isinstance(g, str)],
            tags=[t for t in (m.get("tags") or []) if isinstance(t, str)],
            runtime_ms=duration * 1000 if isinstance(duration, int) else None,
            raw=m,
        )


def discover_providers(base_url: str | None) -> list[AbsAggSource]:
    """One synchronous GET /providers at startup; register each available
    provider as an AbsAggSource bound to a shared async client. Any failure
    (no url, unreachable, non-200, bad body) registers nothing (logged)."""
    if not base_url:
        return []
    try:
        with httpx.Client(base_url=base_url, timeout=5.0) as client:
            resp = client.get("/providers")
            if resp.status_code >= 400:
                logger.warning(f"abs-agg /providers returned {resp.status_code}")
                return []
            payload = resp.json() or []
    except (httpx.HTTPError, ValueError):
        logger.warning(f"abs-agg discovery failed at {base_url}", exc_info=True)
        return []
    # The API wraps the list as {"providers": [...]}; tolerate a bare list too.
    providers = payload.get("providers") or [] if isinstance(payload, dict) else payload
    shared = httpx.AsyncClient(base_url=base_url, timeout=15.0)
    out: list[AbsAggSource] = []
    for p in providers:
        if isinstance(p, dict) and p.get("available") and p.get("id"):
            out.append(AbsAggSource(provider=p["id"], label=p.get("name") or p["id"], client=shared))
    return out
