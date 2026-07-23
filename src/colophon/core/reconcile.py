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


def _demote_numeric_author(filename_fields: dict[str, str]) -> dict[str, str]:
    """A filename-parsed `$Author` that is purely numeric is never a person (files named
    'YYYY - Title.mp3' under `$Author - $Title` mis-slot the year as the author). Drop such an
    author; when it is a bare 4-digit year and no filename year is set, move it to the year so it
    lands as the publish year. Returns a copy — the caller's dict is not mutated."""
    author = (filename_fields.get("author") or "").strip()
    if not author.isdigit():
        return filename_fields
    out = dict(filename_fields)
    del out["author"]
    if len(author) == 4 and not out.get("year"):
        out["year"] = author
    return out


def reconcile(
    book: BookUnit,
    *,
    embedded: EmbeddedTags,
    datafile: DatafileSidecar | None = None,
    dir_title: str | None,
    filename_fields: dict[str, str],
    directory_fields: dict[str, str] | None = None,
    dir_narrators: list[str] | None = None,
    tiers: str = "all",
) -> None:
    """Populate `book`'s candidate fields and provenance in place.

    `tiers` controls which precedence tiers are consulted:
      - ``"all"``  (default) — embedded + datafile + directory + filename (unchanged behaviour)
      - ``"hard"`` — embedded + datafile only
      - ``"weak"`` — directory + filename only
    """
    df = datafile  # alias for brevity
    dirf = directory_fields or {}
    filename_fields = _demote_numeric_author(filename_fields)

    hard = tiers in ("all", "hard")
    weak = tiers in ("all", "weak")

    # title: embedded.title -> embedded.album -> datafile -> directory -> filename
    if not book.title:
        candidates = []
        if hard:
            candidates += [
                (embedded.title, Provenance.TAG),
                (embedded.album, Provenance.TAG),
                (df.title if df else None, Provenance.DATAFILE),
            ]
        if weak:
            candidates += [
                (dir_title, Provenance.DIRECTORY),
                (filename_fields.get("title"), Provenance.FILENAME),
            ]
        picked = _first(candidates)
        if picked:
            book.title, book.provenance["title"] = picked

    # subtitle: embedded has none -> datafile (hard only)
    if hard and not book.subtitle and df and df.subtitle:
        book.subtitle, book.provenance["subtitle"] = df.subtitle, Provenance.DATAFILE.value

    # authors: embedded.artist -> datafile -> directory -> filename
    if not book.authors:
        if hard and embedded.artist:
            book.authors, book.provenance["authors"] = _split_people(embedded.artist), Provenance.TAG.value
        elif hard and df and df.authors:
            book.authors, book.provenance["authors"] = list(df.authors), Provenance.DATAFILE.value
        elif weak and dirf.get("author"):
            book.authors, book.provenance["authors"] = [dirf["author"]], Provenance.DIRECTORY.value
        elif weak and filename_fields.get("author"):
            book.authors = [filename_fields["author"]]
            book.provenance["authors"] = Provenance.FILENAME.value

    # narrators: embedded.narrator -> datafile -> directory -> filename
    if not book.narrators:
        if hard and embedded.narrator:
            book.narrators, book.provenance["narrators"] = _split_people(embedded.narrator), Provenance.TAG.value
        elif hard and df and df.narrators:
            book.narrators, book.provenance["narrators"] = list(df.narrators), Provenance.DATAFILE.value
        elif weak and dirf.get("narrator"):
            book.narrators = [dirf["narrator"]]
            book.provenance["narrators"] = Provenance.DIRECTORY.value
        elif weak and dir_narrators:
            book.narrators, book.provenance["narrators"] = list(dir_narrators), Provenance.DIRECTORY.value
        elif weak and filename_fields.get("narrator"):
            book.narrators = [filename_fields["narrator"]]
            book.provenance["narrators"] = Provenance.FILENAME.value

    # series: embedded -> datafile -> directory -> filename (each tier builds a
    # non-empty [SeriesRef], so a present source always yields a truthy value)
    if not book.series:
        candidates = []
        if hard:
            candidates += [
                ([SeriesRef(name=embedded.series, sequence=embedded.sequence)]
                 if embedded.series else None, Provenance.TAG),
                ([SeriesRef(name=df.series_name, sequence=df.series_sequence)]
                 if df and df.series_name else None, Provenance.DATAFILE),
            ]
        if weak:
            candidates += [
                ([SeriesRef(name=dirf["series"], sequence=to_float(dirf.get("sequence")))]
                 if dirf.get("series") else None, Provenance.DIRECTORY),
                ([SeriesRef(name=filename_fields["series"],
                            sequence=to_float(filename_fields.get("sequence")))]
                 if filename_fields.get("series") else None, Provenance.FILENAME),
            ]
        picked = _first(candidates)
        if picked:
            book.series, book.provenance["series"] = picked

    # publish_year: embedded -> datafile -> directory -> filename
    if book.publish_year is None:
        if hard and embedded.year is not None:
            book.publish_year, book.provenance["publish_year"] = embedded.year, Provenance.TAG.value
        elif hard and df and df.publish_year is not None:
            book.publish_year, book.provenance["publish_year"] = df.publish_year, Provenance.DATAFILE.value
        elif weak and dirf.get("year"):
            year = to_int(dirf["year"])
            if year is not None:
                book.publish_year = year
                book.provenance["publish_year"] = Provenance.DIRECTORY.value
        elif weak and filename_fields.get("year"):
            year = to_int(filename_fields["year"])
            if year is not None:
                book.publish_year = year
                book.provenance["publish_year"] = Provenance.FILENAME.value

    # publisher: embedded has none -> datafile (hard only)
    if hard and not book.publisher and df and df.publisher:
        book.publisher, book.provenance["publisher"] = df.publisher, Provenance.DATAFILE.value

    # description: embedded -> datafile (hard only)
    if not book.description:
        candidates = []
        if hard:
            candidates += [
                (embedded.description, Provenance.TAG),
                (df.description if df else None, Provenance.DATAFILE),
            ]
        picked = _first(candidates)
        if picked:
            book.description, book.provenance["description"] = picked

    # asin: embedded -> datafile (hard only)
    if not book.asin:
        candidates = []
        if hard:
            candidates += [
                (embedded.asin, Provenance.TAG),
                (df.asin if df else None, Provenance.DATAFILE),
            ]
        picked = _first(candidates)
        if picked:
            book.asin, book.provenance["asin"] = picked

    # isbn: embedded -> datafile (normalized, hard only)
    if not book.isbn:
        if hard and embedded.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(embedded.isbn), Provenance.TAG.value
        elif hard and df and df.isbn:
            book.isbn, book.provenance["isbn"] = normalize_isbn(df.isbn), Provenance.DATAFILE.value
