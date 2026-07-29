from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.models import SourceFile
from colophon.services.resolve import ResolveResult, compute_file_changes, resolve_scope


def _sf(name: str, size: int = 1_000_000, dur: float = 60.0) -> SourceFile:
    return SourceFile(path=Path("/lib/bk") / name, size=size, duration_seconds=dur, ext="mp3")


def test_diff_added_and_removed():
    prior = [_sf("01.mp3")]
    post = [_sf("01.mp3"), _sf("02.mp3")]
    ch = compute_file_changes(prior, post, prior_missing=False, post_missing=False, book_id="b")
    assert [p.name for p in ch.added] == ["02.mp3"]
    assert ch.removed == []
    assert not ch.is_empty
    assert "added" in ch.summary().lower()


def test_diff_corrupt_resolved_and_newly_corrupt():
    prior = [_sf("01.mp3", size=2_000_000, dur=0.0), _sf("02.mp3", dur=60.0)]
    post = [_sf("01.mp3", size=2_000_000, dur=45.0), _sf("02.mp3", size=2_000_000, dur=0.0)]
    ch = compute_file_changes(prior, post, prior_missing=False, post_missing=False, book_id="b")
    assert [p.name for p in ch.corrupt_resolved] == ["01.mp3"]
    assert [p.name for p in ch.newly_corrupt] == ["02.mp3"]


def test_diff_rename_heuristic_pairs_by_size_and_duration():
    prior = [_sf("oldname.mp3", size=5_000_000, dur=120.0)]
    post = [_sf("newname.mp3", size=5_000_000, dur=120.0)]
    ch = compute_file_changes(prior, post, prior_missing=False, post_missing=False, book_id="b")
    assert ch.added == [] and ch.removed == []
    assert [(a.name, b.name) for a, b in ch.renamed] == [("oldname.mp3", "newname.mp3")]


def test_diff_missing_transitions_and_empty_summary():
    same = [_sf("01.mp3")]
    resolved = compute_file_changes(same, same, prior_missing=True, post_missing=False, book_id="b")
    assert resolved.missing_resolved == ["b"] and resolved.newly_missing == []
    gone = compute_file_changes(same, [], prior_missing=False, post_missing=True, book_id="b")
    assert gone.newly_missing == ["b"]
    noop = compute_file_changes(same, same, prior_missing=False, post_missing=False, book_id="b")
    assert noop.is_empty and noop.summary() == "No changes on disk"


def _mp3(path: Path, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def _ctx(tmp_path):
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest])
    )
    return ctx, AppController(ctx), ingest


def _book_in(ctx, folder):
    ids = list(ctx.books.ids_in_folder(folder))
    return ctx.books.get(ids[0]) if ids else None


def test_resolve_scope_picks_up_a_new_file_in_single_book_folder(tmp_path):
    ctx, ctrl, ingest = _ctx(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")
    _mp3(ingest / "Dune" / "02.mp3")  # appeared on disk after the scan

    result = resolve_scope(
        ctx.books, book, root=ingest, template=ctx.config.filename_template,
        directory_scheme=ctx.config.directory_scheme,
    )

    assert isinstance(result, ResolveResult)
    assert [sf.path.name for sf in result.plan.units[0].source_files] == ["01.mp3", "02.mp3"]
    assert [p.name for p in result.changes.added] == ["02.mp3"]
    ctx.close()


def test_resolve_scope_does_not_touch_sibling_in_multi_book_folder(tmp_path):
    ctx, ctrl, ingest = _ctx(tmp_path)
    folder = ingest / "Shared"
    _mp3(folder / "bookA.mp3")
    _mp3(folder / "bookB.mp3")
    ctrl.scan([ingest])
    ctx.grouping.set_partition(str(folder), [["bookA.mp3"], ["bookB.mp3"]])
    ctrl.apply_scan(ctrl.scan_preview([ingest]))
    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(folder)]
    book_a = next(b for b in books if b.source_files[0].path.name == "bookA.mp3")
    book_b = next(b for b in books if b.source_files[0].path.name == "bookB.mp3")
    b_files_before = [sf.path.name for sf in book_b.source_files]

    result = resolve_scope(
        ctx.books, book_a, root=ingest, template=ctx.config.filename_template,
        directory_scheme=ctx.config.directory_scheme,
    )

    assert [sf.path.name for sf in result.plan.units[0].source_files] == ["bookA.mp3"]
    assert [sf.path.name for sf in ctx.books.get(book_b.id).source_files] == b_files_before
    ctx.close()
