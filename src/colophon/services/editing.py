"""Candidate-record editing: set a field, remap one field to another, swap two.

All mutations record EditChange history under a batch_id (for undo) and stamp the
affected field's provenance as 'manual'. No file writes — candidate records only.
"""

from __future__ import annotations

from collections.abc import Callable

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo
from colophon.core.fields import EDITABLE_TO_PROVENANCE, get_field, set_field
from colophon.core.genre_policy import GenrePolicy
from colophon.core.models import BookUnit, EditChange, EmbeddedTags, Provenance, new_batch_id
from colophon.core.normalize import FIELD_NORMALIZERS

# Embedded-tag fields offered as a Remap source: a one-way copy of what the file itself carries
# into a book field (move-only — you can't write back into or clear a file tag from here). Ordered
# for the dropdown; each name is an EmbeddedTags attribute.
EMBEDDED_SOURCE_FIELDS = (
    "title", "album", "artist", "narrator", "series", "sequence",
    "year", "genre", "description", "asin", "isbn", "track",
)


def embedded_value(tags: EmbeddedTags, key: str) -> str | None:
    """The string value of one embedded-tag field, formatted for setting into a book field (numbers
    stringified, a whole sequence without a trailing '.0'). None when the file carries no such tag."""
    if key not in EMBEDDED_SOURCE_FIELDS:
        raise ValueError(f"unknown embedded source field {key!r}")
    raw = getattr(tags, key)
    if raw is None or raw == "":
        return None
    if key == "sequence":
        return str(int(raw)) if raw == int(raw) else str(raw)
    return str(raw)


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


def _bulk_commit[T](
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[T],
    change_fn: Callable[[T], tuple[BookUnit, list[EditChange]]],
) -> str:
    """Run `change_fn` for each item in one all-or-nothing transaction, persisting
    only the books it actually changed (a change is `new_value != old_value`), and
    record every collected EditChange as one undoable batch. Returns the batch id.

    `change_fn(item)` returns `(book, changes)`. Only effective changes
    (`new_value != old_value`) are recorded and only changed books are persisted, so
    every bulk op skips no-op writes consistently (no spurious touch or history/undo
    entry for an unchanged book)."""
    batch_id = new_batch_id()
    changes: list[EditChange] = []
    with books.conn:  # one transaction: all-or-nothing
        for item in items:
            book, book_changes = change_fn(item)
            effective = [c for c in book_changes if c.new_value != c.old_value]
            changes.extend(effective)
            if effective:
                book.touch()
                books.upsert(book, commit=False)
        hist.record(batch_id, changes, commit=False)
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


def bulk_remap_embedded_field(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[BookUnit],
    *,
    dst: str,
    value_for: Callable[[BookUnit], str | None],
) -> str:
    """Set `dst` on each book from `value_for(book)` (its own embedded value) in one undoable batch.
    A book whose embedded tag is empty yields no value and is skipped."""
    def _changes(book: BookUnit) -> tuple[BookUnit, list[EditChange]]:
        value = value_for(book)
        return book, ([_apply(book, dst, value)] if value is not None else [])

    return _bulk_commit(books, hist, items, _changes)


def swap_fields(
    books: BookUnitRepo, hist: HistoryRepo, book: BookUnit, field_a: str, field_b: str
) -> str:
    val_a = get_field(book, field_a)
    val_b = get_field(book, field_b)
    changes = [_apply(book, field_a, val_b), _apply(book, field_b, val_a)]
    return _commit(books, hist, book, changes)


def bulk_swap_fields(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[BookUnit],
    *,
    field_a: str,
    field_b: str,
) -> str:
    """Exchange two fields' values across `items` in one undoable batch. A book
    whose two fields already hold the same value is a no-op and is skipped."""
    def _changes(book: BookUnit) -> tuple[BookUnit, list[EditChange]]:
        val_a = get_field(book, field_a)
        val_b = get_field(book, field_b)
        return book, [_apply(book, field_a, val_b), _apply(book, field_b, val_a)]

    return _bulk_commit(books, hist, items, _changes)


def bulk_set_field(
    books: BookUnitRepo, hist: HistoryRepo, items: list[BookUnit], field: str, value: str | None
) -> str:
    return _bulk_commit(books, hist, items, lambda book: (book, [_apply(book, field, value)]))


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

    def _changes(book: BookUnit) -> tuple[BookUnit, list[EditChange]]:
        out: list[EditChange] = []
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
            out.append(_apply(book, field, normalized))
        return book, out

    return _bulk_commit(books, hist, items, _changes)


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
    def _changes(item: tuple[BookUnit, dict[str, str | None], str]) -> tuple[BookUnit, list[EditChange]]:
        book, updates, provenance = item
        return book, [
            _apply(book, field, value, provenance=provenance) for field, value in updates.items()
        ]

    return _bulk_commit(books, hist, items, _changes)


def bulk_remap(
    books: BookUnitRepo,
    hist: HistoryRepo,
    items: list[BookUnit],
    *,
    src: str,
    dst: str,
    clear_source: bool,
) -> str:
    def _changes(book: BookUnit) -> tuple[BookUnit, list[EditChange]]:
        moved = get_field(book, src)
        out = [_apply(book, dst, moved)]
        if clear_source:
            out.append(_apply(book, src, None))
        return book, out

    return _bulk_commit(books, hist, items, _changes)
