from pathlib import Path

from mutagen.id3 import ID3, TALB, TPE1

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.services.ingest import plan_rescan_folders, scan_ingest


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _tagged(path: Path, artist: str, album: str | None = None) -> None:
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    if album is not None:
        tags.add(TALB(encoding=3, text=[album]))
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


def test_rescan_upgrades_a_stale_weak_leaf_author_to_the_tag(tmp_path):
    # A leaf in a multi-book folder gets its author from its own artist tag (TAG provenance). A
    # library scanned before that was fixed still carries the author mislabeled as a weak FILENAME
    # guess, which forces its identity confidence to 0. A plain rescan (no removal) must upgrade the
    # stale weak value to the tag and restore the confidence — previously only remove + re-scan did.
    ingest = tmp_path / "ingest"
    dump = ingest / "Star Trek"
    dump.mkdir(parents=True)
    _tagged(dump / "34th Rule.mp3", "Armin Shimmerman", album="The 34th Rule")
    _tagged(dump / "Voyage Home.mp3", "Vonda N. McIntyre", album="The Voyage Home")

    repo = _repo(tmp_path)
    scan_ingest(repo, ingest, template="$Author - $Title")
    vh = next(b for b in repo.list_all() if b.authors == ["Vonda N. McIntyre"])
    assert vh.provenance["authors"] == "tag"          # baseline: a fresh scan gets it right
    assert vh.identity_confidence >= 80

    # Simulate a pre-fix library row: the tag author mislabeled as a weak filename guess, conf 0.
    vh.provenance["authors"] = "filename"
    vh.identity_confidence = 0.0
    repo.upsert(vh)

    scan_ingest(repo, ingest, template="$Author - $Title")   # plain rescan, book left in place

    reloaded = repo.get(vh.id)
    assert reloaded.authors == ["Vonda N. McIntyre"]
    assert reloaded.provenance["authors"] == "tag"    # weak value upgraded back to the tag
    assert reloaded.identity_confidence >= 80          # and confidence restored


def test_rescan_keeps_a_manual_leaf_author(tmp_path):
    # The refresh only touches weak (folder/filename) provenance; a manual edit the user made to a
    # leaf's author must survive a rescan untouched.
    ingest = tmp_path / "ingest"
    dump = ingest / "Star Trek"
    dump.mkdir(parents=True)
    _tagged(dump / "34th Rule.mp3", "Armin Shimmerman", album="The 34th Rule")
    _tagged(dump / "Voyage Home.mp3", "Vonda N. McIntyre", album="The Voyage Home")

    repo = _repo(tmp_path)
    scan_ingest(repo, ingest, template="$Author - $Title")
    vh = next(b for b in repo.list_all() if b.authors == ["Vonda N. McIntyre"])

    vh.authors = ["V. N. McIntyre"]                   # a deliberate manual correction
    vh.provenance["authors"] = "manual"
    repo.upsert(vh)

    scan_ingest(repo, ingest, template="$Author - $Title")

    reloaded = repo.get(vh.id)
    assert reloaded.authors == ["V. N. McIntyre"]      # manual value preserved
    assert reloaded.provenance["authors"] == "manual"
