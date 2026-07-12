from pathlib import Path

import pytest

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo, connect, migrate
from colophon.core.models import BookUnit, Provenance
from colophon.services.editing import (
    apply_fields,
    bulk_apply_fields,
    bulk_remap,
    bulk_set_field,
    bulk_swap_fields,
    remap_field,
    set_field_value,
    swap_fields,
)
from colophon.services.undo import undo_batch


def _repos(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn), HistoryRepo(conn)


def _book(books: BookUnitRepo) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Wrong Title"
    b.narrators = []
    books.upsert(b)
    return b


def test_set_field_records_history_and_provenance(tmp_path):
    books, hist = _repos(tmp_path)
    b = _book(books)
    batch = set_field_value(books, hist, b, "title", "Dune")
    assert b.title == "Dune"
    assert b.provenance["title"] == Provenance.MANUAL.value
    changes = hist.list_batch(batch)
    assert (changes[0].old_value, changes[0].new_value) == ("Wrong Title", "Dune")
    assert books.get(b.id).title == "Dune"


def test_remap_moves_value_and_clears_source(tmp_path):
    books, hist = _repos(tmp_path)
    b = _book(books)
    b.title = "Scott Brick"  # narrator wrongly in title
    books.upsert(b)
    set_remap_batch = remap_field(books, hist, b, src="title", dst="narrator", clear_source=True)
    assert b.narrators == ["Scott Brick"]
    assert b.title is None
    changes = hist.list_batch(set_remap_batch)
    fields = {c.field for c in changes}
    assert fields == {"narrator", "title"}


def test_swap_exchanges_two_fields(tmp_path):
    books, hist = _repos(tmp_path)
    b = _book(books)
    b.title, b.subtitle = "A", "B"
    books.upsert(b)
    swap_fields(books, hist, b, "title", "subtitle")
    assert b.title == "B"
    assert b.subtitle == "A"


def test_swap_undo_restores_both_fields(tmp_path):
    books, hist = _repos(tmp_path)
    b = _book(books)
    b.title, b.subtitle = "A", "B"
    books.upsert(b)
    batch = swap_fields(books, hist, b, "title", "subtitle")
    assert (b.title, b.subtitle) == ("B", "A")
    undo_batch(books, hist, batch)
    restored = books.get(b.id)
    assert (restored.title, restored.subtitle) == ("A", "B")


