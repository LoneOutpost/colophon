"""Pydantic domain models. Internal snake_case; camelCase on the wire."""

from __future__ import annotations

import hashlib
import os
import unicodedata
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def new_batch_id() -> str:
    """A fresh id grouping the operations of one undoable action (edit/tag/move)."""
    return uuid.uuid4().hex


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
    DATAFILE = "datafile"
    GRAPHING = "graphing"   # derived from a directory-tree relationship (Phase 3b)
    AUDNEXUS = "audnexus"
    AUDIBLE = "audible"
    HARDCOVER = "hardcover"
    OPENLIBRARY = "openlibrary"
    GOOGLEBOOKS = "googlebooks"
    MANUAL = "manual"


# The "weak" provenance tier: a field sourced from the folder name or the filename is a guess that
# fill-empty passes and graph fill-down (GRAPHING) are allowed to overwrite. A tag/datafile/match/
# manual value never is. Single source of truth — imported anywhere that gates on "is this weak?".
WEAK_PROV: frozenset[str] = frozenset({Provenance.DIRECTORY.value, Provenance.FILENAME.value})


class ContentKind(StrEnum):
    """Axis 1 — how many distinct works a folder holds."""

    SINGLE = "single"
    MULTI = "multi"
    UNKNOWN = "unknown"


class FolderKind(StrEnum):
    """Axis 2 — what the folder itself represents."""

    AUTHOR = "author"
    TITLE = "title"
    UNDETERMINED = "undetermined"


class FindingSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class FindingCode(StrEnum):
    LOOSE_IN_AUTHOR = "loose_in_author"
    MULTI_IN_AUTHOR = "multi_in_author"
    MULTI_IN_UNDETERMINED = "multi_in_undetermined"
    MIXED_WORKS = "mixed_works"
    DUP_FORMAT = "dup_format"
    DUP_EDITION = "dup_edition"
    STRUCTURE_UNCLEAR = "structure_unclear"
    EMPTY_AUDIO = "empty_audio"  # a file with real size but no readable audio (corrupt/incomplete)
    MIXED_QUALITY = "mixed_quality"  # a book's audio files disagree in bitrate/format (possible mis-grouping)


# Findings whose remedy is fostering the folder into one subfolder per work.
RESTRUCTURE_FINDINGS = frozenset({
    FindingCode.LOOSE_IN_AUTHOR,
    FindingCode.MULTI_IN_AUTHOR,
    FindingCode.MULTI_IN_UNDETERMINED,
    FindingCode.MIXED_WORKS,
})

# Findings retired from the user-facing surface (not "Needs attention", not a review reason).
# LOOSE_IN_AUTHOR — a single book sitting loose in an author folder — is the normal audiobook
# layout under the graph model, not a problem to fix. It is still generated and kept in
# RESTRUCTURE_FINDINGS because IDENTIFY reads it to name a foster container's author from its
# folder; it is simply not shown as something the user must act on.
SUPPRESSED_FINDINGS = frozenset({FindingCode.LOOSE_IN_AUTHOR})

# Findings that BLOCK persisted actions (tag/organize/encode): the fault is outside the app's
# control, so no in-app edit can fix it and attempting to persist would error. Structural findings
# like MIXED_WORKS are deliberately absent — those are fixable in-app by splitting the folder.
BLOCKING_FINDINGS = frozenset({FindingCode.EMPTY_AUDIO})


class Finding(_Base):
    """One structural finding about a book unit (recomputed each scan)."""

    code: FindingCode
    severity: FindingSeverity
    detail: str


class DetectedWork(_Base):
    """One distinct work the classifier found inside a folder; the unit a split
    would foster into. `files` are the source files belonging to this work. `author`, when set,
    always comes from the files' embedded artist tag (see `classify._to_work`) — a trusted source,
    so a leaf built from it records TAG provenance, not a weak filename guess. `label_prov` records
    where `label` came from (a Title/Album tag vs the filename) so the leaf's title provenance is
    honest rather than always stamped `filename`."""

    label: str
    label_prov: str = Provenance.FILENAME.value
    author: str | None = None
    series: str | None = None
    sequence: float | None = None
    files: list[Path] = []


