"""Move a finished M4B into its LazyLibrarian-derived location, collision-safe."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo
from colophon.core.models import BookUnit, Phase, PhaseState, _Base
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
    target: Path,
) -> OrganizeResult:
    """Move `m4b_path` to `target` under the library; never overwrite an existing
    file. `target` is precomputed by the caller (from the book's canonical entity
    names), so this function owns the move and the book's phase/output_path mutation,
    not the path grammar."""
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
    # colophon does not write a destination metadata.json — that is AudiobookShelf's domain.
    # A future explicit "export to ABS" utility can opt into writing sidecars.
    return OrganizeResult(book_id=book.id, target_path=target, moved=True)
