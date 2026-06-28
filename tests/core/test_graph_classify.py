from pathlib import Path

from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.models import BookUnit


def _g() -> Graph:
    return Graph()


def _dir(g: Graph, path: Path) -> DirectoryNode:
    node = g.directories.get(DirectoryNode.id_for(path))
    if node is None:
        node = DirectoryNode(path=path)
        g.directories[node.id] = node
    return node


def _link(g: Graph, parent: Path, child: Path) -> None:
    p, c = _dir(g, parent), _dir(g, child)
    if c.id not in p.child_dirs:
        p.child_dirs.append(c.id)


def _book(g: Graph, folder: Path, title: str, n: int = 1) -> None:
    d = _dir(g, folder)
    for i in range(n):
        b = BookUnit.new(source_folder=folder)
        b.title = f"{title}{i if i else ''}"
        bn = BookNode(id=f"{folder}|{title}|{i}", book=b, dir_id=d.id)
        g.books[bn.id] = bn
        d.books.append(bn.id)


def _loose_audio(g: Graph, folder: Path, name: str) -> None:
    d = _dir(g, folder)
    fn = FileNode(path=folder / name, role=FileRole.AUDIO)
    g.files[fn.id] = fn
    d.child_files.append(fn.id)


def test_single_book_leaf_is_title():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    g = _g()
    _link(g, root, root / "Dune")
    _book(g, root / "Dune", "Dune")

    classify_graph(g, root=root)

    dune = g.directories[DirectoryNode.id_for(root / "Dune")]
    assert dune.kind == "title" and dune.kind_confidence == 1.0


def test_author_with_title_subfolders_is_grouping():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    author = root / "Frank Herbert"
    g = _g()
    for t in ("Dune", "Dune Messiah", "Children of Dune"):
        _link(g, author, author / t)
        _book(g, author / t, t)
    _link(g, root, author)

    classify_graph(g, root=root)

    node = g.directories[DirectoryNode.id_for(author)]
    assert node.kind == "grouping"
    assert "3 of 3 child folders are book-like" in node.kind_evidence


def test_author_with_series_subfolders_is_grouping_recursive():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    author = root / "Brandon Sanderson"
    series = author / "Mistborn"
    g = _g()
    for t in ("The Final Empire", "The Well of Ascension"):
        _link(g, series, series / t)
        _book(g, series / t, t)
    _link(g, author, series)
    _link(g, root, author)

    classify_graph(g, root=root)

    assert g.directories[DirectoryNode.id_for(series)].kind == "grouping"
    assert g.directories[DirectoryNode.id_for(author)].kind == "grouping"


def test_multiple_loose_books_in_one_folder_is_container():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    dump = root / "uploaderdump"
    g = _g()
    _link(g, root, dump)
    _book(g, dump, "Book", n=4)

    classify_graph(g, root=root)

    node = g.directories[DirectoryNode.id_for(dump)]
    assert node.kind == "container"
    assert any("loose books in one folder" in e for e in node.kind_evidence)


def test_loose_audio_alongside_subfolders_is_container():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    mixed = root / "mixed"
    g = _g()
    _link(g, root, mixed)
    _link(g, mixed, mixed / "Sub")
    _book(g, mixed / "Sub", "X")
    _loose_audio(g, mixed, "stray.mp3")

    classify_graph(g, root=root)

    assert g.directories[DirectoryNode.id_for(mixed)].kind == "container"


def test_minority_book_like_children_is_unknown():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    parent = root / "parent"
    g = _g()
    _link(g, parent, parent / "title")
    _book(g, parent / "title", "T")
    for junk in ("j1", "j2"):
        _link(g, parent, parent / junk)
        _book(g, parent / junk, "B", n=3)
    _link(g, root, parent)

    classify_graph(g, root=root)

    assert g.directories[DirectoryNode.id_for(parent)].kind == "unknown"
