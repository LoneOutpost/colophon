from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.services.ingest import plan_rescan_folders


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _tagged(path: Path, artist: str) -> None:
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def test_scoped_rebuild_resplits_multibook_folder_without_ballooning(tmp_path):
    # A selection-scoped rebuild goes through the full graph scan, so each book in a shared folder
    # owns only its own file (no ballooning) and an authorless sibling does not inherit the first
    # file's tag author (the regression that assigned every book "Armin Shimmerman").
    ingest = tmp_path / "ingest"
    dump = ingest / "Star Trek"
    dump.mkdir(parents=True)
    _tagged(dump / "34th Rule.mp3", "Armin Shimmerman")   # sorts first -> old code's container author
    (dump / "Final Frontier.mp3").write_bytes(b"")         # untagged, needs identification

    plan = plan_rescan_folders(
        _repo(tmp_path), [dump], template="$Author - $Title",
        inference_root_for=lambda f: ingest, known_franchises={"star trek": "Star Trek"},
    )

    by_title = {b.title: b for b in plan.units}
    assert set(by_title) == {"34th Rule", "Final Frontier"}
    for b in plan.units:
        assert len(b.source_files) == 1                    # no ballooning
    assert by_title["Final Frontier"].authors == []        # untagged sibling stays authorless
    assert by_title["34th Rule"].authors == ["Armin Shimmerman"]   # keeps its own tag author


def test_scoped_rebuild_touches_only_the_named_folders(tmp_path):
    ingest = tmp_path / "ingest"
    (ingest / "Author A").mkdir(parents=True)
    (ingest / "Author A" / "Book A.mp3").write_bytes(b"")
    (ingest / "Author B").mkdir(parents=True)
    (ingest / "Author B" / "Book B.mp3").write_bytes(b"")

    plan = plan_rescan_folders(
        _repo(tmp_path), [ingest / "Author A"], template="$Author - $Title",
        inference_root_for=lambda f: ingest,
    )

    assert [u.source_folder for u in plan.units] == [ingest / "Author A"]
    assert plan.new_books == 1
