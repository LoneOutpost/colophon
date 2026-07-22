"""Reassign a single audio file from one book to a sibling book in the same folder.

A selective, per-file counterpart to Combine: instead of merging every file into one book, move one
file between the books that share a folder. Persists a 'partition' grouping override (the folder's
file->book split) so a rescan keeps the manual assignment. Modeled on `services/combine.py`."""

from __future__ import annotations

from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, GroupingOverrideRepo
from colophon.core.chapters import file_boundary_chapters
from colophon.core.graph import leaf_id_for
from colophon.core.models import BookUnit, ContentKind, DetectedWork, SourceFile
from colophon.services.combine import _natural_key


def _rebuild(book: BookUnit, folder: Path, files: list[SourceFile]) -> BookUnit:
    """A copy of `book` owning exactly `files`: new file-derived id, refreshed chapters/detected work,
    metadata preserved. `files` must be natural-sorted by the caller."""
    b = book.model_copy(deep=True)
    b.id = leaf_id_for(folder, [sf.path for sf in files])
    b.source_files = list(files)
    b.content_kind = ContentKind.SINGLE
    b.detected_works = [DetectedWork(label=(book.title or folder.name), files=[sf.path for sf in files])]
    b.chapters = file_boundary_chapters([(sf.path.name, sf.duration_seconds) for sf in files])
    b.touch()
    return b


def reassign_file(
    books: BookUnitRepo, grouping: GroupingOverrideRepo, folder: Path, file: Path, target_id: str
) -> BookUnit:
    """Move `file` into the book `target_id`, out of whichever sibling book currently owns it, within
    `folder`. Rebuilds both books (metadata carried across the id change), deletes the source if it is
    left empty, persists the folder's new partition, and returns the rebuilt target. A no-op (returns
    the target) if `file` already belongs to the target."""
    folder_books = [b for b in (books.get(i) for i in books.ids_in_folder(folder)) if b is not None]
    target = next(b for b in folder_books if b.id == target_id)
    source = next(b for b in folder_books if any(sf.path == file for sf in b.source_files))
    if source.id == target.id:
        return target
    moved = next(sf for sf in source.source_files if sf.path == file)

    target_files = sorted([*target.source_files, moved], key=lambda sf: _natural_key(sf.path.name))
    new_target = _rebuild(target, folder, target_files)
    source_files = [sf for sf in source.source_files if sf.path != file]
    new_source = _rebuild(source, folder, source_files) if source_files else None

    surviving: list[BookUnit] = []
    for b in folder_books:
        if b.id == target.id:
            surviving.append(new_target)
        elif b.id == source.id:
            if new_source is not None:
                surviving.append(new_source)
        else:
            surviving.append(b)
    grouping.set_partition(
        str(folder), [[sf.path.name for sf in b.source_files] for b in surviving]
    )

    books.delete(target.id, commit=False)
    books.delete(source.id, commit=False)
    books.upsert(new_target, commit=False)
    if new_source is not None:
        books.upsert(new_source, commit=False)
    books.conn.commit()
    return new_target
