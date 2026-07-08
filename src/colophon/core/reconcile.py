"""Merge embedded-tag, datafile sidecar, directory, and filename evidence into a BookUnit.

Precedence per spec §6/FR-2.1: embedded tags > datafile sidecar > directory inference >
filename. Each populated field records its winning source in `book.provenance`.
NOTE: description/genre/publisher/language beyond what is set here remain for
later plans; this reconciles the identity-bearing fields plus datafile sidecar extras.
"""

from __future__ import annotations

from colophon.adapters.sidecar import DatafileSidecar
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
    datafile: DatafileSidecar | None = None,
    dir_title: str | None,
    filename_fields: dict[str, str],
    directory_fields: dict[str, str] | None = None,
) -> None:
    """Populate `book`'s candidate fields and provenance in place."""
    df = datafile  # alias for brevity
    dirf = directory_fields or {}

    # title: embedded.title -> embedded.album -> datafile -> directory -> filename
    if not book.title:
        picked = _first([
            (embedded.title, Provenance.TAG),
            (embedded.album, Provenance.TAG),
            (df.title if df else None, Provenance.DATAFILE),
            (dir_title, Provenance.DIRECTORY),
            (filename_fields.get("title"), Provenance.FILENAME),
        ])
        if picked:
            book.title, book.provenance["title"] = picked

    # subtitle: embedded has none -> datafile
    if not book.subtitle and df and df.subtitle:
        book.subtitle, book.provenance["subtitle"] = df.subtitle, Provenance.DATAFILE.value

    # authors: embedded.artist -> datafile -> filename
    if not book.authors:
        if embedded.artist:
            book.authors, book.provenance["authors"] = _split_people(embedded.artist), Provenance.TAG.value
        elif df and df.authors:
            book.authors, book.provenance["authors"] = list(df.authors), Provenance.DATAFILE.value
        elif dirf.get("author"):
            book.authors, book.provenance["authors"] = [dirf["author"]], Provenance.DIRECTORY.value
        elif filename_fields.get("author"):
            book.authors = [filename_fields["author"]]
            book.provenance["authors"] = Provenance.FILENAME.value

    # narrators: embedded.narrator -> datafile -> filename
    if not book.narrators:
        if embedded.narrator:
            book.narrators, book.provenance["narrators"] = _split_people(embedded.narrator), Provenance.TAG.value
        elif df and df.narrators:
            book.narrators, book.provenance["narrators"] = list(df.narrators), Provenance.DATAFILE.value
        elif dirf.get("narrator"):
            book.narrators = [dirf["narrator"]]
            book.provenance["narrators"] = Provenance.DIRECTORY.value
        elif filename_fields.get("narrator"):
            book.narrators = [filename_fields["narrator"]]
            book.provenance["narrators"] = Provenance.FILENAME.value

    # series: embedded -> datafile -> directory -> filename (each tier builds a
    # non-empty [SeriesRef], so a present source always yields a truthy value)
    if not book.series:
        picked = _first([
            ([SeriesRef(name=embedded.series, sequence=embedded.sequence)]
             if embedded.series else None, Provenance.TAG),
            ([SeriesRef(name=df.series_name, sequence=df.series_sequence)]
             if df and df.series_name else None, Provenance.DATAFILE),
            ([SeriesRef(name=dirf["series"], sequence=to_float(dirf.get("sequence")))]
             if dirf.get("series") else None, Provenance.DIRECTORY),
            ([SeriesRef(name=filename_fields["series"],
                        sequence=to_float(filename_fields.get("sequence")))]
             if filename_fields.get("series") else None, Provenance.FILENAME),
        ])
        if picked:
            book.series, book.provenance["series"] = picked

    # publish_year: embedded -> datafile -> filename
    if book.publish_year is None:
        if embedded.year is not None:
            book.publish_year, book.provenance["publish_year"] = embedded.year, Provenance.TAG.value
        elif df and df.publish_year is not None:
            book.publish_year, book.provenance["publish_year"] = df.publish_year, Provenance.DATAFILE.value
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

    # publisher: embedded has none -> datafile
    if not book.publisher and df and df.publisher:
        book.publisher, book.provenance["publisher"] = df.publisher, Provenance.DATAFILE.value

    # description: embedded -> datafile
    if not book.description:
        picked = _first([
            (embedded.description, Provenance.TAG),
            (df.description if df else None, Provenance.DATAFILE),
        ])
        if picked:
            book.description, book.provenance["description"] = picked

    # asin: embedded -> datafile
    if not book.asin:
        picked = _first([
            (embedded.asin, Provenance.TAG),
            (df.asin if df else None, Provenance.DATAFILE),
        ])
        if picked:
            book.asin, book.provenance["asin"] = picked

    # isbn: embedded -> datafile (normalized)
    if not book.isbn:
        if embedded.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(embedded.isbn), Provenance.TAG.value
        elif df and df.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(df.isbn), Provenance.DATAFILE.value
