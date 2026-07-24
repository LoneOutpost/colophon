"""scan_plan_changes diffs a computed scan plan's would-be books against the currently-stored ones,
so the scan dialog can show what a re-scan will change to existing books before it is applied."""

from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit, SeriesRef
from colophon.services.ingest import ScanPlan


def _ctx(tmp_path):
    return AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"]))


def _book(bid: str, folder: Path, *, title=None, authors=(), series=None) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    b.id = bid
    b.title = title
    b.authors = list(authors)
    b.series = [SeriesRef(name=series)] if series else []
    return b


def test_scan_plan_changes_reports_existing_book_field_changes(tmp_path):
    ctx = _ctx(tmp_path)
    c = AppController(ctx)
    folder = tmp_path / "ingest" / "Book"
    ctx.books.upsert(_book("b1", folder, title="Old Title", authors=["Old Author"]))

    plan = ScanPlan()
    plan.units = [_book("b1", folder, title="New Title", authors=["New Author"])]

    changes = c.scan_plan_changes(plan)
    by_field = {ch.field: (ch.before, ch.after) for ch in changes}
    assert {ch.book_id for ch in changes} == {"b1"}
    assert by_field["title"] == ("Old Title", "New Title")
    assert by_field["authors"] == ("Old Author", "New Author")
    ctx.close()


def test_scan_plan_changes_ignores_new_books(tmp_path):
    ctx = _ctx(tmp_path)
    c = AppController(ctx)
    folder = tmp_path / "ingest" / "Book"
    plan = ScanPlan()
    plan.units = [_book("new1", folder, title="Fresh")]  # no stored book with this id

    assert c.scan_plan_changes(plan) == []
    ctx.close()


def test_scan_plan_changes_empty_for_a_noop_rescan(tmp_path):
    ctx = _ctx(tmp_path)
    c = AppController(ctx)
    folder = tmp_path / "ingest" / "Book"
    ctx.books.upsert(_book("b1", folder, title="Same", authors=["A"], series="S"))
    plan = ScanPlan()
    plan.units = [_book("b1", folder, title="Same", authors=["A"], series="S")]

    assert c.scan_plan_changes(plan) == []
    ctx.close()
