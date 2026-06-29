from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit, Provenance


def _name_key_subject(s):
    from colophon.core.graph_resolve import _name_key
    return _name_key(s)


def test_name_key_handles_order_case_spacing_and_punctuation():
    k = _name_key_subject
    assert k("Stephen King") == k("stephen king") == k("King, Stephen")
    # punctuation in initials must not block the match (the confirmed real cause)
    assert k("Robert A. Heinlein") == k("Robert A Heinlein") == k("Robert A.Heinlein")
    assert k("J.R.R. Tolkien") == k("J R R Tolkien")
    assert k("Stephen King") != k("Brandon Sanderson")


def test_period_in_tag_author_still_classifies_author(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    author_dir = root / "Robert A Heinlein"          # folder: no period
    a = author_dir / "Stranger in a Strange Land"
    b = author_dir / "Starship Troopers"
    graph = _graph_with_dirs(a, b)

    tagged = _book(a, ["Robert A. Heinlein"], Provenance.TAG.value)  # tag: with period
    untagged = _book(b, [])
    resolve_graph_authors(graph, [tagged, untagged], root=root)

    node = graph.directories[DirectoryNode.id_for(author_dir)]
    assert node.kind == "author" and node.author == "Robert A. Heinlein"
    assert untagged.authors == ["Robert A. Heinlein"]
    assert untagged.provenance["authors"] == Provenance.GRAPHING.value


def _graph_with_dirs(*folders: Path) -> Graph:
    g = Graph()
    seen: set[Path] = set()
    for f in folders:
        for p in [f, *f.parents]:
            if p not in seen:
                seen.add(p)
                g.directories[DirectoryNode.id_for(p)] = DirectoryNode(path=p)
    return g


def _book(folder: Path, authors, prov=None) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    if authors:
        b.authors = authors
        b.provenance["authors"] = prov
    return b


def test_up_classifies_and_down_fills(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    author_dir = root / "up" / "Stephen King"
    coll = author_dir / "-collection-"
    a = coll / "The Gunslinger"
    b = coll / "Wizard and Glass"
    graph = _graph_with_dirs(a, b)

    tagged = _book(a, ["Stephen King"], Provenance.TAG.value)
    untagged = _book(b, [])
    resolve_graph_authors(graph, [tagged, untagged], root=root)

    assert graph.directories[DirectoryNode.id_for(author_dir)].kind == "author"
    assert graph.directories[DirectoryNode.id_for(author_dir)].author == "Stephen King"
    assert untagged.authors == ["Stephen King"]
    assert untagged.provenance["authors"] == Provenance.GRAPHING.value
    assert tagged.provenance["authors"] == Provenance.TAG.value  # strong author untouched


def test_graphing_replaces_weak_author_but_not_manual(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    author_dir = root / "Stephen King"
    weak = author_dir / "BookFolder"
    manual = author_dir / "Manual"
    graph = _graph_with_dirs(weak, manual)

    tag_sib = _book(author_dir / "Tagged", ["Stephen King"], Provenance.TAG.value)
    graph.directories[DirectoryNode.id_for(author_dir / "Tagged")] = DirectoryNode(path=author_dir / "Tagged")
    weak_book = _book(weak, ["BookFolder"], Provenance.DIRECTORY.value)
    manual_book = _book(manual, ["Someone Else"], Provenance.MANUAL.value)

    resolve_graph_authors(graph, [tag_sib, weak_book, manual_book], root=root)

    assert weak_book.authors == ["Stephen King"]              # weak DIRECTORY replaced
    assert weak_book.provenance["authors"] == Provenance.GRAPHING.value
    assert manual_book.authors == ["Someone Else"]            # MANUAL untouched
    assert manual_book.provenance["authors"] == Provenance.MANUAL.value


def test_no_classification_without_matching_dir(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    a = root / "Dune"            # folder name does not match the author
    graph = _graph_with_dirs(a)
    book = _book(a, ["Frank Herbert"], Provenance.TAG.value)
    sibling = _book(root / "Other", [])
    graph.directories[DirectoryNode.id_for(root / "Other")] = DirectoryNode(path=root / "Other")

    resolve_graph_authors(graph, [book, sibling], root=root)

    assert graph.directories[DirectoryNode.id_for(a)].kind == "unknown"
    assert sibling.authors == []   # nothing classified -> nothing inherited


def test_match_source_author_classifies_and_fills(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    author_dir = root / "Robert A Heinlein"
    a = author_dir / "Stranger in a Strange Land"
    b = author_dir / "Starship Troopers"
    graph = _graph_with_dirs(a, b)

    # author resolved from a match source (audnexus), not a tag
    tagged = _book(a, ["Robert A. Heinlein"], "audnexus")
    untagged = _book(b, [])
    resolve_graph_authors(graph, [tagged, untagged], root=root)

    node = graph.directories[DirectoryNode.id_for(author_dir)]
    assert node.kind == "author" and node.author == "Robert A. Heinlein"
    assert untagged.authors == ["Robert A. Heinlein"]
    assert untagged.provenance["authors"] == Provenance.GRAPHING.value


def test_manual_author_classifies(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    book = _book(author_dir / "Elantris", ["Brandon Sanderson"], Provenance.MANUAL.value)
    graph = _graph_with_dirs(author_dir / "Elantris")

    resolve_graph_authors(graph, [book], root=root)
    assert graph.directories[DirectoryNode.id_for(author_dir)].kind == "author"


def test_directory_provenance_author_does_not_classify(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    author_dir = root / "Some Folder"
    # author was derived from the folder name itself (directory inference) — circular,
    # so it must NOT classify the folder AUTHOR on its own.
    book = _book(author_dir / "Book", ["Some Folder"], Provenance.DIRECTORY.value)
    graph = _graph_with_dirs(author_dir / "Book")

    resolve_graph_authors(graph, [book], root=root)
    assert graph.directories[DirectoryNode.id_for(author_dir)].kind == "unknown"


def test_container_node_is_not_upgraded_to_author(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    folder = root / "Stephen King"
    book = _book(folder / "Book", ["Stephen King"], Provenance.TAG.value)
    graph = _graph_with_dirs(folder / "Book")
    graph.directories[DirectoryNode.id_for(folder)].kind = "container"

    resolve_graph_authors(graph, [book], root=root)

    assert graph.directories[DirectoryNode.id_for(folder)].kind == "container"


def test_title_node_is_not_upgraded_to_author(tmp_path):
    from colophon.core.graph_resolve import resolve_graph_authors

    root = tmp_path / "lib"
    folder = root / "Stephen King"
    book = _book(folder / "Book", ["Stephen King"], Provenance.TAG.value)
    graph = _graph_with_dirs(folder / "Book")
    graph.directories[DirectoryNode.id_for(folder)].kind = "title"

    resolve_graph_authors(graph, [book], root=root)

    assert graph.directories[DirectoryNode.id_for(folder)].kind == "title"


def test_propagate_manual_author_fills_empty_not_tag(tmp_path):
    from colophon.core.graph_resolve import propagate_overrides

    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    a, b2 = author_dir / "Elantris", author_dir / "Tagged"
    graph = _graph_with_dirs(a, b2)
    node = graph.directories[DirectoryNode.id_for(author_dir)]
    node.kind, node.kind_source, node.kind_value = "author", "manual", "Brandon Sanderson"

    empty = _book(a, [])
    tagged = _book(b2, ["Someone Else"], Provenance.TAG.value)
    propagate_overrides(graph, [empty, tagged], root=root)

    assert empty.authors == ["Brandon Sanderson"]
    assert empty.provenance["authors"] == Provenance.MANUAL.value
    assert tagged.authors == ["Someone Else"]
    assert tagged.provenance["authors"] == Provenance.TAG.value


def test_propagate_manual_author_fills_weak(tmp_path):
    from colophon.core.graph_resolve import propagate_overrides

    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    a = author_dir / "Elantris"
    graph = _graph_with_dirs(a)
    node = graph.directories[DirectoryNode.id_for(author_dir)]
    node.kind, node.kind_source, node.kind_value = "author", "manual", "Brandon Sanderson"

    weak = _book(a, ["Elantris"], Provenance.DIRECTORY.value)
    propagate_overrides(graph, [weak], root=root)

    assert weak.authors == ["Brandon Sanderson"]
    assert weak.provenance["authors"] == Provenance.MANUAL.value


def test_propagate_manual_series_fills_empty(tmp_path):
    from colophon.core.graph_resolve import propagate_overrides

    root = tmp_path / "lib"
    series_dir = root / "Mistborn"
    a = series_dir / "Final Empire"
    graph = _graph_with_dirs(a)
    node = graph.directories[DirectoryNode.id_for(series_dir)]
    node.kind, node.kind_source, node.kind_value = "series", "manual", "Mistborn"

    book = _book(a, [])
    propagate_overrides(graph, [book], root=root)

    assert [s.name for s in book.series] == ["Mistborn"]
    assert book.provenance["series"] == Provenance.MANUAL.value


def test_propagate_nested_series_under_author_applies_both(tmp_path):
    from colophon.core.graph_resolve import propagate_overrides

    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    series_dir = author_dir / "Mistborn"
    a = series_dir / "Final Empire"
    graph = _graph_with_dirs(a)
    an = graph.directories[DirectoryNode.id_for(author_dir)]
    an.kind, an.kind_source, an.kind_value = "author", "manual", "Brandon Sanderson"
    sn = graph.directories[DirectoryNode.id_for(series_dir)]
    sn.kind, sn.kind_source, sn.kind_value = "series", "manual", "Mistborn"

    book = _book(a, [])
    propagate_overrides(graph, [book], root=root)

    assert book.authors == ["Brandon Sanderson"]
    assert [s.name for s in book.series] == ["Mistborn"]


def test_propagate_franchise_does_not_touch_books(tmp_path):
    from colophon.core.graph_resolve import propagate_overrides

    root = tmp_path / "lib"
    fdir = root / "Doctor Who"
    a = fdir / "Book"
    graph = _graph_with_dirs(a)
    node = graph.directories[DirectoryNode.id_for(fdir)]
    node.kind, node.kind_source, node.kind_value = "franchise", "manual", "DOCTOR WHO"

    book = _book(a, [])
    propagate_overrides(graph, [book], root=root)

    assert book.authors == [] and "authors" not in book.provenance


def test_ancestor_paths_nearest_first_inclusive(tmp_path):
    from colophon.core.graph_resolve import _ancestor_paths

    root = tmp_path / "lib"
    leaf = root / "Author" / "Book"
    assert list(_ancestor_paths(leaf, root)) == [leaf, root / "Author", root]


def test_ancestor_paths_stops_outside_root(tmp_path):
    from colophon.core.graph_resolve import _ancestor_paths

    root = tmp_path / "lib"
    outside = tmp_path / "elsewhere" / "Book"
    assert list(_ancestor_paths(outside, root)) == [outside]


def test_ancestor_paths_folder_is_root(tmp_path):
    from colophon.core.graph_resolve import _ancestor_paths

    root = tmp_path / "lib"
    assert list(_ancestor_paths(root, root)) == [root]
