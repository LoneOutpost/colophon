from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.graph import FileRole
from colophon.core.models import BookUnit
from colophon.services.graph_build import build_graph


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def test_build_graph_makes_a_book_node_per_unit_owning_its_files(tmp_path):
    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    (dune / "01.mp3").write_bytes(b"")
    (dune / "02.mp3").write_bytes(b"")

    g = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")

    book_id = BookUnit.id_for(dune)
    assert book_id in g.books
    bn = g.books[book_id]
    owned = [g.files[fid] for fid in bn.owns]
    assert {f.path.name for f in owned} == {"01.mp3", "02.mp3"}
    assert all(f.role is FileRole.AUDIO for f in owned)
    assert g.directories[bn.dir_id].path == dune


def test_project_reconstructs_folder_and_files_from_nodes(tmp_path):
    from colophon.services.graph_build import project

    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    (dune / "01.mp3").write_bytes(b"")

    g = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")
    books = project(g)

    assert len(books) == 1
    b = books[0]
    assert b.source_folder == dune
    assert [sf.path.name for sf in b.source_files] == ["01.mp3"]


def test_same_title_duplicates_split_into_separate_books(tmp_path):
    from colophon.services.graph_build import project
    # Two files that are the same title (one with a parenthetical subtitle) with no chapter numbers
    # are separate editions, not one multi-file "chapters" book: each becomes its own book unit.
    author = tmp_path / "ingest" / "Susan Freinkel"
    author.mkdir(parents=True)
    (author / "Plastic.mp3").write_bytes(b"")
    (author / "Plastic (A Toxic Love Story).mp3").write_bytes(b"")

    books = project(build_graph(_repo(tmp_path), tmp_path / "ingest", template="$Author - $Title"))

    assert len(books) == 2
    assert all(len(b.source_files) == 1 for b in books)
    assert {sf.path.name for b in books for sf in b.source_files} == {
        "Plastic.mp3", "Plastic (A Toxic Love Story).mp3",
    }


def _by_id(books):
    return {b.id: b for b in books}


def test_graph_roundtrips_single_book_to_plan_scan(tmp_path):
    from colophon.services.graph_build import project
    from colophon.services.ingest import plan_scan

    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    expected = _by_id(plan_scan(repo, ingest, template="$Author - $Title").units)
    actual = _by_id(project(build_graph(repo, ingest, template="$Author - $Title")))

    assert set(actual) == set(expected)
    for bid, exp in expected.items():
        got = actual[bid]
        assert got.source_folder == exp.source_folder
        assert [sf.path for sf in got.source_files] == [sf.path for sf in exp.source_files]
        assert got.content_kind == exp.content_kind
        assert got.folder_kind == exp.folder_kind
        assert [w.label for w in got.detected_works] == [w.label for w in exp.detected_works]
        assert got.title == exp.title
        assert got.authors == exp.authors
        assert [s.name for s in got.series] == [s.name for s in exp.series]


def test_multi_book_folder_splits_into_one_leaf_per_work(tmp_path):
    from colophon.core.graph import leaf_id_for
    from colophon.core.models import BookUnit, ContentKind
    from colophon.services.graph_build import project

    ingest = tmp_path / "ingest"
    author = ingest / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")

    books = project(build_graph(_repo(tmp_path), ingest, template="$Author - $Title"))

    assert len(books) == 2
    by_title = {b.title: b for b in books}
    assert set(by_title) == {"Legion", "Elantris"}
    for b in books:
        assert b.content_kind is ContentKind.SINGLE
        assert len(b.source_files) == 1
        assert b.source_files[0].path.stem == b.title
        # A leaf's id is the folder+files hash, never the bare folder id.
        assert b.id != BookUnit.id_for(author)
        assert b.id == leaf_id_for(author, [b.source_files[0].path])
    assert books[0].id != books[1].id


def test_multi_book_leaf_carries_series(tmp_path):
    from colophon.services.graph_build import project

    ingest = tmp_path / "ingest"
    author = ingest / "Sarah Noffke"
    author.mkdir(parents=True)
    (author / "Alpha Wolf (Olento Research 1).mp3").write_bytes(b"")
    (author / "Bad Wolf (Olento Research 4).mp3").write_bytes(b"")

    books = project(build_graph(_repo(tmp_path), ingest, template="$Author - $Title"))

    assert len(books) == 2
    # Each leaf carries exactly the series for its own work.
    for b in books:
        assert len(b.series) == 1
        assert b.series[0].name == "Olento Research"


def test_single_book_folder_is_unchanged(tmp_path):
    from colophon.core.models import BookUnit, ContentKind
    from colophon.services.graph_build import project

    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    (dune / "01.mp3").write_bytes(b"")
    (dune / "02.mp3").write_bytes(b"")

    books = project(build_graph(_repo(tmp_path), ingest, template="$Author - $Title"))

    assert len(books) == 1
    b = books[0]
    assert b.id == BookUnit.id_for(dune)
    assert b.content_kind is not ContentKind.MULTI
    assert {sf.path.name for sf in b.source_files} == {"01.mp3", "02.mp3"}


def test_leaf_inherits_container_author_when_work_has_none(tmp_path):
    from colophon.core.models import Provenance
    from colophon.services.graph_build import project

    # An author folder of loose, untagged single-file works → MULTI container whose
    # author IDENTIFY resolves to the folder name; leaves inherit it.
    ingest = tmp_path / "ingest"
    author = ingest / "Sarah Graves"
    author.mkdir(parents=True)
    for n in ("Dead Cat Bounce (Home Repair is Homicide 1).mp3",
              "A Face at the Window (Home Repair is Homicide 12).mp3",
              "Death by Chocolate Malted Milkshake (Death by Chocolate 2).mp3"):
        (author / n).write_bytes(b"")

    books = project(build_graph(_repo(tmp_path), ingest, template="$Author - $Title"))

    assert len(books) == 3
    for b in books:
        assert b.authors == ["Sarah Graves"]
        assert b.provenance["authors"] == Provenance.DIRECTORY.value


def test_build_graph_threads_new_only_scope(tmp_path):
    from colophon.core.models import BookUnit
    from colophon.services.ingest import ScanOptions, ScanScope

    ingest = tmp_path / "ingest"
    dune = ingest / "Dune"
    dune.mkdir(parents=True)
    (dune / "01.mp3").write_bytes(b"")

    repo = _repo(tmp_path)
    # Pre-persist the Dune book so NEW_ONLY must skip it.
    repo.upsert(BookUnit.new(source_folder=dune))

    g = build_graph(repo, ingest, template="$Author - $Title",
                    options=ScanOptions(scope=ScanScope.NEW_ONLY))
    assert g.books == {}  # known folder skipped → no book nodes


def test_build_graph_materializes_ancestor_dirs(tmp_path):
    from colophon.core.graph import DirectoryNode

    ingest = tmp_path / "ingest"
    folder = ingest / "up" / "Author" / "book"
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"")

    g = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")

    up_id = DirectoryNode.id_for(ingest / "up")
    author_id = DirectoryNode.id_for(ingest / "up" / "Author")
    book_id = DirectoryNode.id_for(folder)
    assert up_id in g.directories and author_id in g.directories
    assert author_id in g.directories[up_id].child_dirs       # up -> Author
    assert book_id in g.directories[author_id].child_dirs      # Author -> book
    assert DirectoryNode.id_for(ingest) in g.directories       # root materialized
    assert DirectoryNode.id_for(ingest.parent) not in g.directories  # stops at root
