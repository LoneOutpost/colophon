from pathlib import Path

from colophon.core.graph import BookNode, DirectoryNode, FileNode, FileRole, Graph
from colophon.core.graph_view import _dir_badges, folder_rows, graph_summary
from colophon.core.models import (
    BookUnit,
    ContentKind,
    Finding,
    FindingCode,
    FindingSeverity,
    Provenance,
)


def _walk(rows):
    for r in rows:
        yield r
        yield from _walk(r.children)


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
        kind_value="Brandon Sanderson", kind_confidence=0.75,
        child_dirs=[DirectoryNode.id_for(multi)])
    g.directories[DirectoryNode.id_for(multi)] = DirectoryNode(
        path=multi, child_files=[legion.id, elantris.id, meta.id],
        books=[legion_b.id, elantris_b.id])
    return g, root


def test_folder_rows_are_directory_only_with_counts():
    g, root = _build_graph()
    rows = folder_rows(g, root)

    assert all(r.node_kind == "dir" for r in _walk(rows))   # no book/file nodes anywhere
    assert len(rows) == 1
    author = rows[0]
    assert author.label == "Brandon Sanderson"
    assert author.badges == ["AUTHOR → Brandon Sanderson · 0.75"]
    assert author.book_count == 2          # rolls up the Collection subtree's two books
    assert author.attention_count == 0     # no findings on these books

    collection = author.children[0]
    assert collection.label == "Collection"
    assert collection.children == []       # books/files are not rendered as folder children
    assert collection.book_count == 2
    assert collection.multi_book is True   # a folder holding >1 book


def test_folder_rows_attention_rolls_up_active_findings():
    g, root = _build_graph()
    g.books["legion"].book.findings = [
        Finding(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN, detail="x")
    ]
    rows = folder_rows(g, root)
    assert rows[0].attention_count == 1               # lifted to the ancestor author folder
    assert rows[0].children[0].attention_count == 1


def test_folder_rows_acknowledged_or_suppressed_finding_is_not_counted():
    g, root = _build_graph()
    b = g.books["legion"].book
    b.findings = [Finding(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                          detail="x")]
    b.acknowledged_findings = [FindingCode.METADATA_CONFLICT]
    assert folder_rows(g, root)[0].attention_count == 0


def test_folder_rows_empty_when_root_absent():
    g, _ = _build_graph()
    assert folder_rows(g, Path("/nowhere")) == []


def test_folder_rows_sorts_dirs_case_insensitively_and_flags_needs_review():
    root = Path("/lib")
    g = Graph()
    for name in ("Zoo", "apple"):
        d = DirectoryNode(path=root / name)
        d.kind = "unknown"                 # unknown -> needs_review
        g.directories[DirectoryNode.id_for(root / name)] = d
    g.directories[DirectoryNode.id_for(root)] = DirectoryNode(
        path=root,
        child_dirs=[DirectoryNode.id_for(root / "Zoo"), DirectoryNode.id_for(root / "apple")],
    )
    rows = folder_rows(g, root)
    assert [r.label for r in rows] == ["apple", "Zoo"]
    assert all(r.needs_review for r in rows)


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


def test_graph_summary_counts_resolved_kinds():
    g = Graph()
    for name, kind in [("a", "author"), ("b", "series"), ("c", "container"),
                       ("d", "title"), ("e", "unknown")]:
        n = DirectoryNode(path=Path("/lib") / name)
        n.kind = kind
        g.directories[n.id] = n

    s = graph_summary(g)
    assert s.author_dirs == 1
    assert s.series_dirs == 1
    assert s.container_dirs == 1
    assert s.title_dirs == 1
    assert s.unknown_dirs == 1


def test_graph_summary_counts_auto_unconfirmed():
    # auto (source == "") author/series nodes are the confirm-cohort review queue
    g = Graph()
    for name, kind, src in [("a", "author", ""), ("b", "author", "manual"),
                            ("c", "series", ""), ("d", "series", "")]:
        n = DirectoryNode(path=Path("/lib") / name)
        n.kind = kind
        n.kind_source = src
        g.directories[n.id] = n

    s = graph_summary(g)
    assert s.auto_author == 1     # only the source == "" author
    assert s.auto_series == 2


def test_dir_badges_manual_override():
    from colophon.core.graph_view import _dir_badges

    node = DirectoryNode(path=Path("/lib/Doctor Who"))
    node.kind = "franchise"
    node.kind_value = "DOCTOR WHO"
    node.kind_source = "manual"
    assert _dir_badges(node) == ["FRANCHISE → DOCTOR WHO · manual"]

    # a manual node shows its source, not a confidence chip, even if confidence is set
    node.kind_confidence = 0.9
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


def test_grouping_cohort_selects_auto_of_kind_excluding_root():
    from colophon.core.graph_view import grouping_cohort

    root = Path("/lib")
    g = Graph()

    def _n(path, kind, source=""):
        n = DirectoryNode(path=path)
        n.kind = kind
        n.kind_source = source
        g.directories[n.id] = n
        return n

    _n(root, "author")                       # the root itself -> excluded
    _n(root / "A1", "author")
    _n(root / "A2", "author")
    _n(root / "A3", "author", "manual")      # already confirmed -> not in the cohort
    _n(root / "S1", "series")                # different kind
    _n(root / "C", "container")              # not author

    cohort = grouping_cohort(g, root=root, hint="author")
    assert {n.path for n in cohort} == {root / "A1", root / "A2"}
