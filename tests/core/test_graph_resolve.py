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
