"""Classify persisted books whose backing files are no longer trackable, for the
user-driven Utilities clean-up action.

Two reasons, disjoint by construction: a book is either UNDER a current scan path
(and may be 'removed_from_disk') or under NONE of them ('outside_scan_paths'). It
cannot be both, so the two result lists never share a book."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from colophon.core.models import BookUnit
from colophon.core.reassociate import is_missing

CleanupReason = Literal["removed_from_disk", "outside_scan_paths"]


@dataclass(frozen=True)
class CleanupCandidate:
    """One book proposed for removal, with the reason it is stale and enough to
    display it in the preview list."""

    book_id: str
    title: str
    source_folder: Path
    reason: CleanupReason


@dataclass
class CleanupReport:
    """The two disjoint buckets of removable books. Not frozen: it holds lists, so
    `frozen=True` would only guarantee shallow immutability and mislead callers."""

    removed_from_disk: list[CleanupCandidate]
    outside_scan_paths: list[CleanupCandidate]


def _containing_scan_path(folder: Path, scan_paths: Sequence[Path]) -> Path | None:
    """The scan path equal to or containing `folder`, or None if under none."""
    return next(
        (p for p in scan_paths if p == folder or p in folder.parents),
        None,
    )


def _candidate(book: BookUnit, reason: CleanupReason) -> CleanupCandidate:
    return CleanupCandidate(
        book_id=book.id,
        title=book.title or book.source_folder.name,
        source_folder=book.source_folder,
        reason=reason,
    )


def find_cleanup_candidates(
    books: Sequence[BookUnit], scan_paths: Sequence[Path]
) -> CleanupReport:
    """Bucket `books` into removed-from-disk vs outside-scan-paths.

    - removed_from_disk: under a current scan path but its folder is gone. Reuses
      `is_missing`, so organized books (output_path set) and books under an
      unreachable root are excluded automatically.
    - outside_scan_paths: under no current scan path. Organized books are excluded
      here too — their durable artifact means they are finished, not orphaned.
    """
    removed: list[CleanupCandidate] = []
    outside: list[CleanupCandidate] = []
    root_exists: dict[Path, bool] = {}
    for book in books:
        root = _containing_scan_path(book.source_folder, scan_paths)
        if root is None:
            if book.output_path is None:
                outside.append(_candidate(book, "outside_scan_paths"))
            continue
        accessible = root_exists.setdefault(root, root.exists())
        if is_missing(book, root_accessible=accessible):
            removed.append(_candidate(book, "removed_from_disk"))
    return CleanupReport(removed_from_disk=removed, outside_scan_paths=outside)
