from pathlib import Path

from colophon.core.graph import DirectoryNode, FileNode, FileRole, Graph
from colophon.core.graph_records import book_node_id, graph_records
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
