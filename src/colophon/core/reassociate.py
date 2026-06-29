"""Identity re-association: match newly-projected books to existing persisted books
by owned-file overlap, so a book keeps its id and state across re-scans even when its
file set churns. Pure — no I/O. The match key is content (owned files), never the
semantic tiers (author/series/franchise), so reclassification never churns identity."""

from __future__ import annotations

from pathlib import Path

from colophon.core.models import BookUnit


def _fingerprint(book: BookUnit) -> set[tuple[str, int, int]]:
    """The owned-file fingerprint: {(name, size, rounded duration)} per audio file.
    All three fields must match for two files to count as shared, so a single renamed
    file produces no shared tuple with its prior counterpart; size and duration are the
    durable, content-bound fields that carry overlap when a sibling file is also present.
    Read off `source_files` — no probing."""
    return {
        (sf.path.name, sf.size, round(sf.duration_seconds)) for sf in book.source_files
    }


def reassociate(
    projected: list[BookUnit], existing: list[BookUnit]
) -> list[tuple[BookUnit, BookUnit | None]]:
    """Match each projected book to at most one existing book by owned-file overlap.

    Greedy 1:1: among all (projected, existing) pairs sharing >=1 file, assign by
    descending Jaccard overlap (tie-break: more shared files, then input order), each
    book claimed once. Returns one (projected, matched|None) per projected book, in the
    input order of `projected`."""
    pfp = {id(p): _fingerprint(p) for p in projected}
    efp = {id(e): _fingerprint(e) for e in existing}

    # Built projected-major, existing-minor; a stable sort then breaks score/shared ties
    # by that original append order (earliest projected, then earliest existing).
    scored: list[tuple[float, int, BookUnit, BookUnit]] = []
    for p in projected:
        for e in existing:
            shared = len(pfp[id(p)] & efp[id(e)])
            if shared == 0:
                continue
            union = len(pfp[id(p)] | efp[id(e)])  # shared >= 1 guarantees union >= 1
            scored.append((shared / union, shared, p, e))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)  # stable: equal keys keep order

    matched_for: dict[int, BookUnit] = {}
    claimed_existing: set[int] = set()
    claimed_projected: set[int] = set()
    for _score, _shared, p, e in scored:
        if id(p) in claimed_projected or id(e) in claimed_existing:
            continue
        matched_for[id(p)] = e
        claimed_projected.add(id(p))
        claimed_existing.add(id(e))
    return [(p, matched_for.get(id(p))) for p in projected]


def is_missing(book: BookUnit, *, root_accessible: bool) -> bool:
    """A tracked book is missing when its scan root is reachable but its own source
    folder is gone, and it is not yet organized (an organized book's source is meant
    to be gone — its canonical artifact is the output). `root_accessible` is the unmount
    guard: a whole root falling offline must never flag every book under it as missing."""
    if not root_accessible:
        return False
    if book.output_path is not None:
        return False
    return not Path(book.source_folder).exists()
