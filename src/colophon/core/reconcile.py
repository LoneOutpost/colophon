"""Merge embedded-tag, sidecar, directory, and filename evidence into a BookUnit.

Precedence per spec §6/FR-2.1: embedded tags > sidecar > directory inference >
filename. Each populated field records its winning source in `book.provenance`.
NOTE: description/genre/publisher/language beyond what is set here remain for
later plans; this reconciles the identity-bearing fields plus sidecar extras.
"""

from __future__ import annotations

from colophon.adapters.sidecar import SidecarMetadata
from colophon.core.coerce import to_float, to_int
from colophon.core.isbn import normalize_isbn
from colophon.core.models import BookUnit, EmbeddedTags, Provenance, SeriesRef
from colophon.core.people import split_people


def _split_people(value: str) -> list[str]:
    """Split a delimited people string from an ID3 frame into individual names.
    Delegates to the shared, conservative splitter (keeps 'Last, First')."""
    return split_people(value)


def _first[T](candidates: list[tuple[T, Provenance]]) -> tuple[T, str] | None:
    """The first `(value, provenance)` whose value is truthy — the list order is the
    precedence ladder — returned with provenance as its stored string value; `None`
    when every candidate is empty.

    Only for fields whose per-tier value is truthy exactly when its source is present.
    Fields with a transform that can null a present source (`authors`/`narrators` via
    `_split_people`, `isbn` via `normalize_isbn`) or with non-truthy emptiness
    (`publish_year` uses `is None`, so year 0 counts as present) keep their explicit
    ladders below, so this never silently changes their precedence."""
    for value, prov in candidates:
        if value:
            return value, prov.value
    return None


def reconcile(
    book: BookUnit,
    *,
    embedded: EmbeddedTags,
    sidecar: SidecarMetadata | None = None,
    dir_title: str | None,
    filename_fields: dict[str, str],
    directory_fields: dict[str, str] | None = None,
) -> None:
    """Populate `book`'s candidate fields and provenance in place."""
    sc = sidecar  # alias for brevity
    dirf = directory_fields or {}

    # title: embedded.title -> embedded.album -> sidecar -> directory -> filename
    if not book.title:
        picked = _first([
            (embedded.title, Provenance.TAG),
            (embedded.album, Provenance.TAG),
            (sc.title if sc else None, Provenance.SIDECAR),
            (dir_title, Provenance.DIRECTORY),
            (filename_fields.get("title"), Provenance.FILENAME),
        ])
        if picked:
            book.title, book.provenance["title"] = picked

    # subtitle: embedded has none -> sidecar
    if not book.subtitle and sc and sc.subtitle:
        book.subtitle, book.provenance["subtitle"] = sc.subtitle, Provenance.SIDECAR.value

    # authors: embedded.artist -> sidecar -> filename
    if not book.authors:
        if embedded.artist:
            book.authors, book.provenance["authors"] = _split_people(embedded.artist), Provenance.TAG.value
        elif sc and sc.authors:
            book.authors, book.provenance["authors"] = list(sc.authors), Provenance.SIDECAR.value
        elif dirf.get("author"):
            book.authors, book.provenance["authors"] = [dirf["author"]], Provenance.DIRECTORY.value
        elif filename_fields.get("author"):
            book.authors = [filename_fields["author"]]
            book.provenance["authors"] = Provenance.FILENAME.value

    # narrators: embedded.narrator -> sidecar -> filename
    if not book.narrators:
        if embedded.narrator:
            book.narrators, book.provenance["narrators"] = _split_people(embedded.narrator), Provenance.TAG.value
        elif sc and sc.narrators:
            book.narrators, book.provenance["narrators"] = list(sc.narrators), Provenance.SIDECAR.value
        elif dirf.get("narrator"):
            book.narrators = [dirf["narrator"]]
            book.provenance["narrators"] = Provenance.DIRECTORY.value
        elif filename_fields.get("narrator"):
            book.narrators = [filename_fields["narrator"]]
            book.provenance["narrators"] = Provenance.FILENAME.value

    # series: embedded -> sidecar -> directory -> filename (each tier builds a
    # non-empty [SeriesRef], so a present source always yields a truthy value)
    if not book.series:
        picked = _first([
            ([SeriesRef(name=embedded.series, sequence=embedded.sequence)]
             if embedded.series else None, Provenance.TAG),
            ([SeriesRef(name=sc.series_name, sequence=sc.series_sequence)]
             if sc and sc.series_name else None, Provenance.SIDECAR),
            ([SeriesRef(name=dirf["series"], sequence=to_float(dirf.get("sequence")))]
             if dirf.get("series") else None, Provenance.DIRECTORY),
            ([SeriesRef(name=filename_fields["series"],
                        sequence=to_float(filename_fields.get("sequence")))]
             if filename_fields.get("series") else None, Provenance.FILENAME),
        ])
        if picked:
            book.series, book.provenance["series"] = picked

    # publish_year: embedded -> sidecar -> filename
    if book.publish_year is None:
        if embedded.year is not None:
            book.publish_year, book.provenance["publish_year"] = embedded.year, Provenance.TAG.value
        elif sc and sc.publish_year is not None:
            book.publish_year, book.provenance["publish_year"] = sc.publish_year, Provenance.SIDECAR.value
        elif dirf.get("year"):
            year = to_int(dirf["year"])
            if year is not None:
                book.publish_year = year
                book.provenance["publish_year"] = Provenance.DIRECTORY.value
        elif filename_fields.get("year"):
            year = to_int(filename_fields["year"])
            if year is not None:
                book.publish_year = year
                book.provenance["publish_year"] = Provenance.FILENAME.value

    # publisher: embedded has none -> sidecar
    if not book.publisher and sc and sc.publisher:
        book.publisher, book.provenance["publisher"] = sc.publisher, Provenance.SIDECAR.value

    # description: embedded -> sidecar
    if not book.description:
        picked = _first([
            (embedded.description, Provenance.TAG),
            (sc.description if sc else None, Provenance.SIDECAR),
        ])
        if picked:
            book.description, book.provenance["description"] = picked

    # asin: embedded -> sidecar
    if not book.asin:
        picked = _first([
            (embedded.asin, Provenance.TAG),
            (sc.asin if sc else None, Provenance.SIDECAR),
        ])
        if picked:
            book.asin, book.provenance["asin"] = picked

    # isbn: embedded -> sidecar (normalized)
    if not book.isbn:
        if embedded.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(embedded.isbn), Provenance.TAG.value
        elif sc and sc.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(sc.isbn), Provenance.SIDECAR.value