def test_bulk_swap_across_books(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.title, a.subtitle = "TA", "SA"
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    b.title, b.subtitle = "TB", "SB"
    books.upsert(a)
    books.upsert(b)
    batch = bulk_swap_fields(books, hist, [a, b], field_a="title", field_b="subtitle")
    assert (a.title, a.subtitle) == ("SA", "TA")
    assert (b.title, b.subtitle) == ("SB", "TB")
    assert len({c.book_id for c in hist.list_batch(batch)}) == 2


def test_bulk_swap_skips_noop_book(tmp_path):
    books, hist = _repos(tmp_path)
    same = BookUnit.new(source_folder=Path("/ingest/same"))
    same.title = same.subtitle = "X"  # equal fields -> swap is a no-op
    diff = BookUnit.new(source_folder=Path("/ingest/diff"))
    diff.title, diff.subtitle = "T", "S"
    books.upsert(same)
    books.upsert(diff)
    batch = bulk_swap_fields(books, hist, [same, diff], field_a="title", field_b="subtitle")
    assert {c.book_id for c in hist.list_batch(batch)} == {diff.id}  # only diff recorded


def test_bulk_set_field_one_batch_across_books(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    books.upsert(a)
    books.upsert(b)
    batch = bulk_set_field(books, hist, [a, b], "publisher", "Tor")
    assert a.publisher == "Tor" and b.publisher == "Tor"
    changes = hist.list_batch(batch)
    assert {c.book_id for c in changes} == {a.id, b.id}


def test_bulk_set_field_skips_no_op_books(tmp_path):
    # A book already at the target value is a no-op: no history entry and not
    # re-persisted (shared dirty gate). Only the genuinely-changed book is recorded.
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.publisher = "Tor"  # already the target
    b = BookUnit.new(source_folder=Path("/ingest/b"))  # no publisher yet
    books.upsert(a)
    books.upsert(b)
    batch = bulk_set_field(books, hist, [a, b], "publisher", "Tor")
    assert a.publisher == "Tor" and b.publisher == "Tor"
    changes = hist.list_batch(batch)
    assert {c.book_id for c in changes} == {b.id}  # only b changed; a's no-op dropped


def test_bulk_remap_across_books(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.title = "Narrator A"
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    b.title = "Narrator B"
    books.upsert(a)
    books.upsert(b)
    batch = bulk_remap(books, hist, [a, b], src="title", dst="narrator", clear_source=True)
    assert a.narrators == ["Narrator A"] and b.narrators == ["Narrator B"]
    assert a.title is None and b.title is None
    assert len({c.book_id for c in hist.list_batch(batch)}) == 2


def test_bulk_failure_rolls_back(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.publisher = "OrigA"
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    b.publisher = "OrigB"
    books.upsert(a)
    books.upsert(b)

    # Trigger a failure on the SECOND book by monkeypatching upsert to raise
    # on its 2nd invocation, mid-transaction.
    real_upsert = books.upsert
    calls = {"n": 0}

    def flaky_upsert(book, commit=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated mid-bulk failure")
        return real_upsert(book, commit=commit)

    books.upsert = flaky_upsert
    with pytest.raises(RuntimeError, match="simulated mid-bulk failure"):
        bulk_set_field(books, hist, [a, b], "publisher", "Tor")
    books.upsert = real_upsert

    # Neither book's change persisted: the whole transaction rolled back.
    assert books.get(a.id).publisher == "OrigA"
    assert books.get(b.id).publisher == "OrigB"
    # No history committed.
    assert hist.latest_batch_id() is None


def test_set_sequence_without_series_records_no_change(tmp_path):
    books, hist = _repos(tmp_path)
    b = BookUnit.new(source_folder=Path("/ingest/x"))  # empty series
    books.upsert(b)
    batch = set_field_value(books, hist, b, "sequence", "3")
    assert b.series == []  # no-op: meaningless without a series name
    changes = hist.list_batch(batch)
    assert changes[0].old_value == changes[0].new_value  # no real change recorded
    assert "sequence" not in b.provenance


def test_remap_clobbers_and_undo_restores_dst(tmp_path):
    from colophon.services.undo import undo_batch

    books, hist = _repos(tmp_path)
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "X"
    b.subtitle = "OLD"
    books.upsert(b)
    batch = remap_field(books, hist, b, src="title", dst="subtitle", clear_source=True)
    assert b.subtitle == "X"  # dst clobbered
    assert b.title is None  # source cleared
    undo_batch(books, hist, batch)
    restored = books.get(b.id)
    assert restored.subtitle == "OLD"  # clobbered dst restored
    assert restored.title == "X"  # cleared source restored


def test_apply_fields_sets_values_with_source_provenance(tmp_path):
    books, hist = _repos(tmp_path)
    b = _book(books)
    batch = apply_fields(
        books, hist, b,
        {"title": "Dune", "author": "Frank Herbert"},
        provenance="audnexus",
    )
    assert b.title == "Dune"
    assert b.authors == ["Frank Herbert"]
    assert b.provenance["title"] == "audnexus"
    assert b.provenance["authors"] == "audnexus"
    changes = hist.list_batch(batch)
    assert {c.field for c in changes} == {"title", "author"}
    assert books.get(b.id).title == "Dune"


def test_apply_fields_is_undoable(tmp_path):
    books, hist = _repos(tmp_path)
    b = _book(books)  # title "Wrong Title"
    batch = apply_fields(books, hist, b, {"title": "Right"}, provenance="openlibrary")
    undo_batch(books, hist, batch)
    assert books.get(b.id).title == "Wrong Title"


def test_bulk_apply_fields_one_batch_across_books(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.title = "old a"
    books.upsert(a)
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    b.title = "old b"
    books.upsert(b)
    batch = bulk_apply_fields(
        books,
        hist,
        [
            (a, {"title": "New A", "author": "Author A"}, "audnexus"),
            (b, {"title": "New B"}, "google"),
        ],
    )
    assert books.get(a.id).title == "New A"
    assert books.get(a.id).authors == ["Author A"]
    assert books.get(a.id).provenance["title"] == "audnexus"
    assert books.get(a.id).provenance["authors"] == "audnexus"
    assert books.get(b.id).title == "New B"
    # one batch covers every change across both books
    assert {c.book_id for c in hist.list_batch(batch)} == {a.id, b.id}


def test_bulk_apply_fields_undo_reverts_all(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.title = "old a"
    books.upsert(a)
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    b.title = "old b"
    books.upsert(b)
    batch = bulk_apply_fields(
        books,
        hist,
        [(a, {"title": "New A"}, "audnexus"), (b, {"title": "New B"}, "google")],
    )
    undo_batch(books, hist, batch)
    assert books.get(a.id).title == "old a"
    assert books.get(b.id).title == "old b"
