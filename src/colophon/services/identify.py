"""IDENTIFY pipeline: resolve a book's identity from gathered evidence.

Decomposed into named, single-purpose steps so each is testable in isolation and
the IDENTIFY phase stays atomic:
  gather (read + vet)
  -> identify_hard  (seed_title + reconcile hard tiers: embedded + datafile)
  -> identify_weak  (seed_series + reconcile weak tiers: directory + filename, then attribute + normalize)

`attribute` runs before `normalize` so its "title still equals the folder name" guard
sees the un-normalized title; `normalize` then also cleans any label `attribute` promoted
(it is idempotent). `run_identify` composes identify_hard + identify_weak for the
non-scan path. `_run_local` calls `run_identify`.
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
    WEAK_PROV,
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
# empty value it resets to. Mirrors core/reconcile.py's datafile branches.
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
    book: BookUnit, *, root: Path, pattern: Pattern[str], scheme: list[Pattern[str]],
    multi_folder: bool = False,
) -> Evidence:
    """Read all identity evidence for `book` and vet it: drop a datafile sidecar that
    describes a container rather than a book. `multi_folder` marks a book that is one of several
    sharing its folder (a split leaf), so a folder-level container datafile is rejected for it too."""
    first_path = book.source_files[0].path if book.source_files else None
    embedded = read_audio_metadata(first_path)[1] if first_path else None  # cache hit from SEARCH
    filename_fields = parse_filename(pattern, first_path.name) if first_path else {}
    datafile = read_datafile_sidecar(book.source_folder)
    if datafile is not None and is_container_datafile(
        datafile, book.source_folder, book.content_kind, multi_folder=multi_folder
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


def seed_title(book: BookUnit) -> None:
    """Pre-resolve: a multi-file single book is one book's chapters — the shared Album is its title,
    but each file's Title tag is a chapter. `gather` reads only the first file's tags and reconcile
    ranks Title above Album, so without this the book would be titled after chapter one. The detected
    work already read the Album (see `classify._to_work`), so seed its label when it is tag-sourced;
    reconcile's `if not book.title` gate then keeps it."""
    if (
        book.content_kind is ContentKind.SINGLE
        and len(book.source_files) > 1
        and not book.title
        and book.detected_works
    ):
        dw = book.detected_works[0]
        if dw.label and dw.label_prov == Provenance.TAG.value:
            book.title = dw.label
            book.provenance["title"] = Provenance.TAG.value


def _reconcile_from(book: BookUnit, evidence: Evidence, *, tiers: str = "all") -> None:
    """Fill empty identity fields by precedence via `reconcile`. The folder name is parsed
    so a `YEAR -` prefix and a `read by …` narrator fill their own fields, not the title.
    `tiers` is forwarded to `reconcile` to restrict which precedence tiers are consulted:
    ``"hard"`` for embedded + datafile only; ``"weak"`` for directory + filename only;
    ``"all"`` (default) for all tiers."""
    from colophon.core.folder_title import parse_folder_title

    parsed = parse_folder_title(book.source_folder.name)
    dirf = dict(evidence.directory_fields)
    if parsed.year is not None:
        dirf.setdefault("year", str(parsed.year))
    reconcile(
        book,
        embedded=evidence.embedded,
        datafile=evidence.datafile,
        dir_title=parsed.title,
        filename_fields=evidence.filename_fields,
        directory_fields=dirf,
        dir_narrators=parsed.narrators,
        tiers=tiers,
    )


def identify_hard(
    book: BookUnit, *, root: Path, pattern: Pattern[str], scheme: list[Pattern[str]],
    multi_folder: bool = False,
) -> Evidence:
    """First IDENTIFY stage: gather evidence, drop orphaned datafile fields, seed the
    album-as-title for multi-file books, then fill only the hard-sourced identity tiers
    (embedded tags, datafile sidecar). Returns the gathered Evidence so the weak stage can
    reuse it without re-reading files."""
    evidence = gather(book, root=root, pattern=pattern, scheme=scheme, multi_folder=multi_folder)
    drop_orphaned_datafile_fields(book, evidence)
    seed_title(book)  # album tag -> title, hard
    _reconcile_from(book, evidence, tiers="hard")
    return evidence


def identify_weak(book: BookUnit, evidence: Evidence, *, role: str | None = None) -> None:
    """Second IDENTIFY stage: seed series from the filename cluster, fill remaining empty
    identity from the weak tiers (directory decompose, filename), then attribute structural
    fields and normalize. `role` is a graph node.kind ("title", "author", "series", …) that
    drives role-specific attribution; role=None keeps the legacy folder_kind heuristic."""
    seed_series(book)  # filename cluster series, weak
    _reconcile_from(book, evidence, tiers="weak")
    attribute(book, evidence, role=role)
    normalize(book)


