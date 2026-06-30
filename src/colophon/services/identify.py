"""IDENTIFY pipeline: resolve a book's identity from gathered evidence.

Decomposed into named, single-purpose steps so each is testable in isolation and
the IDENTIFY phase stays atomic: gather (read + vet) -> seed_series -> resolve
(reconcile) -> normalize (seam) -> attribute (structural). `_run_local` calls
`run_identify`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from re import Pattern

from colophon.adapters.audio import read_audio_metadata
from colophon.adapters.sidecar import (
    DatafileSidecar,
    is_container_datafile,
    read_datafile_sidecar,
)
from colophon.core.dirinfer import infer_from_path
from colophon.core.filename_cluster import shares_token
from colophon.core.filename_parser import parse_filename
from colophon.core.match import clean_match_title
from colophon.core.models import (
    RESTRUCTURE_FINDINGS,
    BookUnit,
    ContentKind,
    EmbeddedTags,
    FolderKind,
    Provenance,
    SeriesRef,
    _Base,
)
from colophon.core.reconcile import reconcile

logger = logging.getLogger(__name__)


class Evidence(_Base):
    """Raw identity evidence gathered for one book, post-vetting."""

    first_path: Path | None = None
    embedded: EmbeddedTags | None = None
    filename_fields: dict[str, str] = {}  # noqa: RUF012 - pydantic default, copied per instance
    datafile: DatafileSidecar | None = None  # None when vetted out as a container datafile
    directory_fields: dict[str, str] = {}  # noqa: RUF012 - pydantic default, copied per instance


# Fields reconcile can fill from a datafile sidecar (the DATAFILE tier), each with the
# empty value it resets to. Mirrors core/reconcile.py's sidecar branches.
_DATAFILE_FIELDS: dict[str, object] = {
    "title": "", "subtitle": "", "authors": [], "narrators": [], "series": [],
    "publish_year": None, "publisher": "", "description": "", "asin": "", "isbn": "",
}


def drop_orphaned_datafile_fields(book: BookUnit, evidence: Evidence) -> None:
    """Re-derive support: when the datafile sidecar that filled a field is gone (deleted,
    or now vetted out as a container datafile, so `gather` produced no datafile), clear the
    orphaned DATAFILE-stamped field so `reconcile` refills it from the surviving tiers — or
    leaves it empty for a match to fill. A no-op when a datafile is still present."""
    if evidence.datafile is not None:
        return
    for field, empty in _DATAFILE_FIELDS.items():
        if book.provenance.get(field) == Provenance.DATAFILE.value:
            setattr(book, field, list(empty) if isinstance(empty, list) else empty)
            book.provenance.pop(field, None)


def gather(
    book: BookUnit, *, root: Path, pattern: Pattern[str], scheme: list[Pattern[str]]
) -> Evidence:
    """Read all identity evidence for `book` and vet it: drop a datafile sidecar that
    describes a container (a MULTI folder) rather than a book."""
    first_path = book.source_files[0].path if book.source_files else None
    embedded = read_audio_metadata(first_path)[1] if first_path else None  # cache hit from SEARCH
    filename_fields = parse_filename(pattern, first_path.name) if first_path else {}
    datafile = read_datafile_sidecar(book.source_folder)
    if datafile is not None and is_container_datafile(
        datafile, book.source_folder, book.content_kind
    ):
        logger.debug(
            f"scan {book.source_folder}: IDENTIFY ignored container datafile "
            f"(title={datafile.title!r} authors={datafile.authors})"
        )
        datafile = None
    directory_fields = infer_from_path(book.source_folder, root, scheme)
    return Evidence(
        first_path=first_path, embedded=embedded, filename_fields=filename_fields or {},
        datafile=datafile, directory_fields=directory_fields,
    )


def seed_series(book: BookUnit) -> None:
    """Pre-resolve: an untagged single book takes its series/sequence from the filename
    cluster, so reconcile's `if not book.series` gate keeps it."""
    if book.content_kind is ContentKind.SINGLE and book.detected_works:
        dw = book.detected_works[0]
        if dw.series and not book.series:
            book.series = [SeriesRef(name=dw.series, sequence=dw.sequence)]
            book.provenance["series"] = Provenance.FILENAME.value


def resolve(book: BookUnit, evidence: Evidence) -> None:
    """Fill empty identity fields by precedence (embedded > datafile > directory >
    filename) via `reconcile`."""
    reconcile(
        book,
        embedded=evidence.embedded,
        sidecar=evidence.datafile,
        dir_title=book.source_folder.name,
        filename_fields=evidence.filename_fields,
        directory_fields=evidence.directory_fields,
    )


def normalize(book: BookUnit) -> None:
    """Clean a scan-derived title of query/display noise (year prefix, edition/format
    parentheticals). Only directory/filename-sourced titles — tag, datafile, and manual
    titles are left as-is. Idempotent."""
    if book.provenance.get("title") in {Provenance.DIRECTORY.value, Provenance.FILENAME.value}:
        cleaned = clean_match_title(book.title)
        if cleaned and cleaned != book.title:
            book.title = cleaned  # provenance unchanged


def attribute(book: BookUnit, evidence: Evidence) -> None:
    """Post-resolve structural attribution from the folder/cluster context."""
    # Untagged single book whose folder is the author, not the title: promote the
    # filename label to title and the folder name to author. Conservative.
    if book.content_kind is ContentKind.SINGLE and book.detected_works and evidence.first_path:
        dw = book.detected_works[0]
        folder_name = book.source_folder.name
        if (
            dw.label
            and not shares_token(folder_name, dw.label)
            and shares_token(evidence.first_path.stem, dw.label)
            and book.title == folder_name
            and not (evidence.embedded and evidence.embedded.artist)
            and not evidence.directory_fields.get("author")
        ):
            book.title = dw.label
            book.provenance["title"] = Provenance.FILENAME.value
            if not book.authors:
                book.authors = [folder_name]
                book.provenance["authors"] = Provenance.FILENAME.value

    # Foster container: a multi/loose folder we will split before matching. The folder
    # itself is the author (unless it is a title folder).
    if (
        not book.authors
        and book.folder_kind is not FolderKind.TITLE
        and book.detected_works
        and any(f.code in RESTRUCTURE_FINDINGS for f in book.findings)
    ):
        book.authors = [book.source_folder.name]
        book.provenance["authors"] = Provenance.DIRECTORY.value


def run_identify(
    book: BookUnit, *, root: Path, pattern: Pattern[str], scheme: list[Pattern[str]],
    rederive: bool = False,
) -> None:
    """Run the IDENTIFY pipeline for `book`, mutating it in place. `rederive` (Refresh)
    first drops fields orphaned by a removed/vetted datafile so they re-derive."""
    evidence = gather(book, root=root, pattern=pattern, scheme=scheme)
    if rederive:
        drop_orphaned_datafile_fields(book, evidence)
    seed_series(book)
    resolve(book, evidence)
    attribute(book, evidence)
    normalize(book)
    logger.debug(
        f"scan {book.source_folder}: IDENTIFY title={book.title!r}"
        f"({book.provenance.get('title')}) authors={book.authors}"
        f"({book.provenance.get('authors')}) "
        f"series={[s.name for s in book.series]}"
    )
