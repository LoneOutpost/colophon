import json
from pathlib import Path

from mutagen.id3 import ID3, TPE1, TXXX

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import (
    BookState,
    BookUnit,
    ContentKind,
    FolderKind,
    Phase,
    PhaseState,
    Provenance,
)
from colophon.core.phases import state_of
from colophon.services.ingest import scan_ingest


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def test_scan_ingest_persists_book_units(tmp_path: Path):
    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    f = dune / "01.mp3"
    f.write_bytes(b"")
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["Frank Herbert"]))
    id3.save(f)

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="$Author - $Title")

    assert len(units) == 1
    book = units[0]
    # Local phases run during scan; confidence=0 → NEEDS_REVIEW (IDENTIFY FRESH, low confidence)
    assert book.state == BookState.NEEDS_REVIEW
    assert state_of(book, Phase.SEARCH) is PhaseState.FRESH
    assert state_of(book, Phase.CATEGORIZE) is PhaseState.FRESH
    assert state_of(book, Phase.IDENTIFY) is PhaseState.FRESH
    assert book.title == "Dune"  # from directory name
    assert book.provenance["title"] == Provenance.DIRECTORY.value
    assert book.authors == ["Frank Herbert"]  # from embedded TPE1
    assert book.provenance["authors"] == Provenance.TAG.value
    assert len(book.source_files) == 1
    # persisted and retrievable
    assert repo.get(book.id) is not None


def test_first_file_drives_metadata_with_numeric_sort(tmp_path: Path):
    ingest = tmp_path / "ingest"
    book_dir = ingest / "Book"
    book_dir.mkdir(parents=True)
    (book_dir / "2.mp3").write_bytes(b"")
    (book_dir / "10.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="$Author - $Title")

    assert len(units) == 1
    book = units[0]
    assert len(book.source_files) == 2
    # Scanner natural-sorts files, so the lowest track number drives metadata.
    assert book.source_files[0].path.name == "2.mp3"


def test_non_matching_filename_falls_back_to_dir_and_tags(tmp_path: Path):
    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    (dune / "weird_name_no_delimiters.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="$Author - $Title")

    assert len(units) == 1
    book = units[0]
    assert book.title == "Dune"


def test_scan_ingest_is_idempotent(tmp_path: Path):
    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    scan_ingest(repo, ingest, template="$Author - $Title")
    scan_ingest(repo, ingest, template="$Author - $Title")
    assert len(repo.list_all()) == 1  # same folder -> same id -> upsert


def test_scan_ingest_uses_sidecar_for_series(tmp_path):
    ingest = tmp_path / "ingest"
    book_dir = ingest / "Dirk Gently"
    book_dir.mkdir(parents=True)
    (book_dir / "01.mp3").write_bytes(b"")
    (book_dir / "metadata.json").write_text(json.dumps({
        "title": "Dirk Gently", "authors": ["Douglas Adams"],
        "narrators": ["Douglas Adams"], "series": ["Dirk Gently #1"], "publishedYear": "1987",
    }))

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="$Author - $Title")
    book = units[0]
    assert book.series[0].name == "Dirk Gently"
    assert book.series[0].sequence == 1.0
    assert book.narrators == ["Douglas Adams"]
    assert book.publish_year == 1987
    assert book.provenance["series"] == "datafile"


def test_scan_infers_author_from_directory_scheme(tmp_path: Path):
    ingest = tmp_path / "ingest"
    folder = ingest / "Brandon Sanderson" / "Warbreaker"
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, ingest, template="$Title", directory_scheme="$Author/$Title")
    assert len(units) == 1
    book = units[0]
    assert book.authors == ["Brandon Sanderson"]
    assert book.provenance["authors"] == "directory"
    assert book.title == "Warbreaker"


def test_rescan_preserves_app_state_and_fills_empty(tmp_path):
    from colophon.services.ingest import commit_scan, plan_scan

    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    f = dune / "01.mp3"
    f.write_bytes(b"")
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["Frank Herbert"]))
    id3.save(f)

    repo = _repo(tmp_path)
    commit_scan(repo, plan_scan(repo, ingest, template="$Author - $Title"))
    book = repo.list_all()[0]
    book.cover_path = tmp_path / "cover.jpg"
    book.confidence = 100.0
    book.state = BookState.READY
    book.genres = ["Fantasy"]
    book.title = "User Edited Title"
    repo.upsert(book)

    plan = plan_scan(repo, ingest, template="$Author - $Title")
    assert plan.new_books == 0
    assert plan.existing_books == 1
    commit_scan(repo, plan)

    after = repo.get(book.id)
    assert after.cover_path == tmp_path / "cover.jpg"
    assert after.confidence == 100.0
    assert after.state == BookState.READY
    assert after.genres == ["Fantasy"]
    assert after.title == "User Edited Title"


