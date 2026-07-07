"""Controller-level tests for the Utilities clean-up action."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import BookUnit


def _ctx(tmp_path, scan_paths):
    return AppContext.create(
        Config(
            db_path=tmp_path / "db.sqlite",
            library_root=tmp_path / "lib",
            scan_paths=scan_paths,
        )
    )


def test_cleanup_report_buckets_persisted_books(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    ctx = _ctx(tmp_path, [scan])
    gone = BookUnit.new(source_folder=scan / "Gone")            # under scan, missing
    outside = BookUnit.new(source_folder=tmp_path / "away" / "X")  # outside scan
    ctx.books.upsert(gone)
    ctx.books.upsert(outside)

    report = AppController(ctx).cleanup_report()

    assert {c.book_id for c in report.removed_from_disk} == {gone.id}
    assert {c.book_id for c in report.outside_scan_paths} == {outside.id}
    ctx.close()
