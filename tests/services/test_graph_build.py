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


def _by_id(books):
    return {b.id: b for b in books}


def test_graph_roundtrips_to_plan_scan_single_and_multi(tmp_path):
    from colophon.services.graph_build import project
    from colophon.services.ingest import plan_scan

    ingest = tmp_path / "ingest"
    (ingest / "Dune").mkdir(parents=True)
    (ingest / "Dune" / "01.mp3").write_bytes(b"")
    author = ingest / "Brandon Sanderson"
    author.mkdir(parents=True)
    (author / "Legion.mp3").write_bytes(b"")
    (author / "Elantris.mp3").write_bytes(b"")

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
