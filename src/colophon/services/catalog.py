"""Undoable, library-wide catalog edits (rename / merge / delete a vocabulary value
across every referencing book). Built on the field machinery so it reuses the
EditChange undo system."""

from __future__ import annotations

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo
from colophon.core.catalog import entry_names, remap_names
from colophon.core.fields import get_field, set_field
from colophon.core.models import EditChange, new_batch_id


def apply_catalog_mapping(
    books: BookUnitRepo, hist: HistoryRepo, kind: str, mapping: dict[str, str | None]
) -> tuple[list[str], str | None]:
    """Apply `mapping` to `kind` across all books as one undoable batch.

    Returns (affected_book_ids, batch_id); batch_id is None when nothing changed.
    """
    batch_id = new_batch_id()
    changes: list[EditChange] = []
    touched = []
    for book in books.list_all():
        names = entry_names(book, kind)
        if not any(n in mapping for n in names):
            continue
        old_value = get_field(book, kind)
        remapped = remap_names(names, mapping)
        new_value = "; ".join(remapped) if remapped else None
        if new_value == old_value:
            continue
        changes.append(EditChange(book_id=book.id, field=kind, old_value=old_value, new_value=new_value))
        set_field(book, kind, new_value)
        book.touch()
        touched.append(book)
    if not changes:
        return ([], None)
    for book in touched:
        books.upsert(book)
    hist.record(batch_id, changes)
    return ([b.id for b in touched], batch_id)
