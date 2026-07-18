"""Find books that would organize to the same destination.

Pure over the persist dry-run: given each book's computed target path (from
`controller.organize_targets`, i.e. `pathscheme.build_target_path` under the saved
settings), group the ones that collide. Two books sharing a target would clobber
each other on organize, so surfacing the groups lets a human resolve them first.
No I/O and no filesystem comparison — only the in-library previewed targets.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollidingBook:
    """A book in a collision, with enough to identify it in the report (two colliding books often
    share a title — the source folder is what tells them apart)."""

    id: str
    title: str
    source_folder: Path


@dataclass(frozen=True)
class DuplicateDestination:
    """One collision: the shared target path and the books that would organize to it."""

    target: Path
    books: list[CollidingBook]


def duplicate_targets(targets: list[tuple[str, Path]]) -> list[tuple[Path, list[str]]]:
    """Group `(book_id, target_path)` pairs by target, returning only the paths shared by two or
    more books. Each group's book ids are sorted, and groups are ordered by target path, so the
    result is deterministic. Empty when every target is unique."""
    by_path: dict[Path, list[str]] = defaultdict(list)
    for book_id, path in targets:
        by_path[path].append(book_id)
    return [
        (path, sorted(ids))
        for path, ids in sorted(by_path.items(), key=lambda kv: str(kv[0]))
        if len(ids) > 1
    ]
