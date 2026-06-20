"""Pydantic domain models. Internal snake_case; camelCase on the wire."""

from __future__ import annotations

import hashlib
import os
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _Base(BaseModel):
    # camelCase aliasing is for the API boundary only; persistence stores field
    # names (dump/load with by_alias=False). Do not flip the repo to by_alias=True
    # or stored JSON blobs become unreadable.
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        frozen=False,
    )


class Provenance(StrEnum):
    """Where a field value came from."""

    TAG = "tag"
    DIRECTORY = "directory"
    FILENAME = "filename"
    SIDECAR = "sidecar"
    AUDNEXUS = "audnexus"
    AUDIBLE = "audible"
    HARDCOVER = "hardcover"
    OPENLIBRARY = "openlibrary"
    GOOGLEBOOKS = "googlebooks"
    MANUAL = "manual"


class SourceFile(_Base):
    path: Path
    size: int
    duration_seconds: float
    ext: str


class ConfidenceSignal(_Base):
    """One contribution to a book unit's confidence score, kept for auditability."""

    name: str
    points: int
    detail: str


class BookState(StrEnum):
    DETECTED = "detected"
    IDENTIFIED = "identified"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    ENCODING = "encoding"
    ORGANIZED = "organized"
    FAILED = "failed"
    SKIPPED = "skipped"


class SeriesRef(_Base):
    name: str
    sequence: float | None = None


def _now() -> datetime:
    return datetime.now(UTC)


class BookUnit(_Base):
    """A filesystem-origin candidate book: one folder or one multi-track file.

    `provenance` maps a field name (e.g. "title", "authors") to the source string
    that supplied its current value (one of `Provenance`'s values).
    """

    id: str
    source_folder: Path
    source_files: list[SourceFile] = []

    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = []
    narrators: list[str] = []
    series: list[SeriesRef] = []
    publish_year: int | None = None
    publisher: str | None = None
    description: str | None = None
    genres: list[str] = []
    tags: list[str] = []
    asin: str | None = None
    language: str | None = None
    cover_path: Path | None = None
    cover_url: str | None = None  # source-provided cover image URL, fetched into cover_path
    output_path: Path | None = None  # the produced M4B's final location once organized

    provenance: dict[str, str] = {}
    confidence: float = 0.0
    confidence_signals: list[ConfidenceSignal] = []
    state: BookState = BookState.DETECTED

    created_at: datetime = Field(frozen=True)
    updated_at: datetime

    @classmethod
    def new(cls, *, source_folder: Path) -> BookUnit:
        now = _now()
        # Normalize so logically-identical paths hash to the same id. normpath
        # collapses "." and trailing slashes; NFC unifies unicode equivalents.
        # We avoid Path.resolve() because the folder may not exist yet.
        normalized = unicodedata.normalize("NFC", os.path.normpath(str(source_folder)))
        # 64-bit (16 hex char) truncation is an accepted collision tradeoff.
        book_id = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
        return cls(id=book_id, source_folder=source_folder, created_at=now, updated_at=now)

    def touch(self) -> None:
        """Bump updated_at to now; call after any mutation before persisting."""
        self.updated_at = _now()


class EditChange(_Base):
    """One field change applied to a book, with the prior value for undo."""

    book_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None


class OperationRecord(_Base):
    """One logged file/tag operation, retained for audit and recovery.

    `before`/`after` are JSON snapshots (e.g. EmbeddedTags) so a tag write can be
    reverted by replaying `before`. `applied_at`/`reverted` are managed by the repo.
    """

    batch_id: str
    book_id: str
    op_type: str
    target: str
    before: str | None = None
    after: str | None = None
    outcome: str = "ok"
    detail: str | None = None


class EmbeddedTags(_Base):
    """Normalized view of tags read from an audio file (any container)."""

    title: str | None = None
    album: str | None = None
    artist: str | None = None
    narrator: str | None = None
    series: str | None = None
    sequence: float | None = None
    year: int | None = None
    genre: str | None = None
    description: str | None = None
    asin: str | None = None
