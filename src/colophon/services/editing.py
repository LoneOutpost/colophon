"""Candidate-record editing: set a field, remap one field to another, swap two.

All mutations record EditChange history under a batch_id (for undo) and stamp the
affected field's provenance as 'manual'. No file writes — candidate records only.
"""

from __future__ import annotations

import uuid

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo
from colophon.core.fields import EDITABLE_TO_PROVENANCE, get_field, set_field
from colophon.core.models import BookUnit, EditChange, Provenance


def _new_batch_id() -> str:
    return uuid.uuid4().hex


def _apply(
    book: BookUnit, field: str, value: str | None, *, provenance: str = Provenance.MANUAL.value
) -> EditChange:
    old = get_field(book, field)
    set_field(book, field, value)
    actual = get_field(book, field)  # truth after set (a no-op set leaves this == old)
    if actual != old:
        # Provenance is keyed by the stored model attribute (e.g. "authors"), not the
        # editable field key ("author"), to match reconcile.py and field_provenance().
        book.provenance[EDITABLE_TO_PROVENANCE[field]] = provenance
    return EditChange(book_id=book.id, field=field, old_value=old, new_value=actual)


def _commit(
    books: BookUnitRepo, hist: HistoryRepo, book: BookUnit, changes: list[EditChange]
) -> str:
    batch_id = _new_batch_id()
    book.touch()
    books.upsert(book)
    hist.record(batch_id, changes)
    return batch_id


def set_field_value(
    books: BookUnitRepo, hist: HistoryRepo, book: BookUnit, field: str, value: str | None
) -> str:
    return _commit(books, hist, book, [_apply(book, field, value)])


def remap_field(
    books: BookUnitRepo,
    hist: HistoryRepo,
    book: BookUnit,
    *,
    src: str,
    dst: str,
    clear_source: bool,
) -> str:
    moved = get_field(book, src)
    changes = [_apply(book, dst, moved)]
    if clear_source:
        changes.append(_apply(book, src, None))
    return _commit(books, hist, book, changes)


def swap_fields(
    books: BookUnitRepo, hist: HistoryRepo, book: BookUnit, field_a: str, field_b: str
) -> str:
    val_a = get_field(book, field_a)
    val_b = get_field(book, field_b)
    changes = [_apply(book, field_a, val_b), _apply(book, field_b, val_a)]
    return _commit(books, hist, book, changes)


def bulk_set_field(
    books: BookUnitRepo, hist: HistoryRepo, items: list[BookUnit], field: str, value: str | None
) -> str:
    batch_id = _new_batch_id()
    changes: list[EditChange] = []
    with books.conn:  # one transaction: all-or-nothing
        for book in items:
            changes.append(_apply(book, field, value))
            book.touch()
            books.upsert(book, commit=False)
        hist.record(batch_id, changes, commit=False)
    return batch_id


def apply_fields(
    books: BookUnitRepo,
    hist: HistoryRepo,
    book: BookUnit,
    updates: dict[str, str | None],
    *,
    provenance: str,
) -> str:
    """Apply field/value `updates` to `book` under one batch, stamping each
    changed field's provenance with `provenance` (e.g. a source name). Records
    history (undoable) and persists. Returns the batch id."""
    changes = [_apply(book, field, value, provenance=provenance) for field, value in updates.items()]
    return _commit(books, hist, book, changes)


def bulk_remap(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[BookUnit],
    *,
    src: str,
    dst: str,
    clear_source: bool,
) -> str:
    batch_id = _new_batch_id()
    changes: list[EditChange] = []
    with books.conn:  # one transaction: all-or-nothing
        for book in items:
            moved = get_field(book, src)
            changes.append(_apply(book, dst, moved))
            if clear_source:
                changes.append(_apply(book, src, None))
            book.touch()
            books.upsert(book, commit=False)
        hist.record(batch_id, changes, commit=False)
    return batch_id
