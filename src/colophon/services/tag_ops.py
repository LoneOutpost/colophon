"""The in-place Tag operation: plan (preview), commit (write + log), revert.

`plan_tag` is pure (reads files, writes nothing) and powers the dry-run preview.
`commit_tag` writes the projected tags + cached cover into each source file and
records every write — with the file's prior tags — in the operations log, so
`revert_tag_batch` can replay those prior values. A per-file failure never aborts
the rest of the book.
"""

from __future__ import annotations

import logging
from pathlib import Path

from colophon.adapters.repository.store import OperationRepo
from colophon.adapters.tags import embed_cover, read_embedded_tags, write_embedded_tags
from colophon.core.errors import TagWriteError
from colophon.core.models import BookUnit, EmbeddedTags, OperationRecord, _Base
from colophon.core.tag_projection import project_tags
from colophon.core.tag_validation import validate_tags

logger = logging.getLogger(__name__)

_OP_TAG_WRITE = "tag_write"
_TAG_FIELDS = ["title", "album", "artist", "narrator", "series", "sequence", "year", "genre", "description", "asin"]


class TagFilePlan(_Base):
    path: Path
    changed_fields: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance


class TagPlan(_Base):
    book_id: str
    title: str | None = None
    target: EmbeddedTags
    files: list[TagFilePlan] = []  # noqa: RUF012 - pydantic field default, copied per instance
    warnings: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    embed_cover: bool = False


def _changed_fields(current: EmbeddedTags, target: EmbeddedTags) -> list[str]:
    return [
        f for f in _TAG_FIELDS
        if getattr(target, f) is not None and getattr(target, f) != getattr(current, f)
    ]


def plan_tag(book: BookUnit) -> TagPlan:
    """Compute, without writing, the tag changes a Tag commit would make."""
    target = project_tags(book)
    files = [
        TagFilePlan(path=sf.path, changed_fields=_changed_fields(read_embedded_tags(sf.path), target))
        for sf in book.source_files
    ]
    has_cover = book.cover_path is not None and book.cover_path.exists()
    return TagPlan(
        book_id=book.id, title=book.title, target=target, files=files,
        warnings=validate_tags(target), embed_cover=has_cover,
    )


class TagCommitResult(_Base):
    book_id: str
    written: int = 0
    failed: int = 0


def _load_cover(book: BookUnit) -> tuple[bytes, str] | None:
    if book.cover_path is None or not book.cover_path.exists():
        return None
    mime = "image/png" if book.cover_path.suffix.lower() == ".png" else "image/jpeg"
    return book.cover_path.read_bytes(), mime


def _tag_and_log(
    path: Path, target: EmbeddedTags, cover: tuple[bytes, str] | None,
    *, operations: OperationRepo, book_id: str, batch_id: str,
) -> bool:
    """Write `target` tags (+ optional cover) into one file and log the op with
    its prior tags. Returns True on success, False on a logged TagWriteError."""
    before = read_embedded_tags(path)
    try:
        write_embedded_tags(path, target)
        if cover is not None:
            embed_cover(path, cover[0], cover[1])
    except TagWriteError as e:
        logger.warning(f"tag write failed for {path}: {e}")
        operations.record(OperationRecord(
            batch_id=batch_id, book_id=book_id, op_type=_OP_TAG_WRITE,
            target=str(path), before=before.model_dump_json(), outcome="failed", detail=str(e),
        ))
        return False
    operations.record(OperationRecord(
        batch_id=batch_id, book_id=book_id, op_type=_OP_TAG_WRITE, target=str(path),
        before=before.model_dump_json(), after=target.model_dump_json(), outcome="ok",
    ))
    return True


def commit_tag(book: BookUnit, *, operations: OperationRepo, batch_id: str) -> TagCommitResult:
    """Write projected tags (+ cached cover) into each source file and log each write.

    Each file's prior tags are captured into the log before writing so the batch
    is revertible. A per-file TagWriteError is logged as a failed op and does not
    abort the remaining files.
    """
    target = project_tags(book)
    cover = _load_cover(book)
    result = TagCommitResult(book_id=book.id)
    for sf in book.source_files:
        if _tag_and_log(sf.path, target, cover, operations=operations, book_id=book.id, batch_id=batch_id):
            result.written += 1
        else:
            result.failed += 1
    return result


def tag_file(path: Path, book: BookUnit, *, operations: OperationRepo, batch_id: str) -> bool:
    """Embed the book's projected tags (+ cached cover) into a single file (e.g.
    the produced M4B) and log the write. Returns True on success."""
    return _tag_and_log(
        path, project_tags(book), _load_cover(book),
        operations=operations, book_id=book.id, batch_id=batch_id,
    )


def revert_tag_batch(operations: OperationRepo, batch_id: str) -> int:
    """Restore the prior tags of every successful tag write in `batch_id`.

    Restores text tags only (cover art is not reverted). Returns the count
    restored and marks the batch reverted.
    """
    restored = 0
    for op in operations.list_batch(batch_id):
        if op.op_type != _OP_TAG_WRITE or op.outcome != "ok" or op.before is None:
            continue
        try:
            write_embedded_tags(Path(op.target), EmbeddedTags.model_validate_json(op.before))
            restored += 1
        except TagWriteError as e:
            logger.warning(f"revert failed for {op.target}: {e}")
    operations.mark_reverted(batch_id)
    return restored
