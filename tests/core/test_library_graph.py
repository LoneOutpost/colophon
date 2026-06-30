from pathlib import Path

from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph, check_file_references


def _dir(id_, path, root="/lib"):
    return NodeRecord(id=id_, physical="directory", semantic=None, root=root, attrs={"path": path})


def _file(id_, path, root="/lib"):
    return NodeRecord(id=id_, physical="file", semantic=None, root=root, attrs={"path": path})


def _book(id_, root="/lib"):
    return NodeRecord(id=id_, physical=None, semantic="book", root=root, attrs={"book_id": "x"})


def test_from_records_indexes_nodes_by_id():
    n = _dir("d", "/lib/a")
    e = EdgeRecord(src="d", kind="contains", dst="f", root="/lib", props={})
    g = LibraryGraph.from_records([n], [e])
    assert g.nodes == {"d": n}
    assert g.edges == [e]


def test_from_records_empty():
    g = LibraryGraph.from_records([], [])
    assert g.nodes == {} and g.edges == []


def test_validity_all_present_is_empty():
    g = LibraryGraph.from_records(
        [_dir("d", "/lib/a"), _file("f", "/lib/a/x.m4b"), _book("bk")], []
    )
    report = check_file_references(g, exists=lambda p: True)
    assert report.missing_dirs == [] and report.missing_files == []


def test_validity_flags_deleted_file():
    g = LibraryGraph.from_records([_dir("d", "/lib/a"), _file("f", "/lib/a/x.m4b")], [])
    gone = {Path("/lib/a/x.m4b")}
    report = check_file_references(g, exists=lambda p: p not in gone)
    assert report.missing_files == ["f"]
    assert report.missing_dirs == []


def test_validity_prunes_files_under_missing_dir_without_probing_them():
    g = LibraryGraph.from_records(
        [_dir("d", "/lib/a"), _file("f1", "/lib/a/x.m4b"), _file("f2", "/lib/a/y.m4b")], []
    )
    probed: list[str] = []

    def exists(p: Path) -> bool:
        probed.append(str(p))
        return False  # the directory is gone

    report = check_file_references(g, exists=exists)
    assert report.missing_dirs == ["d"]
    assert set(report.missing_files) == {"f1", "f2"}
    assert probed == ["/lib/a"]  # files under the missing dir were pruned, not probed


def test_validity_skips_book_and_entity_nodes():
    g = LibraryGraph.from_records([_book("bk")], [])
    report = check_file_references(g, exists=lambda p: False)
    assert report.missing_dirs == [] and report.missing_files == []


def test_validity_treats_unprobeable_path_as_present_not_a_crash():
    # A probe that raises (e.g. permission denied) must never crash the check; the
    # path is treated as present so startup is not taken down by one unreadable dir.
    def boom(p: Path) -> bool:
        raise PermissionError("denied")

    g = LibraryGraph.from_records([_dir("d", "/lib/a"), _file("f", "/lib/a/x.m4b")], [])
    report = check_file_references(g, exists=boom)
    assert report.missing_dirs == [] and report.missing_files == []


def test_validity_real_filesystem_default(tmp_path):
    # Exercise the production default (exists=Path.exists), which every other test injects.
    present = tmp_path / "here.m4b"
    present.write_bytes(b"\x00")
    g = LibraryGraph.from_records(
        [
            _dir("d", str(tmp_path)),
            _file("f_ok", str(present)),
            _file("f_gone", str(tmp_path / "gone.m4b")),
        ],
        [],
    )
    report = check_file_references(g)  # no injection — real Path.exists
    assert report.missing_dirs == []
    assert report.missing_files == ["f_gone"]