class SourceFile(_Base):
    path: Path
    size: int
    duration_seconds: float
    ext: str
    bitrate: int = 0       # bits per second (0 = unknown)
    sample_rate: int = 0   # Hz (0 = unknown)
    channels: int = 0      # 1 mono, 2 stereo, ... (0 = unknown)
    codec: str = ""        # friendly format label (MP3 / M4B / AAC / FLAC / Opus / OGG); "" = unknown


class Chapter(_Base):
    title: str
    start_ms: int
    end_ms: int


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
    ENCODED = "encoded"
    ORGANIZED = "organized"
    FAILED = "failed"
    SKIPPED = "skipped"


class Phase(StrEnum):
    """Pipeline phases, in pipeline order (declaration order is load-bearing)."""

    SEARCH = "search"
    CATEGORIZE = "categorize"
    IDENTIFY = "identify"
    MATCH = "match"
    TAG = "tag"
    ORGANIZE = "organize"
    ENCODE = "encode"


class PhaseState(StrEnum):
    PENDING = "pending"
    FRESH = "fresh"
    STALE = "stale"
    RUNNING = "running"
    FAILED = "failed"


class PhaseRecord(_Base):
    state: PhaseState = PhaseState.PENDING
    updated_at: datetime | None = None
    detail: str | None = None


class SeriesRef(_Base):
    name: str
    sequence: float | None = None


class NodeOverride(_Base):
    """A user's manual classification of a directory node, keyed (in storage) by folder path."""

    kind: str
    value: str | None = None


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
    franchise: str | None = None  # the franchise this book belongs to; manual edit or folder-derived
    publish_year: int | None = None
    publisher: str | None = None
    description: str | None = None
    genres: list[str] = []
    tags: list[str] = []
    asin: str | None = None
    isbn: str | None = None
    language: str | None = None
    abridged: bool | None = None
    cover_path: Path | None = None
    cover_url: str | None = None  # source-provided cover image URL, fetched into cover_path
    output_path: Path | None = None  # the produced M4B's final location once organized
    chapters: list[Chapter] = []

    provenance: dict[str, str] = {}
    confidence: float = 0.0
    confidence_signals: list[ConfidenceSignal] = []
    content_kind: ContentKind = ContentKind.UNKNOWN
    folder_kind: FolderKind = FolderKind.UNDETERMINED
    classification_confidence: float = 0.0
    classification_signals: list[ConfidenceSignal] = []
    # Local-identification confidence (0-100) rolled up from the graph evidence: how sure we are we
    # know this book locally (pre-match). Distinct from `confidence`, which is the post-match score.
    identity_confidence: float = 0.0
    findings: list[Finding] = []
    detected_works: list[DetectedWork] = []
    acknowledged_findings: list[FindingCode] = []
    manually_confirmed: bool = False
    missing: bool = False  # tracked folder vanished outside the app; surfaced for cleanup
    state: BookState = BookState.DETECTED
    phases: dict[Phase, PhaseRecord] = {}  # sparse: a missing key reads as PENDING
    skipped: bool = False

    created_at: datetime = Field(frozen=True)
    updated_at: datetime

    @staticmethod
    def id_for(source_folder: Path) -> str:
        """Derive the deterministic id for a source folder. The id is a pure function
        of the (normalized) folder, so callers can look a unit up by folder without
        constructing a throwaway model."""
        # Normalize so logically-identical paths hash to the same id. normpath
        # collapses "." and trailing slashes; NFC unifies unicode equivalents.
        # We avoid Path.resolve() because the folder may not exist yet.
        normalized = unicodedata.normalize("NFC", os.path.normpath(str(source_folder)))
        # 64-bit (16 hex char) truncation is an accepted collision tradeoff.
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def new(cls, *, source_folder: Path) -> BookUnit:
        now = _now()
        return cls(
            id=cls.id_for(source_folder),
            source_folder=source_folder,
            created_at=now,
            updated_at=now,
        )

    def touch(self) -> None:
        """Bump updated_at to now; call after any mutation before persisting."""
        self.updated_at = _now()

    @property
    def duration_ms(self) -> int:
        """Total measured audio length across source files, in milliseconds
        (0 when there are no files)."""
        return round(sum(sf.duration_seconds for sf in self.source_files) * 1000)


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
    isbn: str | None = None
    track: int | None = None