def normalize(book: BookUnit) -> None:
    """Clean a scan-derived title of display noise (edition/format parentheticals) and a strong
    sequence-number affix ('05 - Phoenix' -> 'Phoenix'), and de-shout a shouting title/author
    ('DARKSABER' -> 'Darksaber', 'SANDRA BROWN' -> 'Sandra Brown'). Title *cleaning* is gated to
    directory/filename provenance, but the title and author *de-shout* apply to any non-manual source
    (ALL CAPS is never an intentional spelling; a lone-acronym author like 'BBC' is preserved, though
    a lone-acronym title is not). A leading year prefix is NOT stripped
    here (unlike the match-query path): a leading 4-digit number is ambiguous with a numeric title,
    so the persisted title keeps it rather than risk deleting real content. A weak (unspaced) title
    affix like '30-Day Heart Tune-Up' is left for the corroborated series-ramp path. Idempotent."""
    from colophon.core.normalize import proper_case_if_shouting
    from colophon.core.sequence_affix import parse_sequence_affix
    if book.provenance.get("title") in WEAK_PROV:
        cleaned = clean_match_title(book.title, strip_year=False)
        if cleaned and cleaned != book.title:
            book.title = cleaned  # provenance unchanged
        affix = parse_sequence_affix(book.title or "")
        if affix is not None and affix.confidence == "strong" and affix.cleaned != book.title:
            book.title = affix.cleaned  # provenance unchanged
    # De-shout a shouting title from any non-manual source (the multi-book path already does this in
    # `classify._to_work`; this covers the single-book reconcile path). Single tokens are de-shouted
    # too ('DARKSABER' -> 'Darksaber'): a lone-acronym title is far rarer than an acronym author.
    if book.title and book.provenance.get("title") != Provenance.MANUAL.value:
        book.title = proper_case_if_shouting(book.title)
    # De-shout a shouting author from any source except a manual edit: ALL CAPS is never an
    # intentional spelling for a name. A single all-caps token is kept as a likely acronym (BBC).
    if book.authors and book.provenance.get("authors") != Provenance.MANUAL.value:
        book.authors = [proper_case_if_shouting(a, keep_acronyms=True) for a in book.authors]


def _attribute_legacy(book: BookUnit, evidence: Evidence) -> None:
    """Post-resolve structural attribution from the folder/cluster context."""
    # Untagged single book whose folder is the author, not the title: promote the
    # filename label to title and the folder name to author. Conservative.
    if (book.content_kind is ContentKind.SINGLE and book.detected_works and evidence.first_path
            and book.folder_kind is not FolderKind.TITLE):
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


def _author_folder_title(book: BookUnit, label: str | None) -> str | None:
    """The title for a lone book sitting in an author folder: its own filename, not the folder echo.
    Prefer the cluster label; but when the current title is only the folder name echoed back by
    directory inference (the template parser produced no title, so the label is a truncation artifact
    like '$Author - $Title' on 'weird_name_no_delimiters' -> 'weird'), use the full spaced filename
    stem — the whole filename is the title. Skips a stem that is just the folder name or a bare track
    number (no real words identifies no title)."""
    from colophon.core.filename_cluster import _spaced, _text_sig, _tokens
    label = (label or "").strip()
    folder_echo = book.provenance.get("title") == Provenance.DIRECTORY.value
    if folder_echo:
        chosen = _spaced(book.source_files[0].path.stem.replace("_", " "))
    else:
        chosen = label
    if not chosen or not _text_sig(_tokens(chosen)):   # no real words (a bare "01")
        return None
    if chosen.casefold() == book.source_folder.name.casefold():
        return None
    return chosen


def attribute(book: BookUnit, evidence: Evidence, *, role: str | None = None) -> None:
    """Post-classification structural attribution, driven by the folder's classified `role`
    (a graph node.kind). role=None keeps the legacy folder_kind heuristic for the non-scan path."""
    if role is None:
        _attribute_legacy(book, evidence)
        return
    if role == "title":
        return  # folder is the title; its decomposed name already filled title/year/narrator. No author.
    if role == "author":
        # folder names the author; the book's own title comes from its filename, not the folder echo.
        if book.content_kind is ContentKind.SINGLE and book.detected_works and book.source_files:
            dw = book.detected_works[0]
            if not book.title or book.provenance.get("title") in WEAK_PROV:
                new_title = _author_folder_title(book, dw.label)
                if new_title:
                    book.title, book.provenance["title"] = new_title, Provenance.FILENAME.value
        if not book.authors:
            book.authors, book.provenance["authors"] = [book.source_folder.name], Provenance.DIRECTORY.value
        return
    # series / franchise / container: no name promotion here.
    return


def run_identify(
    book: BookUnit, *, root: Path, pattern: Pattern[str], scheme: list[Pattern[str]],
    multi_folder: bool = False,
) -> None:
    """Single-pass IDENTIFY for the non-scan path: hard then weak, no classification
    between. Fields orphaned by a removed/vetted datafile are always dropped so they
    re-derive — the datafile sidecar being gone is the trigger, not the scan mode.
    `multi_folder` flags a split leaf so a folder-level container datafile is not adopted
    as its own."""
    evidence = identify_hard(book, root=root, pattern=pattern, scheme=scheme,
                             multi_folder=multi_folder)
    identify_weak(book, evidence, role=None)
    logger.debug(
        f"scan {book.source_folder}: IDENTIFY title={book.title!r}"
        f"({book.provenance.get('title')}) authors={book.authors}"
        f"({book.provenance.get('authors')}) "
        f"series={[s.name for s in book.series]}"
    )
