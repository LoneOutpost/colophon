from pathlib import Path

from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph, check_file_references, reconcile


def _bookn(id_, book_id, root="/lib"):
    return NodeRecord(id=id_, physical=None, semantic="book", root=root, attrs={"book_id": book_id})


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


def test_reconcile_removes_orphan_book_nodes_and_dangling_edges():
    live = _bookn("book:live", "b1")
    orphan = _bookn("book:orphan", "gone")  # book_id no longer in the store
    d = _dir("d", "/lib/a")
    edges = [
        EdgeRecord(src="d", kind="contains", dst="book:live", root="/lib", props={}),
        EdgeRecord(src="dghost", kind="contains", dst="book:orphan", root="/lib", props={}),
        EdgeRecord(src="book:orphan", kind="owns", dst="fghost", root="/lib", props={}),
    ]
    g = LibraryGraph.from_records([live, orphan, d], edges)
    result = reconcile(g, active_roots={"/lib"}, book_ids={"b1"})
    assert result.removed_node_ids == {"book:orphan"}
    assert set(g.nodes) == {"book:live", "d"}
    assert g.edges == [edges[0]]          # only the live book's edge survives
    assert result.removed_edges == 2
    assert result.affected_roots == {"/lib"}
    assert bool(result) is True


def test_reconcile_purges_nodes_on_dead_roots():
    keep = _bookn("book:k", "b1", root="/active")
    stale = _bookn("book:s", "b2", root="/gone")  # dead root, and book gone too
    g = LibraryGraph.from_records([keep, stale], [])
    result = reconcile(g, active_roots={"/active"}, book_ids={"b1"})
    assert set(g.nodes) == {"book:k"}
    assert result.affected_roots == {"/gone"}


def test_reconcile_never_purges_a_root_with_live_books():
    # Safety net: a root missing from active_roots but still holding a live book is kept,
    # so a momentarily-empty or renamed scan-path config can't wipe live data.
    live = _bookn("book:k", "b1", root="/not-in-config")
    g = LibraryGraph.from_records([live], [])
    result = reconcile(g, active_roots=set(), book_ids={"b1"})
    assert set(g.nodes) == {"book:k"}
    assert not result


def test_reconcile_clean_graph_is_a_noop():
    live = _bookn("book:k", "b1")
    d = _dir("d", "/lib/a")
    e = EdgeRecord(src="d", kind="contains", dst="book:k", root="/lib", props={})
    g = LibraryGraph.from_records([live, d], [e])
    gen = g.generation
    result = reconcile(g, active_roots={"/lib"}, book_ids={"b1"})
    assert not result
    assert g.generation == gen            # no write counter bump when nothing changed
    assert len(g.nodes) == 2 and len(g.edges) == 1


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


def _edge(src, dst, root="/lib", kind="contains"):
    return EdgeRecord(src=src, kind=kind, dst=dst, root=root, props={})


def test_replace_root_replaces_only_that_root():
    g = LibraryGraph.from_records(
        [_dir("a", "/lib/a", root="/lib"), _dir("x", "/other/x", root="/other")],
        [_edge("a", "a", root="/lib"), _edge("x", "x", root="/other")],
    )
    g.replace_root("/lib", [_dir("a2", "/lib/a2", root="/lib")], [])
    assert set(g.nodes) == {"a2", "x"}
    assert [e.src for e in g.edges] == ["x"]


def test_replace_root_can_shrink_to_fewer_nodes():
    g = LibraryGraph.from_records(
        [_dir("a", "/lib/a", root="/lib"), _file("b", "/lib/a/b.m4b", root="/lib")],
        [_edge("a", "b", root="/lib")],
    )
    g.replace_root("/lib", [_dir("a", "/lib/a", root="/lib")], [])
    assert set(g.nodes) == {"a"}
    assert g.edges == []


def test_replace_root_absent_root_just_adds():
    g = LibraryGraph.from_records([], [])
    g.replace_root("/lib", [_dir("a", "/lib/a", root="/lib")], [])
    assert set(g.nodes) == {"a"}
