from pathlib import Path

from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.models import BookUnit, DetectedWork, SeriesRef


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


def test_shape_prior_boosts_conforming_grouping():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    g = _g()
    # dominant shape: root / author / title  (titles at depth 2)
    for author in ("A One", "A Two", "A Three"):
        a = root / author
        _link(g, root, a)
        _link(g, a, a / "Book")
        _book(g, a / "Book", "Book")

    classify_graph(g, root=root)

    a_one = g.directories[DirectoryNode.id_for(root / "A One")]
    assert a_one.kind == "grouping"
    assert "matches dominant Author/Title shape" in a_one.kind_evidence
    assert a_one.kind_confidence == 1.0  # 1.0 book-like, capped after +0.1 boost


def test_shape_prior_flags_off_pattern_grouping():
    from colophon.core.graph_classify import classify_graph

    root = Path("/lib")
    g = _g()
    # dominant: depth-2 titles for two authors
    for author in ("A One", "A Two"):
        a = root / author
        _link(g, root, a)
        _link(g, a, a / "Book")
        _book(g, a / "Book", "Book")
    # off-pattern: an author who organizes by series (titles at depth 3)
    deep = root / "Deep Author"
    series = deep / "Series"
    _link(g, root, deep)
    _link(g, deep, series)
    _link(g, series, series / "Vol1")
    _book(g, series / "Vol1", "Vol1")

    classify_graph(g, root=root)

    series_node = g.directories[DirectoryNode.id_for(series)]
    assert series_node.kind == "grouping"
    assert any("off-pattern" in e for e in series_node.kind_evidence)


def _series_book(g, folder, title, series=None, seq=None, via="series"):
    d = _dir(g, folder)
    b = BookUnit.new(source_folder=folder)
    b.title = title
    if series and via == "series":
        b.series = [SeriesRef(name=series, sequence=seq)]
    elif series and via == "detected":
        b.detected_works = [DetectedWork(label=title, series=series, sequence=seq, files=[])]
    bn = BookNode(id=f"{folder}|{title}", book=b, dir_id=d.id)
    g.books[bn.id] = bn
    d.books.append(bn.id)


def test_hint_series_for_one_series_with_ramp():
    from colophon.core.graph_classify import classify_graph, hint_grouping_kinds

    root = Path("/lib")
    series = root / "Mistborn"
    g = _g()
    for i, t in enumerate(("Final Empire", "Well of Ascension", "Hero of Ages"), start=1):
        _link(g, series, series / t)
        _series_book(g, series / t, t, series="Mistborn", seq=float(i))
    _link(g, root, series)

    classify_graph(g, root=root)
    hint_grouping_kinds(g)

    node = g.directories[DirectoryNode.id_for(series)]
    assert node.kind == "grouping"
    assert node.kind_hint == "series"
    assert any("Mistborn" in e for e in node.kind_hint_evidence)


def test_hint_author_for_multiple_series():
    from colophon.core.graph_classify import classify_graph, hint_grouping_kinds

    root = Path("/lib")
    author = root / "Brandon Sanderson"
    g = _g()
    for t, s, q in [("Final Empire", "Mistborn", 1.0), ("Well of Ascension", "Mistborn", 2.0),
                    ("Way of Kings", "Stormlight", 1.0)]:
        _link(g, author, author / t)
        _series_book(g, author / t, t, series=s, seq=q)
    _link(g, root, author)

    classify_graph(g, root=root)
    hint_grouping_kinds(g)

    node = g.directories[DirectoryNode.id_for(author)]
    assert node.kind == "grouping"
    assert node.kind_hint == "author"


def test_hint_author_for_standalone_titles():
    from colophon.core.graph_classify import classify_graph, hint_grouping_kinds

    root = Path("/lib")
    author = root / "Doris Kearns Goodwin"
    g = _g()
    for t in ("Team of Rivals", "Leadership", "The Bully Pulpit"):
        _link(g, author, author / t)
        _series_book(g, author / t, t)
    _link(g, root, author)

    classify_graph(g, root=root)
    hint_grouping_kinds(g)

    node = g.directories[DirectoryNode.id_for(author)]
    assert node.kind == "grouping"
    assert node.kind_hint == "author"
    assert node.kind_hint_confidence == 0.6


def test_hint_ambiguous_one_series_no_ramp():
    from colophon.core.graph_classify import classify_graph, hint_grouping_kinds

    root = Path("/lib")
    grp = root / "Some Collection"
    g = _g()
    for t in ("A", "B"):
        _link(g, grp, grp / t)
        _series_book(g, grp / t, t, series="Some Collection", seq=None)
    _link(g, root, grp)

    classify_graph(g, root=root)
    hint_grouping_kinds(g)

    node = g.directories[DirectoryNode.id_for(grp)]
    assert node.kind == "grouping"
    assert node.kind_hint == "ambiguous"


def test_hint_uses_detected_works_fallback():
    from colophon.core.graph_classify import classify_graph, hint_grouping_kinds

    root = Path("/lib")
    series = root / "Mistborn"
    g = _g()
    for i, t in enumerate(("Final Empire", "Well of Ascension"), start=1):
        _link(g, series, series / t)
        _series_book(g, series / t, t, series="Mistborn", seq=float(i), via="detected")
    _link(g, root, series)

    classify_graph(g, root=root)
    hint_grouping_kinds(g)

    assert g.directories[DirectoryNode.id_for(series)].kind_hint == "series"


def test_hint_only_on_grouping_nodes():
    from colophon.core.graph_classify import classify_graph, hint_grouping_kinds

    root = Path("/lib")
    g = _g()
    _link(g, root, root / "Dune")
    _series_book(g, root / "Dune", "Dune")

    classify_graph(g, root=root)
    hint_grouping_kinds(g)

    assert g.directories[DirectoryNode.id_for(root / "Dune")].kind == "title"
    assert g.directories[DirectoryNode.id_for(root / "Dune")].kind_hint == ""


def test_apply_overrides_wins_and_marks_manual():
    from colophon.core.graph_classify import apply_overrides, classify_graph
    from colophon.core.models import NodeOverride

    root = Path("/lib")
    franchise = root / "Doctor Who"
    g = _g()
    _link(g, root, franchise)
    _book(g, franchise, "Power of the Daleks", n=3)  # auto would be container

    classify_graph(g, root=root)
    apply_overrides(g, {str(franchise): NodeOverride(kind="franchise", value="DOCTOR WHO")})

    node = g.directories[DirectoryNode.id_for(franchise)]
    assert node.kind == "franchise"
    assert node.kind_value == "DOCTOR WHO"
    assert node.kind_source == "manual"


def test_apply_overrides_author_sets_author_attr():
    from colophon.core.graph_classify import apply_overrides
    from colophon.core.models import NodeOverride

    g = _g()
    d = _dir(g, Path("/lib/Some Folder"))
    apply_overrides(g, {"/lib/Some Folder": NodeOverride(kind="author", value="Jane Doe")})

    assert d.kind == "author" and d.author == "Jane Doe" and d.kind_source == "manual"


def test_apply_overrides_ignores_unmatched_path():
    from colophon.core.graph_classify import apply_overrides
    from colophon.core.models import NodeOverride

    g = _g()
    d = _dir(g, Path("/lib/A"))
    d.kind = "grouping"
    apply_overrides(g, {"/lib/Other": NodeOverride(kind="franchise", value="X")})

    assert d.kind == "grouping" and d.kind_source == ""
