"""Composition root: wire config, database, repositories, and metadata sources."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_path

from colophon.adapters.audiobookshelf import AbsClient
from colophon.adapters.config import Config, default_config_path
from colophon.adapters.lazylibrarian import PathPatterns
from colophon.adapters.repository.store import (
    BookUnitRepo,
    EntityAliasRepo,
    GraphStore,
    GroupingOverrideRepo,
    HistoryRepo,
    KnownFranchiseRepo,
    NodeOverrideRepo,
    OperationRepo,
    RdCacheRepo,
    connect,
    migrate,
)
from colophon.adapters.sources.abs_agg import discover_providers
from colophon.adapters.sources.audnexus import AudnexusSource
from colophon.adapters.sources.googlebooks import GoogleBooksSource
from colophon.adapters.sources.internet_archive import InternetArchiveSource
from colophon.adapters.sources.openlibrary import OpenLibrarySource
from colophon.core.jobs import JobRegistry
from colophon.core.library_graph import LibraryGraph
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
    rd_cache: RdCacheRepo
    history: HistoryRepo
    operations: OperationRepo
    overrides: NodeOverrideRepo
    grouping: GroupingOverrideRepo
    aliases: EntityAliasRepo
    franchises: KnownFranchiseRepo
    graph: GraphStore
    library_graph: LibraryGraph
    sources: list[MetadataSource]
    patterns: PathPatterns
    abs_client: AbsClient | None
    config_path: Path
    jobs: JobRegistry = field(default_factory=JobRegistry)

    @classmethod
    def create(cls, config: Config, *, config_path: Path | None = None) -> AppContext:
        db = config.db_path or default_db_path()
        conn = connect(db)
        migrate(conn)
        patterns = PathPatterns(
            folder=config.organize_folder_pattern,
            single_file=config.organize_file_pattern,
            series_pattern=config.series_pattern,
            series_name_pattern=config.series_name_pattern,
            series_number_pattern=config.series_number_pattern,
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
        graph_store = GraphStore(conn)
        return cls(
            config=config,
            conn=conn,
            books=BookUnitRepo(conn),
            rd_cache=RdCacheRepo(conn),
            history=HistoryRepo(conn),
            operations=OperationRepo(conn),
            overrides=NodeOverrideRepo(conn),
            grouping=GroupingOverrideRepo(conn),
            aliases=EntityAliasRepo(conn),
            franchises=KnownFranchiseRepo(conn),
            graph=graph_store,
            library_graph=LibraryGraph.from_records(*graph_store.load_all()),
            sources=sources,
            patterns=patterns,
            abs_client=abs_client,
            config_path=config_path or default_config_path(),
        )

    def close(self) -> None:
        self.conn.close()
