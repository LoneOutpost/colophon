from pathlib import Path

from colophon.core.graph import DirectoryNode, FileNode, FileRole, Graph
from colophon.core.graph_records import book_node_id, entity_node_id, graph_records
from colophon.core.models import BookUnit, SourceFile


def _graph_single_book(root: Path):
    """A graph for root/Author/Book with two audio files, as build_graph would shape it."""
    folder = root / "Author" / "Book"
    g = Graph()
    for p in (root, root / "Author", folder):
        g.directories[DirectoryNode.id_for(p)] = DirectoryNode(path=p)
    g.directories[DirectoryNode.id_for(root)].child_dirs.append(DirectoryNode.id_for(root / "Author"))
    g.directories[DirectoryNode.id_for(root / "Author")].child_dirs.append(DirectoryNode.id_for(folder))
    f1, f2 = folder / "01.mp3", folder / "02.mp3"
    for f in (f1, f2):
        g.files[FileNode.id_for(f)] = FileNode(path=f, role=FileRole.AUDIO)
        g.directories[DirectoryNode.id_for(folder)].child_files.append(FileNode.id_for(f))
    return g, folder, [f1, f2]


def _unit(folder: Path, files: list[Path], *, unit_id: str | None = None) -> BookUnit:
    u = BookUnit.new(source_folder=folder)
    if unit_id is not None:
        u.id = unit_id
    u.source_files = [SourceFile(path=f, size=1, duration_seconds=1.0, ext=".mp3") for f in files]
    return u


