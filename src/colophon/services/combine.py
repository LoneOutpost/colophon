"""Combine a folder's separate books into one multi-file book, and undo it.

When the scanner over-splits a folder (each file read as its own book), Combine merges
them into one book whose files become ordered chapters, and persists a grouping override so
the merge survives rescans. Uncombine restores the exact pre-combine books from a snapshot.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, GroupingOverrideRepo
from colophon.core.chapters import file_boundary_chapters
from colophon.core.models import BookUnit, ContentKind, DetectedWork, SourceFile


def _natural_key(name: str) -> list:
    """Sort key so '2' precedes '10' (numeric runs compare as numbers)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _primary(books: list[BookUnit]) -> BookUnit:
    """The book whose metadata survives: highest local-identification confidence, then match
    confidence, tie-broken by first file path (stable and deterministic)."""
    def first_path(b: BookUnit) -> str:
        return min((str(sf.path) for sf in b.source_files), default=str(b.source_folder))
    return sorted(books, key=lambda b: (-b.identity_confidence, -b.confidence, first_path(b)))[0]


def combine_books(
    books: BookUnitRepo, grouping: GroupingOverrideRepo, folder: Path, folder_books: list[BookUnit]
) -> BookUnit:
    """Merge every book in `folder` into one. The primary book's metadata wins; all files
    (natural-sorted) become the book's chapters. Persists a 'single book' grouping override
    with a snapshot of the replaced books (for undo), replaces the book rows, and returns the
    merged book. `folder_books` must be non-empty and all share `folder`."""
    snapshot = json.dumps([b.model_dump(mode="json") for b in folder_books])
    primary = _primary(folder_books)

    seen: dict[Path, SourceFile] = {}
    for b in folder_books:
        for sf in b.source_files:
            seen.setdefault(sf.path, sf)
    ordered = sorted(seen.values(), key=lambda sf: _natural_key(sf.path.name))

    merged = primary.model_copy(deep=True)
    merged.id = BookUnit.id_for(folder)  # the folder's sole book now
    merged.source_folder = folder
    merged.source_files = list(ordered)
    merged.content_kind = ContentKind.SINGLE
    merged.detected_works = [
        DetectedWork(label=(primary.title or folder.name), files=[sf.path for sf in ordered])
    ]
    merged.chapters = file_boundary_chapters(
        [(sf.path.name, sf.duration_seconds) for sf in ordered]
    )
    merged.touch()

    grouping.set_single(str(folder), snapshot)
    for b in folder_books:
        if b.id != merged.id:
            books.delete(b.id, commit=False)
    books.upsert(merged, commit=True)
    return merged


def uncombine_books(
    books: BookUnitRepo, grouping: GroupingOverrideRepo, folder: Path
) -> list[BookUnit]:
    """Reverse a Combine: clear the grouping override and restore the snapshotted books,
    dropping the merged book. Returns the restored books (empty if there was no snapshot)."""
    snap = grouping.snapshot(str(folder))
    grouping.clear(str(folder))
    books.delete(BookUnit.id_for(folder), commit=False)
    restored: list[BookUnit] = []
    if snap:
        for data in json.loads(snap):
            book = BookUnit.model_validate(data)
            books.upsert(book, commit=False)
            restored.append(book)
    books.conn.commit()
    return restored
