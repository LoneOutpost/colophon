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
    # A future explicit "export to ABS" utility can opt into writing datafile sidecars.
    return OrganizeResult(book_id=book.id, target_path=target, moved=True)


def organize_book_parts(
    repo: BookUnitRepo,
    book: BookUnit,
    pairs: list[tuple[Path, Path]],
    *,
    delete_sources: bool,
) -> OrganizeResult:
    """Copy each (source, target) into the library, all-or-nothing. Targets share
    a book folder. Never overwrites an existing file; on any failure the copies made
    this call are removed and sources are left intact. On success sets output_path to
    the book folder, marks ORGANIZE fresh, and deletes sources only if requested."""
    targets = [dst for _, dst in pairs]
    folder = targets[0].parent
    if any(dst.exists() for dst in targets):
        logger.warning(f"collision organizing {book.id}: a target under {folder} exists")
        return OrganizeResult(book_id=book.id, target_path=folder, collision=True)

    folder.mkdir(parents=True, exist_ok=True)
    reserved: list[Path] = []
    try:
        for _src, dst in pairs:
            fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            reserved.append(dst)
        for src, dst in pairs:
            shutil.copy2(str(src), str(dst))  # replaces the 0-byte placeholder
        for _src, dst in pairs:
            if dst.stat().st_size == 0:
                raise OSError(f"verification failed: empty file {dst}")
    except FileExistsError:
        for dst in reserved:
            dst.unlink(missing_ok=True)
        logger.warning(f"collision organizing {book.id}: target appeared under {folder}")
        return OrganizeResult(book_id=book.id, target_path=folder, collision=True)
    except OSError as e:
        for dst in reserved:
            dst.unlink(missing_ok=True)
        logger.warning(f"copy failed organizing {book.id}: {e}")
        return OrganizeResult(book_id=book.id, target_path=folder, error=str(e))

    if delete_sources:
        for src, _dst in pairs:
            src.unlink(missing_ok=True)

    book.output_path = folder
    mark(book, Phase.ORGANIZE, PhaseState.FRESH)
    resync_state(book)
    book.touch()
    repo.upsert(book)
    return OrganizeResult(book_id=book.id, target_path=folder, moved=True)
