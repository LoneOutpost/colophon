from pathlib import Path

from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.graph_view import graph_summary, graph_tree
from colophon.core.models import BookUnit, ContentKind, Provenance


def _build_graph() -> tuple[Graph, Path]:
    root = Path("/lib")
    author = root / "Brandon Sanderson"
    multi = author / "Collection"
    g = Graph()

    # files
    legion = FileNode(path=multi / "Legion.mp3", role=FileRole.AUDIO)
    elantris = FileNode(path=multi / "Elantris.mp3", role=FileRole.AUDIO)
    meta = FileNode(path=multi / "metadata.json", role=FileRole.DATAFILE)
    for fn in (legion, elantris, meta):
        g.files[fn.id] = fn

    # books (two leaves in one MULTI folder)
    def _book(folder, title, prov):
        b = BookUnit.new(source_folder=folder)
        b.title = title
        b.content_kind = ContentKind.SINGLE
        b.authors = ["Brandon Sanderson"]
        b.provenance["authors"] = prov
        return b

    legion_b = BookNode(id="legion", book=_book(multi, "Legion", Provenance.GRAPHING.value),
                        owns=[legion.id], dir_id=DirectoryNode.id_for(multi))
    elantris_b = BookNode(id="elantris", book=_book(multi, "Elantris", Provenance.TAG.value),
                          owns=[elantris.id], dir_id=DirectoryNode.id_for(multi))
    g.books[legion_b.id] = legion_b
    g.books[elantris_b.id] = elantris_b

    # directories
    g.directories[DirectoryNode.id_for(root)] = DirectoryNode(
        path=root, child_dirs=[DirectoryNode.id_for(author)])
    g.directories[DirectoryNode.id_for(author)] = DirectoryNode(
        path=author, kind="author", author="Brandon Sanderson",
        child_dirs=[DirectoryNode.id_for(multi)])
    g.directories[DirectoryNode.id_for(multi)] = DirectoryNode(
        path=multi, child_files=[legion.id, elantris.id, meta.id],
        books=[legion_b.id, elantris_b.id])
    return g, root


def test_graph_tree_nests_dirs_books_and_loose_files():
    g, root = _build_graph()
    top = graph_tree(g, root)

    assert len(top) == 1
    author = top[0]
    assert author.node_kind == "dir"
    assert author.label == "Brandon Sanderson"
    assert author.badges == ["AUTHOR → Brandon Sanderson"]

    multi = author.children[0]
    assert multi.node_kind == "dir" and multi.label == "Collection"
    # children sorted: books by title (Elantris, Legion), then loose files (metadata.json)
    kinds = [(c.node_kind, c.label) for c in multi.children]
    assert kinds == [("book", "Elantris"), ("book", "Legion"), ("file", "metadata.json")]

    elantris = multi.children[0]
    assert elantris.badges == ["single", "author: tag"]
    assert [(c.node_kind, c.label, c.badges) for c in elantris.children] == [
        ("file", "Elantris.mp3", ["audio"])]
    legion = multi.children[1]
    assert legion.badges == ["single", "author: graphing"]
    assert multi.children[2].badges == ["datafile"]   # loose datafile, not owned by a book


def test_graph_tree_empty_when_root_absent():
    g, _ = _build_graph()
    assert graph_tree(g, Path("/nowhere")) == []


def test_graph_summary_counts():
    g, _ = _build_graph()
    s = graph_summary(g)
    assert s.directories == 3
    assert s.author_dirs == 1
    assert s.books == 2
    assert s.multi_book_dirs == 1            # the Collection dir holds 2 books
    assert s.files_by_role == {"audio": 2, "datafile": 1}
