"""Undo a recorded edit batch by replaying prior values back onto the books."""

from __future__ import annotations

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo
from colophon.core.fields import set_field
from colophon.core.models import BookUnit


def undo_batch(books: BookUnitRepo, hist: HistoryRepo, batch_id: str) -> None:
    """Restore every change in `batch_id` to its old value, newest change first."""
    changes = hist.list_batch(batch_id)
    touched: dict[str, BookUnit] = {}
    for change in reversed(changes):
        book = touched.get(change.book_id) or books.get(change.book_id)
        if book is None:
            continue
        set_field(book, change.field, change.old_value)
        touched[change.book_id] = book
    for book in touched.values():
        book.touch()
        books.upsert(book)
    hist.mark_reverted(batch_id)


def undo_last(books: BookUnitRepo, hist: HistoryRepo) -> bool:
    """Undo the most recent non-reverted batch. Returns False if there is none."""
    batch_id = hist.latest_batch_id()
    if batch_id is None:
        return False
    undo_batch(books, hist, batch_id)
    return True
