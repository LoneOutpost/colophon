"""Composition root: wire config, database, repositories, and metadata sources."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from colophon.adapters.audiobookshelf import AbsClient
from colophon.adapters.config import Config, default_config_path
from colophon.adapters.lazylibrarian import AudiobookPatterns
from colophon.adapters.repository.store import (
    BookUnitRepo,
    EntityAliasRepo,
    GraphStore,
    HistoryRepo,
    NodeOverrideRepo,
    OperationRepo,
    connect,
    migrate,
)
from colophon.adapters.sources.abs_agg import discover_providers
from colophon.adapters.sources.audnexus import AudnexusSource
from colophon.adapters.sources.googlebooks import GoogleBooksSource
from colophon.adapters.sources.internet_archive import InternetArchiveSource
from colophon.adapters.sources.openlibrary import OpenLibrarySource
from colophon.core.sources import MetadataSource, arrange_sources

__all__ = ["AppContext", "arrange_sources", "build_all_sources", "default_db_path"]


def default_db_path() -> Path:
    return user_data_path("colophon") / "colophon.db"


def build_all_sources(config: Config) -> list[MetadataSource]:
    """The full available set: the four built-ins plus discovered abs-agg providers."""
    sources: list[MetadataSource] = [
        AudnexusSource(), OpenLibrarySource(), GoogleBooksSource(), InternetArchiveSource()
    ]
    sources.extend(discover_providers(config.abs_agg_url))
    return sources


@dataclass
class AppContext:
    config: Config
    conn: sqlite3.Connection
    books: BookUnitRepo
    history: HistoryRepo
    operations: OperationRepo
    overrides: NodeOverrideRepo
    aliases: EntityAliasRepo
    graph: GraphStore
    sources: list[MetadataSource]
    patterns: AudiobookPatterns
    abs_client: AbsClient | None
    config_path: Path

    @classmethod
    def create(cls, config: Config, *, config_path: Path | None = None) -> AppContext:
        db = config.db_path or default_db_path()
        conn = connect(db)
        migrate(conn)
        patterns = AudiobookPatterns(
            folder=config.organize_folder_pattern,
            single_file=config.organize_file_pattern,
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
        return cls(
            config=config,
            conn=conn,
            books=BookUnitRepo(conn),
            history=HistoryRepo(conn),
            operations=OperationRepo(conn),
            overrides=NodeOverrideRepo(conn),
            aliases=EntityAliasRepo(conn),
            graph=GraphStore(conn),
            sources=sources,
            patterns=patterns,
            abs_client=abs_client,
            config_path=config_path or default_config_path(),
        )

    def close(self) -> None:
        self.conn.close()
