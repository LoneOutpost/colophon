"""Move a finished M4B into its LazyLibrarian-derived location, collision-safe."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from colophon.adapters.lazylibrarian import AudiobookPatterns
from colophon.adapters.repository.store import BookUnitRepo
from colophon.adapters.sidecar import write_sidecar
from colophon.core.models import BookUnit, Phase, PhaseState, _Base
from colophon.core.pathscheme import build_target_path
from colophon.core.phases import mark, resync_state

logger = logging.getLogger(__name__)


class OrganizeResult(_Base):
    book_id: str
    target_path: Path | None = None
    moved: bool = False
    collision: bool = False
    error: str | None = None


def organize_book(
    repo: BookUnitRepo,
    book: BookUnit,
    m4b_path: Path,
    *,
    root: Path,
    patterns: AudiobookPatterns,
) -> OrganizeResult:
    """Move `m4b_path` to its target under `root`; never overwrite an existing file."""
    target = build_target_path(root, patterns, book)
    if target.exists():
        logger.warning(f"collision organizing {book.id}: {target} exists")
        return OrganizeResult(book_id=book.id, target_path=target, collision=True)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomically reserve the destination name so a file appearing after the
    # exists() check above cannot be silently overwritten by the move.
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
    except FileExistsError:
        logger.warning(f"collision organizing {book.id}: {target} appeared")
        return OrganizeResult(book_id=book.id, target_path=target, collision=True)

    try:
        shutil.move(str(m4b_path), str(target))  # replaces our 0-byte placeholder
    except OSError as e:
        logger.warning(f"move failed organizing {book.id}: {e}")
        target.unlink(missing_ok=True)  # remove the placeholder we created
        return OrganizeResult(book_id=book.id, target_path=target, error=str(e))

    book.output_path = target
    mark(book, Phase.ORGANIZE, PhaseState.FRESH)
    resync_state(book)
    book.touch()
    repo.upsert(book)
    try:
        write_sidecar(target.parent, book)
    except Exception as e:  # destination sidecar is secondary to the completed move
        logger.warning(f"destination sidecar write failed for {book.id}: {e}")
    return OrganizeResult(book_id=book.id, target_path=target, moved=True)