def test_nodes_have_correct_facets(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    unit = _unit(folder, files)  # a real single-book id (== the folder path-hash)
    nodes, _edges = graph_records(g, [unit], root=tmp_path)
    by_id = {n.id: n for n in nodes}
    assert by_id[DirectoryNode.id_for(folder)].physical == "directory"
    assert by_id[DirectoryNode.id_for(folder)].semantic is None
    assert by_id[FileNode.id_for(files[0])].physical == "file"
    bn = by_id[book_node_id(unit.id)]
    assert bn.physical is None and bn.semantic == "book"
    assert bn.attrs["book_id"] == unit.id
    assert all(n.root == str(tmp_path) for n in nodes)


def test_book_node_id_does_not_collide_with_its_directory(tmp_path):
    # a single-book folder's BookUnit.id IS the folder path-hash, == DirectoryNode.id_for(folder);
    # namespacing the book node id keeps the two nodes distinct (no nodes-PK collision).
    g, folder, files = _graph_single_book(tmp_path)
    unit = _unit(folder, files)
    assert unit.id == DirectoryNode.id_for(folder)  # the collision this guards against
    nodes, _edges = graph_records(g, [unit], root=tmp_path)
    ids = [n.id for n in nodes]
    assert len(ids) == len(set(ids))  # no duplicate ids
    assert book_node_id(unit.id) != DirectoryNode.id_for(folder)


def test_book_node_id_comes_from_unit_not_graph(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    unit = _unit(folder, files, unit_id="durable-xyz")  # simulate a re-associated id
    nodes, edges = graph_records(g, [unit], root=tmp_path)
    bid = book_node_id("durable-xyz")
    assert any(n.id == bid and n.semantic == "book" and n.attrs["book_id"] == "durable-xyz" for n in nodes)
    assert any(e.src == bid and e.kind == "owns" for e in edges)


def test_contains_and_owns_edges(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    unit = _unit(folder, files)
    bid = book_node_id(unit.id)
    _nodes, edges = graph_records(g, [unit], root=tmp_path)
    triples = {(e.src, e.kind, e.dst) for e in edges}
    assert (DirectoryNode.id_for(tmp_path / "Author"), "contains", DirectoryNode.id_for(folder)) in triples
    assert (DirectoryNode.id_for(folder), "contains", FileNode.id_for(files[0])) in triples
    assert (DirectoryNode.id_for(folder), "contains", bid) in triples
    assert (bid, "owns", FileNode.id_for(files[0])) in triples
    assert (bid, "owns", FileNode.id_for(files[1])) in triples


def _classified_author_dir(g, folder, *, name):
    """Mark the folder's DirectoryNode as a resolved author (as resolve_graph_authors would)."""
    d = g.directories[DirectoryNode.id_for(folder)]
    d.kind = "author"
    d.author = name


def test_author_dir_gets_author_facet(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    author_dir = tmp_path / "Author"
    _classified_author_dir(g, author_dir, name="Brandon Sanderson")
    unit = _unit(folder, files)
    nodes, _edges = graph_records(g, [unit], root=tmp_path)
    by_id = {n.id: n for n in nodes}
    assert by_id[DirectoryNode.id_for(author_dir)].semantic == "author"
    assert by_id[DirectoryNode.id_for(folder)].semantic is None  # a title/book folder


def test_one_entity_per_name_with_an_edge_per_book(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    u1 = _unit(folder, files, unit_id="b1")
    u2 = _unit(tmp_path / "Author" / "Other", [tmp_path / "Author" / "Other" / "x.mp3"], unit_id="b2")
    u1.authors = ["Brandon Sanderson"]
    u1.provenance["authors"] = "tag"
    u2.authors = ["Brandon Sanderson"]
    u2.provenance["authors"] = "graphing"
    nodes, edges = graph_records(g, [u1, u2], root=tmp_path)
    eid = entity_node_id("author", "Brandon Sanderson", tmp_path)
    entity_nodes = [n for n in nodes if n.id == eid]
    assert len(entity_nodes) == 1  # deduped to one entity
    assert entity_nodes[0].semantic == "author" and entity_nodes[0].physical is None
    assert entity_nodes[0].attrs["name"] == "Brandon Sanderson"
    author_edges = [(e.src, e.dst, e.props.get("provenance")) for e in edges if e.kind == "author"]
    assert (book_node_id("b1"), eid, "tag") in author_edges
    assert (book_node_id("b2"), eid, "graphing") in author_edges


def test_name_key_dedups_order_case_and_period_variants(tmp_path):
    # entity dedup uses the existing _name_key: Last,First order, case, and periods-in-
    # initials all merge to one entity (it does NOT collapse space-between-initials).
    g, folder, files = _graph_single_book(tmp_path)
    u1 = _unit(folder, files, unit_id="b1")
    u2 = _unit(tmp_path / "Author" / "Other", [tmp_path / "Author" / "Other" / "x.mp3"], unit_id="b2")
    u1.authors = ["Robert A. Heinlein"]
    u2.authors = ["Heinlein, Robert A"]
    nodes, _edges = graph_records(g, [u1, u2], root=tmp_path)
    author_entities = [n for n in nodes if n.semantic == "author"]
    assert len(author_entities) == 1  # order + period variants merge under _name_key


def test_multi_author_book_emits_one_edge_per_author(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    u = _unit(folder, files, unit_id="b1")
    u.authors = ["Brandon Sanderson", "Janci Patterson"]
    _nodes, edges = graph_records(g, [u], root=tmp_path)
    dsts = {e.dst for e in edges if e.kind == "author" and e.src == book_node_id("b1")}
    assert dsts == {entity_node_id("author", "Brandon Sanderson", tmp_path),
                    entity_node_id("author", "Janci Patterson", tmp_path)}


def test_series_edge_carries_sequence(tmp_path):
    from colophon.core.models import SeriesRef

    g, folder, files = _graph_single_book(tmp_path)
    u = _unit(folder, files, unit_id="b1")
    u.series = [SeriesRef(name="Mistborn", sequence=1.0)]
    u.provenance["series"] = "tag"
    _nodes, edges = graph_records(g, [u], root=tmp_path)
    series_edges = [e for e in edges if e.kind == "series"]
    assert len(series_edges) == 1
    e = series_edges[0]
    assert e.dst == entity_node_id("series", "Mistborn", tmp_path)
    assert e.props["sequence"] == 1.0 and e.props["provenance"] == "tag"


def test_franchise_edge_from_ancestor_dir(tmp_path):
    # a franchise-classified ancestor directory yields a book->franchise edge
    g, folder, files = _graph_single_book(tmp_path)
    fdir = tmp_path / "Author"  # pretend this ancestor was classified franchise
    fnode = g.directories[DirectoryNode.id_for(fdir)]
    fnode.kind = "franchise"
    fnode.kind_value = "Cosmere"
    u = _unit(folder, files, unit_id="b1")
    _nodes, edges = graph_records(g, [u], root=tmp_path)
    fid = entity_node_id("franchise", "Cosmere", tmp_path)
    franchise_edges = [(e.src, e.dst, e.props.get("provenance")) for e in edges if e.kind == "franchise"]
    assert (book_node_id("b1"), fid, "manual") in franchise_edges


def test_no_franchise_edge_without_a_franchise_ancestor(tmp_path):
    g, folder, files = _graph_single_book(tmp_path)
    u = _unit(folder, files, unit_id="b1")
    _nodes, edges = graph_records(g, [u], root=tmp_path)
    assert not [e for e in edges if e.kind == "franchise"]


def test_two_editions_share_one_series_entity(tmp_path):
    # the dedup-doesn't-merge-books guard: distinct book nodes, one shared series entity
    from colophon.core.models import SeriesRef

    g, folder, files = _graph_single_book(tmp_path)
    u1 = _unit(folder, files, unit_id="edition-A")
    u2 = _unit(folder, files, unit_id="edition-B")  # same title/folder, different #166 id
    for u in (u1, u2):
        u.series = [SeriesRef(name="Mistborn", sequence=1.0)]
    nodes, edges = graph_records(g, [u1, u2], root=tmp_path)
    eid = entity_node_id("series", "Mistborn", tmp_path)
    assert len([n for n in nodes if n.id == eid]) == 1  # one series entity
    book_nodes = {n.id for n in nodes if n.semantic == "book"}
    assert book_nodes == {book_node_id("edition-A"), book_node_id("edition-B")}  # both editions survive
    series_srcs = {e.src for e in edges if e.kind == "series" and e.dst == eid}
    assert series_srcs == {book_node_id("edition-A"), book_node_id("edition-B")}
