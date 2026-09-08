"""A disc-split book stays one book, even when its discs disagree about their own tags.

Found by scanning the real library: `Tom Clancy - Shadow warriors` is six numbered disc folders,
and the collapse folds them into one unit correctly — but the embedded tags disagree disc to disc
(`Tom Chancy` / `Shadow Warriors`, then no artist and album `Disk3`, then
`Tom Clancy Shadow Warriors Last Disk`), so the grouping election split the folded book back into
two along exactly that tag boundary.

Six numbered disc folders under one parent is far harder evidence than a typo'd artist field, so a
folded unit asserts "one book" to the classifier — the same assertion a user's manual Combine makes.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.id3 import ID3, TALB, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _mp3(path: Path, *, artist: str | None = None, album: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    if artist is not None:
        tags.add(TPE1(encoding=3, text=[artist]))
    if album is not None:
        tags.add(TALB(encoding=3, text=[album]))
    tags.save(path)


def _ctrl(tmp_path: Path):
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    return ctx, AppController(ctx), ingest


def test_discs_that_disagree_about_their_tags_are_still_one_book(tmp_path):
    # The real Shadow Warriors tag spread, reproduced.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    book = ingest / "Tom Clancy - Shadow warriors"
    for disc, artist, album in (
        ("Shadow Warriors - Disk1", "Tom Chancy", "Shadow Warriors"),
        ("Shadow Warriors - Disk2", "Tom Chancy", "Shadow Warriors"),
        ("Shadow Warriors - Disk3", None, "Disk3"),
        ("Shadow Warriors - Disk4", None, "Disk4"),
        ("Shadow Warriors - Disk5", None, "Disk5"),
        ("Shadow Warriors - Disk6", "Tom Clancy Shadow Warriors Last Disk", None),
    ):
        for track in ("01. Track 1.mp3", "02. Track 2.mp3"):
            _mp3(book / disc / track, artist=artist, album=album)

    plan = ctrl.scan_preview([ingest])

    assert len(plan.units) == 1, (
        f"the disc split was re-divided into {len(plan.units)} books: "
        f"{[len(u.source_files) for u in plan.units]}"
    )
    assert len(plan.units[0].source_files) == 12
    assert plan.units[0].source_folder == book
    ctx.close()


def test_a_genuine_multi_book_folder_is_still_split(tmp_path):
    # The assertion is scoped to folded units. An ordinary folder holding two books must still
    # split — folding must not become a blanket "never split anything".
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Two Books"
    _mp3(folder / "Dune.mp3", artist="Frank Herbert", album="Dune")
    _mp3(folder / "Messiah.mp3", artist="Frank Herbert", album="Dune Messiah")

    plan = ctrl.scan_preview([ingest])

    assert len(plan.units) == 2, "a real multi-book folder stopped splitting"
    ctx.close()


def test_upgrading_a_library_prunes_the_old_per_disc_books(tmp_path):
    # The upgrade path, which a fresh-database test cannot exercise: a library scanned BEFORE the
    # fold holds one book per disc folder. After the fold those folders produce no unit, so the
    # scan reconcile never visited them and their rows survived — leaving the real book plus a
    # ghost per disc, every file counted twice, and cleanup unable to see them because the files
    # exist and are inside a scan path.
    from colophon.core.models import BookUnit, SourceFile

    ctx, ctrl, ingest = _ctrl(tmp_path)
    book = ingest / "Innocent in Death"
    for disc in ("CD01", "CD02", "CD03"):
        for track in ("01.mp3", "02.mp3"):
            _mp3(book / disc / track, artist="J D Robb")
    # Seed the pre-fold state: one book per disc folder.
    for disc in ("CD01", "CD02", "CD03"):
        folder = book / disc
        stale = BookUnit.new(source_folder=folder)
        stale.title = disc.title()
        stale.source_files = [
            SourceFile(path=folder / t, size=1, duration_seconds=60.0, ext="mp3")
            for t in ("01.mp3", "02.mp3")
        ]
        ctx.books.upsert(stale)
    assert len(ctx.books.list_all()) == 3

    ctrl.scan([ingest])

    rows = ctx.books.list_all()
    assert len(rows) == 1, (
        "per-disc books survived the upgrade: "
        f"{sorted(str(b.source_folder.name) for b in rows)}"
    )
    assert rows[0].source_folder == book
    assert len(rows[0].source_files) == 6
    ctx.close()
