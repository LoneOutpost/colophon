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


def _split_people(value: str) -> list[str]:
    """Split a comma-joined people string into individual names.

    Accepts the comma-separated form some taggers use for multiple
    authors/narrators in a single ID3 frame (e.g. "Terry Jones, Douglas Adams").
    Known tradeoff: a single name legitimately containing a comma (rare for
    audiobook authors) would be over-split.
    """
    return [part.strip() for part in value.split(",") if part.strip()]


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
        if embedded.title:
            book.title, book.provenance["title"] = embedded.title, Provenance.TAG.value
        elif embedded.album:
            book.title, book.provenance["title"] = embedded.album, Provenance.TAG.value
        elif sc and sc.title:
            book.title, book.provenance["title"] = sc.title, Provenance.SIDECAR.value
        elif dir_title:
            book.title, book.provenance["title"] = dir_title, Provenance.DIRECTORY.value
        elif filename_fields.get("title"):
            book.title = filename_fields["title"]
            book.provenance["title"] = Provenance.FILENAME.value

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
        elif filename_fields.get("narrator"):
            book.narrators = [filename_fields["narrator"]]
            book.provenance["narrators"] = Provenance.FILENAME.value

    # series: embedded -> sidecar -> filename
    if not book.series:
        if embedded.series:
            book.series = [SeriesRef(name=embedded.series, sequence=embedded.sequence)]
            book.provenance["series"] = Provenance.TAG.value
        elif sc and sc.series_name:
            book.series = [SeriesRef(name=sc.series_name, sequence=sc.series_sequence)]
            book.provenance["series"] = Provenance.SIDECAR.value
        elif dirf.get("series"):
            book.series = [SeriesRef(name=dirf["series"], sequence=None)]
            book.provenance["series"] = Provenance.DIRECTORY.value
        elif filename_fields.get("series"):
            book.series = [SeriesRef(name=filename_fields["series"], sequence=to_float(filename_fields.get("sequence")))]
            book.provenance["series"] = Provenance.FILENAME.value

    # publish_year: embedded -> sidecar -> filename
    if book.publish_year is None:
        if embedded.year is not None:
            book.publish_year, book.provenance["publish_year"] = embedded.year, Provenance.TAG.value
        elif sc and sc.publish_year is not None:
            book.publish_year, book.provenance["publish_year"] = sc.publish_year, Provenance.SIDECAR.value
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
        if embedded.description:
            book.description, book.provenance["description"] = embedded.description, Provenance.TAG.value
        elif sc and sc.description:
            book.description, book.provenance["description"] = sc.description, Provenance.SIDECAR.value

    # asin: embedded -> sidecar
    if not book.asin:
        if embedded.asin:
            book.asin, book.provenance["asin"] = embedded.asin, Provenance.TAG.value
        elif sc and sc.asin:
            book.asin, book.provenance["asin"] = sc.asin, Provenance.SIDECAR.value

    # isbn: embedded -> sidecar (normalized)
    if not book.isbn:
        if embedded.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(embedded.isbn), Provenance.TAG.value
        elif sc and sc.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(sc.isbn), Provenance.SIDECAR.value