def test_plan_scan_does_not_persist(tmp_path):
    from colophon.services.ingest import plan_scan

    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    f = dune / "01.mp3"
    f.write_bytes(b"")
    id3 = ID3()
    id3.add(TPE1(encoding=3, text=["Frank Herbert"]))
    id3.save(f)

    repo = _repo(tmp_path)
    plan = plan_scan(repo, ingest, template="$Author - $Title")
    assert plan.new_books == 1
    assert repo.list_all() == []


def test_container_datafile_ignored_for_multi_folder(tmp_path):
    incoming = tmp_path / "incoming"
    folder = incoming / "TE_Audiobooks_S" / "Sarah Graves"
    folder.mkdir(parents=True)
    for n in ("Dead Cat Bounce (Home Repair is Homicide 1).mp3",
              "A Face at the Window (Home Repair is Homicide 12).mp3",
              "Death by Chocolate Malted Milkshake (Death by Chocolate 2).mp3"):
        (folder / n).write_bytes(b"")
    (folder / "metadata.json").write_text(json.dumps(
        {"title": "Sarah Graves", "authors": ["TE_Audiobooks_S"]}))

    repo = _repo(tmp_path)
    units = scan_ingest(repo, incoming, template="$Author - $Title")
    book = next(u for u in units if u.source_folder == folder)
    assert book.content_kind is ContentKind.MULTI
    # The uploader handle from the datafile is rejected; the folder name (the real
    # author) is identified instead via the foster-container rule.
    assert book.authors == ["Sarah Graves"]
    assert book.provenance.get("authors") == "directory"


def test_matching_name_datafile_kept_for_single_folder(tmp_path):
    lib = tmp_path / "lib"
    folder = lib / "Brandon Sanderson" / "Elantris"
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"")
    (folder / "metadata.json").write_text(json.dumps(
        {"title": "Elantris", "authors": ["Brandon Sanderson"]}))

    repo = _repo(tmp_path)
    units = scan_ingest(repo, lib, template="$Author - $Title")
    book = units[0]
    assert book.content_kind is ContentKind.SINGLE
    assert book.authors == ["Brandon Sanderson"]
    assert book.provenance.get("authors") == "datafile"


def test_foster_container_author_is_folder_name(tmp_path):
    incoming = tmp_path / "incoming"
    folder = incoming / "TE_Audiobooks_S" / "Sarah Graves"
    folder.mkdir(parents=True)
    for n in ("Dead Cat Bounce (Home Repair is Homicide 1).mp3",
              "A Face at the Window (Home Repair is Homicide 12).mp3",
              "Death by Chocolate Malted Milkshake (Death by Chocolate 2).mp3"):
        (folder / n).write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, incoming, template="$Author - $Title")
    book = next(u for u in units if u.source_folder == folder)
    assert book.content_kind is ContentKind.MULTI
    assert book.authors == ["Sarah Graves"]
    assert book.provenance["authors"] == "directory"


def test_title_folder_split_gets_no_guessed_author(tmp_path):
    incoming = tmp_path / "incoming"
    folder = incoming / "Legion"  # a title folder holding two different works
    folder.mkdir(parents=True)
    (folder / "Legion.mp3").write_bytes(b"")
    (folder / "Elantris.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    units = scan_ingest(repo, incoming, template="$Author - $Title",
                        directory_scheme="$Title")
    book = units[0]
    assert book.folder_kind is FolderKind.TITLE
    assert book.authors == []


def test_commit_scan_reconcile_prunes_replaced_books(tmp_path: Path):
    from colophon.services.ingest import ScanPlan, commit_scan

    repo = _repo(tmp_path)
    folder = Path("/ingest/Author/Multi")
    container = BookUnit.new(source_folder=folder)          # id = id_for(folder)
    leaf_keep = BookUnit.new(source_folder=folder)
    leaf_keep.id = "1111111111111111"
    leaf_orphan = BookUnit.new(source_folder=folder)
    leaf_orphan.id = "2222222222222222"
    for b in (container, leaf_keep, leaf_orphan):
        repo.upsert(b)

    # New scan of `folder` yields only leaf_keep + a brand-new leaf.
    leaf_new = BookUnit.new(source_folder=folder)
    leaf_new.id = "3333333333333333"
    plan = ScanPlan(units=[leaf_keep, leaf_new], reconciled_folders={folder})

    written = commit_scan(repo, plan, reconcile=True)

    assert written == 2
    assert repo.get(container.id) is None      # stale container pruned
    assert repo.get(leaf_orphan.id) is None    # orphan leaf pruned
    assert repo.get(leaf_keep.id) is not None  # kept
    assert repo.get(leaf_new.id) is not None   # added


def test_commit_scan_without_reconcile_keeps_everything(tmp_path: Path):
    from colophon.services.ingest import ScanPlan, commit_scan

    repo = _repo(tmp_path)
    folder = Path("/ingest/Author/Multi")
    stale = BookUnit.new(source_folder=folder)
    stale.id = "4444444444444444"
    repo.upsert(stale)
    keep = BookUnit.new(source_folder=folder)
    keep.id = "5555555555555555"

    commit_scan(repo, ScanPlan(units=[keep], reconciled_folders={folder}))  # reconcile defaults False

    assert repo.get(stale.id) is not None  # nothing pruned without reconcile
    assert repo.get(keep.id) is not None


