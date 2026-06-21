"""Normalized metadata-source query/result types and the source protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from colophon.core.models import _Base


class SourceQuery(_Base):
    title: str | None = None
    author: str | None = None
    asin: str | None = None
    series: str | None = None


class SourceResult(_Base):
    """One normalized candidate from any metadata source."""

    provider: str
    title: str | None = None
    authors: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    narrators: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    series_name: str | None = None
    series_sequence: float | None = None
    publish_year: int | None = None
    asin: str | None = None
    cover_url: str | None = None
    description: str | None = None
    genres: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    tags: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    raw: dict[str, object] = {}  # noqa: RUF012 - pydantic field default, copied per instance


@runtime_checkable
class MetadataSource(Protocol):
    """A metadata source. `name` identifies its provenance; `search` is async."""

    name: str

    async def search(self, query: SourceQuery) -> list[SourceResult]: ...
