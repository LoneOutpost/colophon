from colophon.adapters.repository.store import GraphStore, connect, migrate, save_graph
from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph


def _store(tmp_path) -> GraphStore:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return GraphStore(conn)


def _n(id_, root, physical="directory", semantic=None):
    return NodeRecord(id=id_, physical=physical, semantic=semantic, root=str(root), attrs={"path": id_})


def _e(src, dst, root, kind="contains"):
    return EdgeRecord(src=src, kind=kind, dst=dst, root=str(root), props={})


def test_replace_subgraph_round_trip(tmp_path):
    s = _store(tmp_path)
    root = tmp_path / "lib"
    s.replace_subgraph(root, [_n("a", root), _n("b", root, physical="file")], [_e("a", "b", root)])
    assert {n.id for n in s.nodes_for(root)} == {"a", "b"}
    assert {(e.src, e.kind, e.dst) for e in s.edges_for(root)} == {("a", "contains", "b")}


def test_replace_is_wholesale_for_the_root(tmp_path):
    s = _store(tmp_path)
    root = tmp_path / "lib"
    s.replace_subgraph(root, [_n("a", root), _n("b", root)], [_e("a", "b", root)])
    s.replace_subgraph(root, [_n("a", root)], [])  # rescan drops b
    assert {n.id for n in s.nodes_for(root)} == {"a"}
    assert s.edges_for(root) == []


def test_roots_are_independent(tmp_path):
    s = _store(tmp_path)
    r1, r2 = tmp_path / "one", tmp_path / "two"
    s.replace_subgraph(r1, [_n("x", r1)], [])
    s.replace_subgraph(r2, [_n("y", r2)], [])
    s.replace_subgraph(r1, [_n("x2", r1)], [])  # replacing r1 leaves r2 intact
    assert {n.id for n in s.nodes_for(r1)} == {"x2"}
    assert {n.id for n in s.nodes_for(r2)} == {"y"}


def test_replace_reclaims_a_node_id_lingering_under_another_root(tmp_path):
    # nodes.id is a global PK, but a physical id can linger under a stale/renamed root string. Writing
    # the same id under a new root must reclaim it (move it), not raise UNIQUE constraint failed.
    s = _store(tmp_path)
    old, new = tmp_path / "old", tmp_path / "new"
    s.replace_subgraph(old, [_n("shared", old)], [])
    s.replace_subgraph(new, [_n("shared", new)], [])  # would collide before the reclaim fix
    assert [n.root for n in s.nodes_for(new)] == [str(new)]  # now owned by the new root
    assert s.nodes_for(old) == []                            # gone from the old root


def test_replace_drops_duplicate_ids_within_one_batch(tmp_path):
    s = _store(tmp_path)
    root = tmp_path / "lib"
    s.replace_subgraph(root, [_n("dup", root), _n("dup", root, physical="file")], [])
    assert {n.id for n in s.nodes_for(root)} == {"dup"}  # deduped, no UNIQUE failure


def test_replace_reclaims_an_edge_lingering_under_another_root(tmp_path):
    # edges.(src, kind, dst) is a global UNIQUE key, but a partial rescan re-homes a folder's edges
    # from the broad scan-root string to the folder string. Writing the same edge under a new root
    # must reclaim it (move it), not raise UNIQUE constraint failed — the bug that made a
    # selection-scoped rescan crash on any library first persisted by a full scan.
    s = _store(tmp_path)
    old, new = tmp_path / "old", tmp_path / "new"
    s.replace_subgraph(old, [_n("a", old), _n("b", old)], [_e("a", "b", old)])
    s.replace_subgraph(new, [_n("a", new), _n("b", new)], [_e("a", "b", new)])  # would collide before
    assert [e.root for e in s.edges_for(new)] == [str(new)]  # now owned by the new root
    assert s.edges_for(old) == []                            # gone from the old root


def test_replace_drops_duplicate_edges_within_one_batch(tmp_path):
    s = _store(tmp_path)
    root = tmp_path / "lib"
    s.replace_subgraph(
        root, [_n("a", root), _n("b", root)], [_e("a", "b", root), _e("a", "b", root)])
    assert {(e.src, e.kind, e.dst) for e in s.edges_for(root)} == {("a", "contains", "b")}  # deduped


def test_node_attrs_facets_and_owns_edge_round_trip(tmp_path):
    s = _store(tmp_path)
    root = tmp_path / "lib"
    book = NodeRecord(id="bk", physical=None, semantic="book", root=str(root), attrs={"book_id": "xyz"})
    s.replace_subgraph(root, [book, _n("f", root, physical="file")], [_e("bk", "f", root, kind="owns")])
    bn = next(n for n in s.nodes_for(root) if n.id == "bk")
    assert bn.physical is None and bn.semantic == "book" and bn.attrs == {"book_id": "xyz"}
    assert {(e.src, e.kind, e.dst) for e in s.edges_for(root)} == {("bk", "owns", "f")}


def test_load_all_returns_every_root(tmp_path):
    s = _store(tmp_path)
    r1, r2 = tmp_path / "one", tmp_path / "two"
    s.replace_subgraph(r1, [_n("x", r1)], [])
    s.replace_subgraph(r2, [_n("y", r2)], [_e("y", "y", r2)])
    nodes, edges = s.load_all()
    assert {n.id for n in nodes} == {"x", "y"}
    assert {(e.src, e.kind, e.dst) for e in edges} == {("y", "contains", "y")}


def test_load_all_empty_store(tmp_path):
    s = _store(tmp_path)
    nodes, edges = s.load_all()
    assert nodes == [] and edges == []


def test_load_all_preserves_facets_and_attrs(tmp_path):
    s = _store(tmp_path)
    root = tmp_path / "lib"
    book = NodeRecord(id="bk", physical=None, semantic="book", root=str(root), attrs={"book_id": "xyz"})
    s.replace_subgraph(root, [book], [])
    nodes, _ = s.load_all()
    (n,) = nodes
    assert n.physical is None and n.semantic == "book" and n.attrs == {"book_id": "xyz"}


def test_save_graph_round_trips_via_load_all(tmp_path):
    s = _store(tmp_path)
    r1, r2 = tmp_path / "one", tmp_path / "two"
    s.replace_subgraph(r1, [_n("x", r1)], [])
    s.replace_subgraph(r2, [_n("y", r2)], [_e("y", "y", r2)])
    graph = LibraryGraph.from_records(*s.load_all())
    save_graph(s, graph)
    nodes2, edges2 = s.load_all()
    assert {n.id for n in nodes2} == {"x", "y"}
    assert {(e.src, e.kind, e.dst) for e in edges2} == {("y", "contains", "y")}