def test_plan_scan_graph_persists_leaves_not_container(tmp_path: Path):
    from colophon.services.ingest import commit_scan, plan_scan_graph

    ingest = tmp_path / "ingest"
    author = ingest / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    plan = plan_scan_graph(repo, ingest, template="$Author - $Title")
    commit_scan(repo, plan, reconcile=True)

    persisted = repo.list_all()
    assert len(persisted) == 2
    assert {b.title for b in persisted} == {"Legion", "Elantris"}
    assert all(b.content_kind is ContentKind.SINGLE for b in persisted)
    assert repo.get(BookUnit.id_for(author)) is None  # no container row
    assert author in plan.reconciled_folders


def test_plan_scan_graph_enriches_leaf_from_its_tags(tmp_path: Path):
    from colophon.services.ingest import plan_scan_graph

    ingest = tmp_path / "ingest"
    author = ingest / "Sarah Graves"
    author.mkdir(parents=True)
    f = author / "Dead Cat Bounce (Home Repair is Homicide 1).mp3"
    f.write_bytes(b"")
    (author / "A Face at the Window (Home Repair is Homicide 12).mp3").write_bytes(b"")
    id3 = ID3()
    id3.add(TXXX(encoding=3, desc="narrator", text=["Read By Me"]))
    id3.save(f)

    repo = _repo(tmp_path)
    plan = plan_scan_graph(repo, ingest, template="$Author - $Title")
    leaf = next(u for u in plan.units if u.title == "Dead Cat Bounce")

    assert leaf.authors == ["Sarah Graves"]                 # cluster/container identity kept
    assert leaf.provenance["authors"] == Provenance.DIRECTORY.value
    assert leaf.narrators == ["Read By Me"]                 # empty field enriched from the tag
    assert leaf.provenance["narrators"] == Provenance.TAG.value
    assert state_of(leaf, Phase.IDENTIFY) is PhaseState.FRESH


def test_plan_scan_graph_preserves_leaf_state_on_rescan(tmp_path: Path):
    from colophon.services.ingest import (
        ScanOptions,
        ScanScope,
        commit_scan,
        plan_scan_graph,
    )

    ingest = tmp_path / "ingest"
    author = ingest / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    commit_scan(repo, plan_scan_graph(repo, ingest, template="$Author - $Title"), reconcile=True)

    legion = next(b for b in repo.list_all() if b.title == "Legion")
    legion.manually_confirmed = True
    legion.cover_path = Path("/covers/legion.jpg")
    legion.narrators = ["A Narrator"]
    legion.state = BookState.READY
    repo.upsert(legion)

    # Re-scan (UPDATE) the same folder.
    plan = plan_scan_graph(
        repo, ingest, template="$Author - $Title",
        options=ScanOptions(scope=ScanScope.UPDATE),
    )
    commit_scan(repo, plan, reconcile=True)

    again = repo.get(legion.id)
    assert again is not None
    assert again.manually_confirmed is True
    assert again.cover_path == Path("/covers/legion.jpg")
    assert again.narrators == ["A Narrator"]
    assert again.state is BookState.READY


def test_plan_scan_graph_prunes_legacy_container_on_reprocess(tmp_path: Path):
    from colophon.services.ingest import (
        ScanOptions,
        ScanScope,
        commit_scan,
        plan_scan_graph,
    )

    ingest = tmp_path / "ingest"
    author = ingest / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    # Simulate a pre-2b persisted MULTI container at id_for(folder).
    container = BookUnit.new(source_folder=author)
    container.content_kind = ContentKind.MULTI
    container.title = "Brandon Sanderson"
    repo.upsert(container)

    plan = plan_scan_graph(
        repo, ingest, template="$Author - $Title",
        options=ScanOptions(scope=ScanScope.REFRESH),
    )
    commit_scan(repo, plan, reconcile=True)

    assert repo.get(BookUnit.id_for(author)) is None  # legacy container pruned
    assert {b.title for b in repo.list_all()} == {"Legion", "Elantris"}


def test_plan_scan_graph_new_only_does_not_prune_known_folder(tmp_path: Path):
    from colophon.services.ingest import (
        ScanOptions,
        ScanScope,
        commit_scan,
        plan_scan_graph,
    )

    ingest = tmp_path / "ingest"
    author = ingest / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    container = BookUnit.new(source_folder=author)
    container.content_kind = ContentKind.MULTI
    repo.upsert(container)

    plan = plan_scan_graph(
        repo, ingest, template="$Author - $Title",
        options=ScanOptions(scope=ScanScope.NEW_ONLY),
    )
    commit_scan(repo, plan, reconcile=True)

    # NEW_ONLY skips the known folder → it's not reconciled → container survives.
    assert author not in plan.reconciled_folders
    assert repo.get(BookUnit.id_for(author)) is not None
