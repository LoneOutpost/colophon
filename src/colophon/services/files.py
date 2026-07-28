"""Operations on a BookUnit's source files: reorder, exclude, rename.

reorder/exclude mutate only the in-memory BookUnit (the candidate's file list);
rename also moves the file on disk (collision-safe). Persistence is the caller's
job (the controller upserts after each call)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from colophon.core.models import BookUnit

logger = logging.getLogger(__name__)


def reorder(book: BookUnit, ordered_paths: list[Path]) -> None:
    """Reorder source_files to match `ordered_paths` (must be a permutation)."""
    current = {sf.path: sf for sf in book.source_files}
    if set(ordered_paths) != set(current) or len(ordered_paths) != len(current):
        raise ValueError("ordered_paths must be a permutation of the book's files")
    book.source_files = [current[p] for p in ordered_paths]


def exclude(book: BookUnit, path: Path) -> None:
    """Remove a file from the book's source list (does not delete it from disk)."""
    book.source_files = [sf for sf in book.source_files if sf.path != path]


def delete_files_from_disk(paths: list[Path]) -> list[Path]:
    """Permanently unlink each path from disk. Returns the paths now absent as a result — a path
    already gone counts as removed (the goal is achieved), so the caller drops it from the book too;
    a path that fails to unlink (permissions) is logged and omitted so the caller keeps it and its
    finding stays truthful. Irreversible: callers gate on a confirm dialog."""
    removed: list[Path] = []
    for p in paths:
        try:
            p.unlink(missing_ok=True)
            removed.append(p)
        except OSError as e:
            logger.warning(f"delete_files_from_disk: could not remove {p}: {e}")
    return removed


def rename(book: BookUnit, path: Path, new_name: str) -> Path:
    """Rename `path` to `new_name` within its directory and update source_files.

    Raises FileExistsError if the target already exists (never overwrites)."""
    if not new_name.strip():
        raise ValueError("filename must not be empty")
    target = path.with_name(new_name)
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    path.rename(target)
    for sf in book.source_files:
        if sf.path == path:
            sf.path = target
    return target


def move_on_disk(path: Path, dest_dir: Path, new_name: str | None = None) -> Path:
    """Move `path` into `dest_dir` on disk, optionally renaming to `new_name`. Creates `dest_dir`
    (and parents) if missing. Never overwrites: raises FileExistsError if a different file already
    occupies the target. Returns the new path. Touches no book model — the caller re-derives the
    library from disk afterward."""
    name = (new_name or path.name).strip()
    if not name:
        raise ValueError("filename must not be empty")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    if target != path and target.exists():
        raise FileExistsError(f"{target} already exists")
    shutil.move(str(path), str(target))
    return target
