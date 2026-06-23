"""Candidate-record editing: set a field, remap one field to another, swap two.

All mutations record EditChange history under a batch_id (for undo) and stamp the
affected field's provenance as 'manual'. No file writes — candidate records only.
"""

from __future__ import annotations

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo
from colophon.core.fields import EDITABLE_TO_PROVENANCE, get_field, set_field
from colophon.core.genre_policy import GenrePolicy
from colophon.core.models import BookUnit, EditChange, Provenance, new_batch_id
from colophon.core.normalize import FIELD_NORMALIZERS


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
    batch_id = new_batch_id()
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
    batch_id = new_batch_id()
    changes: list[EditChange] = []
    with books.conn:  # one transaction: all-or-nothing
        for book in items:
            changes.append(_apply(book, field, value))
            book.touch()
            books.upsert(book, commit=False)
        hist.record(batch_id, changes, commit=False)
    return batch_id


def bulk_normalize(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[BookUnit],
    fields: list[str],
    genre_policy: GenrePolicy | None = None,
) -> str:
    """Normalize each given text `field`'s current value across `items`, in one
    undoable batch. A field is normalized per book (its own value), and only
    actual changes are recorded. When `genre_policy` is given, the `genre` field
    is canonicalized through it (map + optional whitelist) instead of the plain
    normalizer. Returns the batch id (an empty batch if nothing needed
    normalizing)."""
    normalizers = dict(FIELD_NORMALIZERS)
    if genre_policy is not None:
        normalizers["genre"] = lambda v: "; ".join(
            genre_policy.canonicalize([p.strip() for p in v.split(";") if p.strip()])
        )
    batch_id = new_batch_id()
    changes: list[EditChange] = []
    with books.conn:  # one transaction: all-or-nothing
        for book in items:
            book_changed = False
            for field in fields:
                normalizer = normalizers.get(field)
                if normalizer is None:
                    continue
                current = get_field(book, field)
                if not current:
                    continue
                normalized = normalizer(current)
                if normalized == current:
                    continue
                changes.append(_apply(book, field, normalized))
                book_changed = True
            if book_changed:
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


def bulk_apply_fields(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[tuple[BookUnit, dict[str, str | None], str]],
) -> str:
    """Apply each (book, field_updates, provenance) in ONE undoable batch. The
    bulk sibling of apply_fields; each book's fields are stamped with its given
    provenance (e.g. the source provider). Returns the batch id."""
    batch_id = new_batch_id()
    changes: list[EditChange] = []
    with books.conn:  # one transaction: all-or-nothing
        for book, updates, provenance in items:
            book_changed = False
            for field, value in updates.items():
                change = _apply(book, field, value, provenance=provenance)
                changes.append(change)
                if change.new_value != change.old_value:
                    book_changed = True
            if book_changed:
                book.touch()
                books.upsert(book, commit=False)
        hist.record(batch_id, changes, commit=False)
    return batch_id


def bulk_remap(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[BookUnit],
    *,
    src: str,
    dst: str,
    clear_source: bool,
) -> str:
    batch_id = new_batch_id()
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
