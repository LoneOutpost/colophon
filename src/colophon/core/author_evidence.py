"""Author field resolution from a weighted-evidence ballot (spec: evidence-resolve, author first).

`collect_author_evidence` turns the soft signals into `FieldEvidence` votes; `resolve_author` resolves
the winner and stamps it onto the book with the winning source's provenance. Junk-shaped candidates
(title-shaped authors, blanks) are penalized to zero weight rather than vetoed. A manual/match author
is authoritative and settles (never overturned)."""

from __future__ import annotations

from colophon.core import evidence_weights as W
from colophon.core.field_resolve import FieldEvidence, ResolvedField, resolve_field
from colophon.core.metadata_quality import is_structural_marker, is_title_shaped_author
from colophon.core.models import BookUnit, Provenance
from colophon.core.people import split_people

# Authoritative provenances that SETTLE the author (never overturned): a user's manual edit + any
# online-source match. Deliberately EXCLUDES tag/datafile, those are overturnable soft votes.
_MATCH_PROV = frozenset({
    Provenance.AUDNEXUS.value, Provenance.AUDIBLE.value, Provenance.HARDCOVER.value,
    Provenance.OPENLIBRARY.value, Provenance.GOOGLEBOOKS.value,
})
_SETTLE_PROV = _MATCH_PROV | {Provenance.MANUAL.value}


def _penalized(value: str, weight: float) -> float:
    """Junk-shaped author -> ~0 weight (kept in the ballot for the readout, out of the tally). A
    structural marker ("Chapter", "Track 3") is a per-file position, never an author."""
    return 0.0 if (not value or not value.strip()
                   or is_title_shaped_author(value) or is_structural_marker(value)) else weight


def collect_author_evidence(
    book: BookUnit, *, author_depth_folder: str | None, classified_author_name: str | None,
    datafile_authors: list[str], filename_author: str | None,
    sibling_consensus: dict[str, int],
) -> list[FieldEvidence]:
    """Assemble the author ballot from the SOFT sources. `sibling_consensus` maps an author value to
    the count of sibling books independently asserting it (their own tag/match, never the shared
    folder). A manual/match author is handled by `resolve_author`'s settle-guard, not here."""
    ev: list[FieldEvidence] = []

    tags = book.source_files[0].tags if book.source_files else None
    artist = tags.artist if tags else None
    prov = book.provenance.get("authors")
    if artist:
        ev.append(FieldEvidence(artist, _penalized(artist, W.W_A_TAG), "tag", f"tag artist '{artist}'"))
    elif book.authors and prov in (Provenance.TAG.value, Provenance.DATAFILE.value):
        # No cached file tag on this in-memory SourceFile, but reconcile committed the file's author
        # with tag/datafile provenance — recover it so the ballot weighs it as an overturnable vote.
        src = "tag" if prov == Provenance.TAG.value else "datafile"
        w = W.W_A_TAG if src == "tag" else W.W_A_DATAFILE
        val = " & ".join(book.authors)
        ev.append(FieldEvidence(val, _penalized(val, w), src, f"{src} author '{val}'"))

    if datafile_authors:
        joined = " & ".join(datafile_authors)
        ev.append(FieldEvidence(joined, _penalized(joined, W.W_A_DATAFILE), "datafile",
                                f"datafile author '{joined}'"))

    if classified_author_name:
        ev.append(FieldEvidence(classified_author_name,
                                _penalized(classified_author_name, W.W_A_FOLDER),
                                "folder", f"classified author node '{classified_author_name}'"))
    elif author_depth_folder:
        ev.append(FieldEvidence(author_depth_folder, _penalized(author_depth_folder, W.W_A_FOLDER),
                                "folder", f"author-depth folder '{author_depth_folder}'"))

    if filename_author:
        ev.append(FieldEvidence(filename_author, _penalized(filename_author, W.W_A_FILENAME),
                                "filename", f"filename $Author '{filename_author}'"))

    for value, n in sibling_consensus.items():
        w = min(W.W_A_CONSENSUS_MAX, W.W_A_CONSENSUS_BASE + W.W_A_CONSENSUS_STEP * n)
        ev.append(FieldEvidence(value, _penalized(value, w), "sibling",
                                f"{n} sibling(s) assert '{value}'"))
    return ev


# winning soft source label -> stored provenance value
_PROV_FOR = {
    "tag": Provenance.TAG.value, "datafile": Provenance.DATAFILE.value,
    "folder": Provenance.DIRECTORY.value, "filename": Provenance.FILENAME.value,
    "sibling": Provenance.GRAPHING.value,
}


def resolve_author(book: BookUnit, **inputs) -> ResolvedField:
    """Resolve the author and stamp the winner onto `book` (value + provenance). A manual/match author
    SETTLES, kept untouched. Otherwise collect the soft ballot and resolve; an empty ballot leaves the
    current author as-is. Returns the ResolvedField (value + likelihood + evidence) for a later slice."""
    if book.authors and book.provenance.get("authors") in _SETTLE_PROV:
        return ResolvedField(book.authors[0], 1.0, [])
    candidates = collect_author_evidence(book, **inputs)
    r = resolve_field(candidates)
    if r.value is None:
        return r
    book.authors = split_people(r.value)
    book.provenance["authors"] = _PROV_FOR.get(r.source, Provenance.GRAPHING.value)
    return r
