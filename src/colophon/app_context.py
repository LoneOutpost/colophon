"""Composition root: wire config, database, repositories, and metadata sources."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from colophon.adapters.audiobookshelf import AbsClient
from colophon.adapters.config import Config, default_config_path
from colophon.adapters.lazylibrarian import AudiobookPatterns, read_audiobook_patterns
from colophon.adapters.lazylibrarian_api import LazyLibrarianClient
from colophon.adapters.repository.store import (
    BookUnitRepo,
    HistoryRepo,
    OperationRepo,
    connect,
    migrate,
)
from colophon.adapters.sources.abs_agg import discover_providers
from colophon.adapters.sources.audnexus import AudnexusSource
from colophon.adapters.sources.googlebooks import GoogleBooksSource
from colophon.adapters.sources.internet_archive import InternetArchiveSource
from colophon.adapters.sources.openlibrary import OpenLibrarySource
from colophon.core.sources import MetadataSource


def default_db_path() -> Path:
    return user_data_path("colophon") / "colophon.db"


def build_all_sources(config: Config) -> list[MetadataSource]:
    """The full available set: the four built-ins plus discovered abs-agg providers."""
    sources: list[MetadataSource] = [
        AudnexusSource(), OpenLibrarySource(), GoogleBooksSource(), InternetArchiveSource()
    ]
    sources.extend(discover_providers(config.abs_agg_url))
    return sources


def arrange_sources(
    all_sources: list[MetadataSource], *, order: list[str], disabled: list[str]
) -> list[MetadataSource]:
    """Order `all_sources` by `order` (known names first, in that order; names not
    in `order` keep their incoming order, appended after); then drop any whose name
    is in `disabled`. Stale `order`/`disabled` names with no live source are ignored."""
    rank = {name: i for i, name in enumerate(order)}
    fallback = len(order)
    ordered = sorted(all_sources, key=lambda s: rank.get(s.name, fallback))
    return [s for s in ordered if s.name not in set(disabled)]


@dataclass
class AppContext:
    config: Config
    conn: sqlite3.Connection
    books: BookUnitRepo
    history: HistoryRepo
    operations: OperationRepo
    sources: list[MetadataSource]
    patterns: AudiobookPatterns
    abs_client: AbsClient | None
    ll_client: LazyLibrarianClient | None
    config_path: Path

    @classmethod
    def create(cls, config: Config, *, config_path: Path | None = None) -> AppContext:
        db = config.db_path or default_db_path()
        conn = connect(db)
        migrate(conn)
        patterns = (
            read_audiobook_patterns(config.lazylibrarian_config_ini)
            if config.lazylibrarian_config_ini
            else AudiobookPatterns()
        )
        sources = arrange_sources(
            build_all_sources(config),
            order=config.source_order,
            disabled=config.disabled_sources,
        )
        abs_client = (
            AbsClient(base_url=config.audiobookshelf_url, token=config.audiobookshelf_token)
            if config.audiobookshelf_url and config.audiobookshelf_token
            else None
        )
        ll_client = (
            LazyLibrarianClient(base_url=config.lazylibrarian_url, api_key=config.lazylibrarian_api_key)
            if config.lazylibrarian_url and config.lazylibrarian_api_key
            else None
        )
        return cls(
            config=config,
            conn=conn,
            books=BookUnitRepo(conn),
            history=HistoryRepo(conn),
            operations=OperationRepo(conn),
            sources=sources,
            patterns=patterns,
            abs_client=abs_client,
            ll_client=ll_client,
            config_path=config_path or default_config_path(),
        )

    def close(self) -> None:
        self.conn.close()
