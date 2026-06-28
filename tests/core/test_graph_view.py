from pathlib import Path

from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.graph_view import _dir_badges, graph_summary, graph_tree
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


def test_graph_tree_sorts_dirs_case_insensitively():
    root = Path("/lib")
    g = Graph()
    # Sibling dirs whose case-sensitive order (Zoo before apple, ASCII) differs from the
    # natural one. graph_tree must order them case-insensitively: apple before Zoo.
    for name in ("Zoo", "apple"):
        g.directories[DirectoryNode.id_for(root / name)] = DirectoryNode(path=root / name)
    g.directories[DirectoryNode.id_for(root)] = DirectoryNode(
        path=root,
        child_dirs=[DirectoryNode.id_for(root / "Zoo"), DirectoryNode.id_for(root / "apple")],
    )
    assert [n.label for n in graph_tree(g, root)] == ["apple", "Zoo"]


def test_graph_summary_counts():
    g, _ = _build_graph()
    s = graph_summary(g)
    assert s.directories == 3
    assert s.author_dirs == 1
    assert s.books == 2
    assert s.multi_book_dirs == 1            # the Collection dir holds 2 books
    assert s.files_by_role == {"audio": 2, "datafile": 1}


def test_dir_badges_show_coarse_kind_and_confidence():
    node = DirectoryNode(path=Path("/lib/A"))
    node.kind = "grouping"
    node.kind_confidence = 0.86
    assert _dir_badges(node) == ["GROUPING · 0.86"]

    container = DirectoryNode(path=Path("/lib/junk"))
    container.kind = "container"
    container.kind_confidence = 0.9
    assert _dir_badges(container) == ["CONTAINER · 0.90"]


def test_graph_summary_counts_coarse_kinds():
    g = Graph()
    for name, kind in [("a", "grouping"), ("b", "grouping"), ("c", "container"),
                       ("d", "title"), ("e", "unknown")]:
        n = DirectoryNode(path=Path("/lib") / name)
        n.kind = kind
        g.directories[n.id] = n

    s = graph_summary(g)
    assert s.grouping_dirs == 2
    assert s.container_dirs == 1
    assert s.title_dirs == 1
    assert s.unknown_dirs == 1


def test_dir_badges_show_grouping_hint_chip():
    from colophon.core.graph_view import _dir_badges

    node = DirectoryNode(path=Path("/lib/Mistborn"))
    node.kind = "grouping"
    node.kind_confidence = 0.86
    node.kind_hint = "series"
    node.kind_hint_confidence = 0.74
    assert _dir_badges(node) == ["GROUPING · 0.86", "series? · 0.74"]

    no_hint = DirectoryNode(path=Path("/lib/A"))
    no_hint.kind = "grouping"
    no_hint.kind_confidence = 0.9
    assert _dir_badges(no_hint) == ["GROUPING · 0.90"]


def test_graph_summary_splits_grouping_hints():
    from colophon.core.graph_view import graph_summary

    g = Graph()
    for name, hint in [("a", "author"), ("b", "series"), ("c", "series"), ("d", "ambiguous")]:
        n = DirectoryNode(path=Path("/lib") / name)
        n.kind = "grouping"
        n.kind_hint = hint
        g.directories[n.id] = n

    s = graph_summary(g)
    assert s.grouping_author_hint == 1
    assert s.grouping_series_hint == 2
    assert s.grouping_ambiguous_hint == 1


def test_dir_badges_manual_override():
    from colophon.core.graph_view import _dir_badges

    node = DirectoryNode(path=Path("/lib/Doctor Who"))
    node.kind = "franchise"
    node.kind_value = "DOCTOR WHO"
    node.kind_source = "manual"
    assert _dir_badges(node) == ["FRANCHISE → DOCTOR WHO · manual"]

    # a manual node shows no auto/hint chip even if those fields are set
    node.kind_confidence = 0.9
    node.kind_hint = "series"
    assert _dir_badges(node) == ["FRANCHISE → DOCTOR WHO · manual"]


def test_graph_summary_counts_manual_dirs():
    from colophon.core.graph_view import graph_summary

    g = Graph()
    for name, src in [("a", "manual"), ("b", "manual"), ("c", "")]:
        n = DirectoryNode(path=Path("/lib") / name)
        n.kind = "grouping"
        n.kind_source = src
        g.directories[n.id] = n
    assert graph_summary(g).manual_dirs == 2
