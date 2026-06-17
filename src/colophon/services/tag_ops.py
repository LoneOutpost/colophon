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
