"""Author field resolution from a weighted-evidence ballot (spec: evidence-resolve, author first).

`collect_author_evidence` turns the soft signals into `FieldEvidence` votes; `resolve_author` resolves
the winner and stamps it onto the book with the winning source's provenance. Junk-shaped candidates
(title-shaped authors, blanks) are penalized to zero weight rather than vetoed. A manual/match author
is authoritative and settles (never overturned)."""

from __future__ import annotations

from collections import Counter

from colophon.core import evidence_weights as W
from colophon.core.field_resolve import FieldEvidence, ResolvedField, resolve_field
from colophon.core.metadata_quality import author_junk
from colophon.core.models import AUTHORITATIVE_PROV, BookUnit, Provenance
from colophon.core.normalize import normalize_author
from colophon.core.people import split_people

# Authoritative provenances that SETTLE the author (never overturned): a user's manual edit + any
# online-source match. Deliberately EXCLUDES tag/datafile, those are overturnable soft votes.
_SETTLE_PROV = AUTHORITATIVE_PROV


def _penalized(value: str, weight: float) -> float:
    """Junk-shaped author -> its weight scaled down by the intrinsic author-junk magnitude (scalar, not
    a veto). A structural marker ("Chapter", "Track 3"), a title-shaped author, a `.-.`-spanning
    `Author.-.Title` string, or an embedded `N of M` fragment sheds most/all of its weight; a clean
    name keeps its full weight."""
    return weight * (1.0 - author_junk(value))


def collect_author_evidence(
    book: BookUnit, *, author_depth_folder: str | None, classified_author_name: str | None,
    datafile_authors: list[str], filename_author: str | None,
    sibling_consensus: dict[str, int], classified_author_confidence: float = 1.0,
    leaf_folder_author: str | None = None,
) -> list[FieldEvidence]:
    """Assemble the author ballot from the SOFT sources. `sibling_consensus` maps an author value to
    the count of sibling books independently asserting it (their own tag/match, never the shared
    folder). A manual/match author is handled by `resolve_author`'s settle-guard, not here."""
    ev: list[FieldEvidence] = []

    # Aggregate the artist tag across EVERY grouped file, not just the first: agreement is
    # corroboration. Each distinct artist votes once, its weight rising with the number of files that
    # assert it (capped) — so a book whose 9 files all say the same author reinforces it.
    prov = book.provenance.get("authors")
    artist_counts: Counter[str] = Counter(
        normalize_author(sf.tags.artist)
        for sf in book.source_files if sf.tags and sf.tags.artist
    )
    if artist_counts:
        for a, n in artist_counts.items():
            w = min(W.W_A_TAG_MAX, W.W_A_TAG + W.W_A_TAG_STEP * (n - 1))
            ev.append(FieldEvidence(a, _penalized(a, w), "tag",
                                    f"tag artist '{a}' ({n} file{'s' if n != 1 else ''})"))
    elif book.authors and prov in (Provenance.TAG.value, Provenance.DATAFILE.value):
        # No cached file tag on this in-memory SourceFile, but reconcile committed the file's author
        # with tag/datafile provenance — recover it so the ballot weighs it as an overturnable vote.
        src = "tag" if prov == Provenance.TAG.value else "datafile"
        w = W.W_A_TAG if src == "tag" else W.W_A_DATAFILE
        val = normalize_author(" & ".join(book.authors))
        ev.append(FieldEvidence(val, _penalized(val, w), src, f"{src} author '{val}'"))

    if datafile_authors:
        joined = normalize_author(" & ".join(datafile_authors))
        ev.append(FieldEvidence(joined, _penalized(joined, W.W_A_DATAFILE), "datafile",
                                f"datafile author '{joined}'"))

    if classified_author_name:
        c = normalize_author(classified_author_name)
        # A classified author node ALWAYS casts a ballot (floor W_A_FOLDER) and scales UP with its
        # classification strength to W_A_FOLDER_STRONG, so a confident, well-populated author folder
        # out-weighs a lone tag while a shaky node stays near the floor. Never a veto — still a vote.
        conf = max(0.0, min(1.0, classified_author_confidence))
        w = W.W_A_FOLDER + conf * (W.W_A_FOLDER_STRONG - W.W_A_FOLDER)
        ev.append(FieldEvidence(c, _penalized(c, w),
                                "folder", f"classified author node '{c}' (conf {conf:.2f})"))
    elif author_depth_folder:
        f = normalize_author(author_depth_folder)
        ev.append(FieldEvidence(f, _penalized(f, W.W_A_FOLDER),
                                "folder", f"author-depth folder '{f}'"))

    if leaf_folder_author:
        # The book's OWN leaf folder declares its author via the `.-.` convention
        # (`Author.-.[Series.-.]Title`). This is the author the depth logic misses when there is no
        # dedicated author-node ancestor (the author lives inside the leaf name). Weighted above a
        # bucket-depth folder guess and a filename $Author parse, but below a real tag — a ballot, not a
        # veto, so a tag/match/manual author still wins.
        lf = normalize_author(leaf_folder_author)
        ev.append(FieldEvidence(lf, _penalized(lf, W.W_A_LEAF_FOLDER),
                                "folder", f"leaf '.-.' folder author '{lf}'"))

    if filename_author:
        fn = normalize_author(filename_author)
        ev.append(FieldEvidence(fn, _penalized(fn, W.W_A_FILENAME),
                                "filename", f"filename $Author '{fn}'"))

    for value, n in sibling_consensus.items():
        v = normalize_author(value)
        w = min(W.W_A_CONSENSUS_MAX, W.W_A_CONSENSUS_BASE + W.W_A_CONSENSUS_STEP * n)
        ev.append(FieldEvidence(v, _penalized(v, w), "sibling",
                                f"{n} sibling(s) assert '{v}'"))
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
