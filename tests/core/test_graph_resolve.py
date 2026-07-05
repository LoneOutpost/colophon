from pathlib import Path

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit, Provenance, SeriesRef


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
    root = tmp_path / "lib"
    author_dir = root / "Robert A Heinlein"          # folder: no period
    a = author_dir / "Stranger in a Strange Land"
    b = author_dir / "Starship Troopers"
    graph = _graph_with_dirs(a, b)

    tagged = _book(a, ["Robert A. Heinlein"], Provenance.TAG.value)  # tag: with period
    untagged = _book(b, [])
    _resolve(graph, [tagged, untagged], root)

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


def _resolve(graph: Graph, books, root: Path, overrides=None) -> None:
    """Wire the books into the graph and run the production classification pipeline
    (classify_graph -> classify_nodes), the pair that replaced resolve_graph_authors."""
    from colophon.core.graph import BookNode
    from colophon.core.graph_classify import classify_graph
    from colophon.core.node_classify import classify_nodes

    for d in list(graph.directories.values()):
        parent = graph.directories.get(DirectoryNode.id_for(d.path.parent))
        if parent is not None and parent is not d and d.id not in parent.child_dirs:
            parent.child_dirs.append(d.id)
    for i, b in enumerate(books):
        did = DirectoryNode.id_for(b.source_folder)
        if did not in graph.directories:
            graph.directories[did] = DirectoryNode(path=b.source_folder)
        bid = f"bk{i}"
        graph.books[bid] = BookNode(id=bid, book=b, owns=[], dir_id=did)
        graph.directories[did].books.append(bid)
    classify_graph(graph, root=root)
    classify_nodes(graph, books, root=root, overrides=overrides or {})


def test_up_classifies_and_down_fills(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "up" / "Stephen King"
    coll = author_dir / "-collection-"
    a = coll / "The Gunslinger"
    b = coll / "Wizard and Glass"
    graph = _graph_with_dirs(a, b)

    tagged = _book(a, ["Stephen King"], Provenance.TAG.value)
    untagged = _book(b, [])
    _resolve(graph, [tagged, untagged], root)

    assert graph.directories[DirectoryNode.id_for(author_dir)].kind == "author"
    assert graph.directories[DirectoryNode.id_for(author_dir)].author == "Stephen King"
    # the intermediate -collection- grouping must not shadow the real author on the down-fill
    assert untagged.authors == ["Stephen King"]
    assert untagged.provenance["authors"] == Provenance.GRAPHING.value
    assert tagged.provenance["authors"] == Provenance.TAG.value  # strong author untouched


def test_graphing_replaces_weak_author_but_not_manual(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Stephen King"
    weak = author_dir / "BookFolder"
    manual = author_dir / "Manual"
    graph = _graph_with_dirs(weak, manual, author_dir / "Tagged")

    tag_sib = _book(author_dir / "Tagged", ["Stephen King"], Provenance.TAG.value)
    weak_book = _book(weak, ["BookFolder"], Provenance.DIRECTORY.value)
    manual_book = _book(manual, ["Someone Else"], Provenance.MANUAL.value)

    _resolve(graph, [tag_sib, weak_book, manual_book], root)

    assert weak_book.authors == ["Stephen King"]              # weak DIRECTORY replaced
    assert weak_book.provenance["authors"] == Provenance.GRAPHING.value
    assert manual_book.authors == ["Someone Else"]            # MANUAL untouched
    assert manual_book.provenance["authors"] == Provenance.MANUAL.value


def test_book_folder_named_unlike_author_is_not_promoted(tmp_path):
    root = tmp_path / "lib"
    a = root / "Dune"            # folder name does not match the author
    b = root / "Foundation"
    graph = _graph_with_dirs(a, b)
    book = _book(a, ["Frank Herbert"], Provenance.TAG.value)
    other = _book(b, ["Isaac Asimov"], Provenance.TAG.value)

    _resolve(graph, [book, other], root)

    # a single tagged book leaf is that book's title folder (its one book's author names the book,
    # not the folder), so the folder is not promoted to author and keeps the book's own tag author
    assert graph.directories[DirectoryNode.id_for(a)].kind == "title"
    assert book.authors == ["Frank Herbert"]
    assert book.provenance["authors"] == Provenance.TAG.value


def test_match_source_author_classifies_and_fills(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Robert A Heinlein"
    a = author_dir / "Stranger in a Strange Land"
    b = author_dir / "Starship Troopers"
    graph = _graph_with_dirs(a, b)

    # author resolved from a match source (audnexus), not a tag
    tagged = _book(a, ["Robert A. Heinlein"], "audnexus")
    untagged = _book(b, [])
    _resolve(graph, [tagged, untagged], root)

    node = graph.directories[DirectoryNode.id_for(author_dir)]
    assert node.kind == "author" and node.author == "Robert A. Heinlein"
    assert node.kind_source == "matched"
    assert untagged.authors == ["Robert A. Heinlein"]
    assert untagged.provenance["authors"] == Provenance.GRAPHING.value


def test_manual_author_classifies(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    a = author_dir / "Elantris"
    b = author_dir / "Mistborn"
    book = _book(a, ["Brandon Sanderson"], Provenance.MANUAL.value)
    other = _book(b, [])
    graph = _graph_with_dirs(a, b)

    _resolve(graph, [book, other], root)
    # a folder of title subfolders is an author grouping
    assert graph.directories[DirectoryNode.id_for(author_dir)].kind == "author"


def test_directory_provenance_author_uses_structure_not_the_book_tag(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Some Folder"
    # The book's only author is the folder name (circular directory inference). The engine does
    # not treat that as author *evidence* (ax_tag_author_match/consensus ignore directory
    # provenance), so the folder's author name comes from its structure, not the circular tag:
    # a folder of title subfolders is an author grouping named after itself.
    book = _book(author_dir / "Book", ["Some Folder"], Provenance.DIRECTORY.value)
    other = _book(author_dir / "Other", [])
    graph = _graph_with_dirs(author_dir / "Book", author_dir / "Other")

    _resolve(graph, [book, other], root)
    node = graph.directories[DirectoryNode.id_for(author_dir)]
    assert node.kind == "author" and node.author == "Some Folder"  # value is the fallback folder name


def test_single_book_leaf_without_a_title_stays_a_title(tmp_path):
    # Degenerate: a single-book leaf whose one book has no parsed title. With no title to eliminate
    # against, the engine keeps the conservative default (a title folder) rather than guessing author.
    root = tmp_path / "lib"
    folder = root / "Stephen King"
    book = _book(folder, ["Stephen King"], Provenance.TAG.value)  # note: no title set
    graph = _graph_with_dirs(folder)

    _resolve(graph, [book], root)

    assert graph.directories[DirectoryNode.id_for(folder)].kind == "title"


def test_single_book_leaf_folder_named_like_author_becomes_author(tmp_path):
    # Root/Author/OneBook.mp3 (the folder IS the author): the book's title comes from the filename
    # and does not resemble the folder name, so by elimination the folder can only be the author.
    from colophon.core.node_classify import book_identity_confidence
    root = tmp_path / "lib"
    folder = root / "Sean Flynn"
    book = _book(folder, ["Sean Flynn"], Provenance.DIRECTORY.value)
    book.title = "3000 Degrees"
    graph = _graph_with_dirs(folder)

    _resolve(graph, [book], root)

    node = graph.directories[DirectoryNode.id_for(folder)]
    assert node.kind == "author" and node.author == "Sean Flynn"
    # the payoff: the book now reads as locally identified, not a flat zero
    assert book_identity_confidence(book, graph, root) >= 60


def test_single_book_leaf_folder_named_like_title_stays_title(tmp_path):
    # Root/Title.mp3: the folder name resembles the book's own title, so it is a title folder and is
    # NOT invented into an author — even with a tagged author present.
    root = tmp_path / "lib"
    folder = root / "Dune"
    book = _book(folder, ["Frank Herbert"], Provenance.TAG.value)
    book.title = "Dune"
    graph = _graph_with_dirs(folder)

    _resolve(graph, [book], root)

    assert graph.directories[DirectoryNode.id_for(folder)].kind == "title"


def test_memoir_title_embedding_author_name_becomes_author(tmp_path):
    # A memoir/autobiography is often titled after its subject, so an author folder whose book title
    # embeds the author's name reads like a title match but is the author's folder.
    root = tmp_path / "lib"
    folder = root / "Sam Walton"
    book = _book(folder, [])
    book.title = "Sam Walton, made in America, my story"
    book.provenance["title"] = Provenance.FILENAME.value
    graph = _graph_with_dirs(folder)

    _resolve(graph, [book], root)

    node = graph.directories[DirectoryNode.id_for(folder)]
    assert node.kind == "author" and node.author == "Sam Walton"


def test_name_subset_without_memoir_marker_stays_title(tmp_path):
    # The memoir flip is gated on a marker: a plain name-subset title (folder is a fragment of the
    # title, no memoir marker) must NOT be flipped to author — e.g. 'Dune' under 'Dune Messiah'.
    root = tmp_path / "lib"
    folder = root / "Dune"
    book = _book(folder, [])
    book.title = "Dune Messiah"
    book.provenance["title"] = Provenance.FILENAME.value
    graph = _graph_with_dirs(folder)

    _resolve(graph, [book], root)

    assert graph.directories[DirectoryNode.id_for(folder)].kind == "title"


def test_single_book_leaf_folder_named_like_series_becomes_series(tmp_path):
    # Root/Series/OneBook.mp3: the folder resembles the book's series (not its title), so it is a
    # series folder.
    root = tmp_path / "lib"
    folder = root / "Mistborn"
    book = _book(folder, ["Brandon Sanderson"], Provenance.TAG.value)
    book.title = "The Final Empire"
    book.series = [SeriesRef(name="Mistborn", sequence=1)]
    book.provenance["series"] = Provenance.TAG.value
    graph = _graph_with_dirs(folder)

    _resolve(graph, [book], root)

    assert graph.directories[DirectoryNode.id_for(folder)].kind == "series"


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


def _ov(kind, value):
    from colophon.core.models import NodeOverride

    return NodeOverride(kind=kind, value=value)


def _apply_one(book, overrides, root):
    """Run apply_confirmed_overrides on a single book and return the resulting book."""
    from colophon.core.graph_resolve import apply_confirmed_overrides

    return apply_confirmed_overrides([book], overrides, root_for=lambda _b: root)[0]


def test_apply_confirmed_fills_empty_author(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    book = _book(author_dir / "Elantris", [])
    overrides = {str(author_dir): _ov("author", "Brandon Sanderson")}
    result = _apply_one(book, overrides, root)
    assert result.authors == ["Brandon Sanderson"]
    assert result.provenance["authors"] == Provenance.MANUAL.value
    assert book.authors == []  # input is never mutated


def test_apply_confirmed_fills_weak_author(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    book = _book(author_dir / "Elantris", ["Elantris"], Provenance.DIRECTORY.value)
    overrides = {str(author_dir): _ov("author", "Brandon Sanderson")}
    result = _apply_one(book, overrides, root)
    assert result.authors == ["Brandon Sanderson"]
    assert result.provenance["authors"] == Provenance.MANUAL.value


def test_apply_confirmed_does_not_overwrite_tag_author(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    book = _book(author_dir / "Tagged", ["Someone Else"], Provenance.TAG.value)
    overrides = {str(author_dir): _ov("author", "Brandon Sanderson")}
    result = _apply_one(book, overrides, root)
    assert result is book  # nothing to fill -> returned as-is, not copied
    assert result.authors == ["Someone Else"]
    assert result.provenance["authors"] == Provenance.TAG.value


def test_apply_confirmed_nested_author_and_series_both(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    series_dir = author_dir / "Mistborn"
    book = _book(series_dir / "Final Empire", [])
    overrides = {
        str(author_dir): _ov("author", "Brandon Sanderson"),
        str(series_dir): _ov("series", "Mistborn"),
    }
    result = _apply_one(book, overrides, root)
    assert result.authors == ["Brandon Sanderson"]
    assert [s.name for s in result.series] == ["Mistborn"]


def test_apply_confirmed_no_override_is_noop(tmp_path):
    root = tmp_path / "lib"
    book = _book(root / "Brandon Sanderson" / "Elantris", [])
    result = _apply_one(book, {}, root)
    assert result is book
    assert result.authors == [] and "authors" not in result.provenance


def test_apply_confirmed_franchise_does_not_touch_books(tmp_path):
    root = tmp_path / "lib"
    fdir = root / "Doctor Who"
    book = _book(fdir / "Book", [])
    overrides = {str(fdir): _ov("franchise", "DOCTOR WHO")}
    result = _apply_one(book, overrides, root)
    assert result is book
    assert result.authors == [] and "authors" not in result.provenance


def test_apply_confirmed_fills_weak_series(tmp_path):
    root = tmp_path / "lib"
    series_dir = root / "Mistborn"
    book = _book(series_dir / "Final Empire", [])
    book.series = [SeriesRef(name="Final Empire")]
    book.provenance["series"] = Provenance.DIRECTORY.value
    overrides = {str(series_dir): _ov("series", "Mistborn")}
    result = _apply_one(book, overrides, root)
    assert [s.name for s in result.series] == ["Mistborn"]
    assert result.provenance["series"] == Provenance.MANUAL.value


def test_apply_confirmed_nearest_author_wins(tmp_path):
    root = tmp_path / "lib"
    outer = root / "Anthologies"
    inner = outer / "Brandon Sanderson"
    book = _book(inner / "Elantris", [])
    overrides = {
        str(outer): _ov("author", "Various"),
        str(inner): _ov("author", "Brandon Sanderson"),
    }
    result = _apply_one(book, overrides, root)
    assert result.authors == ["Brandon Sanderson"]  # nearer ancestor wins


def test_apply_confirmed_leaves_graphing_author_untouched(tmp_path):
    # GRAPHING is not weak: a graph-inferred author is left alone on the match path
    # (a confirmed override already reached it via propagate_overrides at scan time).
    root = tmp_path / "lib"
    author_dir = root / "Brandon Sanderson"
    book = _book(author_dir / "Elantris", ["Inferred Author"], Provenance.GRAPHING.value)
    overrides = {str(author_dir): _ov("author", "Brandon Sanderson")}
    result = _apply_one(book, overrides, root)
    assert result is book
    assert result.authors == ["Inferred Author"]
    assert result.provenance["authors"] == Provenance.GRAPHING.value


def test_franchise_for_nearest_ancestor(tmp_path):
    from colophon.core.graph_resolve import franchise_for
    from colophon.core.models import NodeOverride

    root = tmp_path / "lib"
    book = root / "Doctor Who" / "Target Novels" / "Genesis"
    overrides = {str(root / "Doctor Who"): NodeOverride(kind="franchise", value="DOCTOR WHO")}
    assert franchise_for(book, overrides, root=root) == "DOCTOR WHO"


def test_franchise_for_none_without_override(tmp_path):
    from colophon.core.graph_resolve import franchise_for

    root = tmp_path / "lib"
    assert franchise_for(root / "Author" / "Book", {}, root=root) is None


def test_franchise_for_nearest_wins_over_farther(tmp_path):
    from colophon.core.graph_resolve import franchise_for
    from colophon.core.models import NodeOverride

    root = tmp_path / "lib"
    book = root / "Doctor Who" / "Target Novels" / "Genesis"
    overrides = {
        str(root / "Doctor Who"): NodeOverride(kind="franchise", value="OUTER"),
        str(root / "Doctor Who" / "Target Novels"): NodeOverride(kind="franchise", value="INNER"),
    }
    assert franchise_for(book, overrides, root=root) == "INNER"  # nearest ancestor wins


def _book_with_series(folder, title, series, seq=None):
    b = BookUnit.new(source_folder=folder)
    b.title = title
    b.series = [SeriesRef(name=series, sequence=seq)]
    return b


def test_resembles_series_matches_title_not_person(tmp_path):
    from colophon.core.graph_resolve import _resembles

    assert _resembles("Liz Carlyle", "Liz Carlyle")
    assert _resembles("The Liz Carlyle Novels", "Liz Carlyle")   # folder superset of series tokens
    assert _resembles("liz  carlyle", "Liz Carlyle")             # case/spacing tolerant
    assert not _resembles("stella Rimington", "Liz Carlyle")     # author folder, single series
    assert not _resembles("Sarah Graves", "Home Repair is Homicide")
    assert not _resembles("", "Liz Carlyle")
    assert not _resembles("Liz Carlyle", "")


def test_structural_author_for_untagged_single_series_container(tmp_path):
    root = tmp_path / "lib"
    author_dir = root / "stella Rimington"          # name does NOT resemble the series
    graph = _graph_with_dirs(author_dir)

    b1 = _book_with_series(author_dir, "Close Call", "Liz Carlyle", 8)
    b2 = _book_with_series(author_dir, "Secret Asset", "Liz Carlyle", 2)
    _resolve(graph, [b1, b2], root)

    node = graph.directories[DirectoryNode.id_for(author_dir)]
    assert node.kind == "author" and node.author == "stella Rimington"
    assert b1.authors == ["stella Rimington"]
    assert b1.provenance["authors"] == Provenance.GRAPHING.value


def test_series_named_folder_is_classified_series_not_author(tmp_path):
    root = tmp_path / "lib"
    series_dir = root / "Liz Carlyle"               # name DOES resemble the series
    graph = _graph_with_dirs(series_dir)

    b1 = _book_with_series(series_dir, "Close Call", "Liz Carlyle", 8)
    b2 = _book_with_series(series_dir, "Secret Asset", "Liz Carlyle", 2)
    _resolve(graph, [b1, b2], root)

    node = graph.directories[DirectoryNode.id_for(series_dir)]
    assert node.kind == "series"         # one series with a ramp, folder matches -> series, not author
    assert b1.authors == []              # a series fill never invents an author name


def test_folder_of_tagged_books_resolves_to_their_author(tmp_path):
    root = tmp_path / "lib"
    folder = root / "Mixed Bucket"
    graph = _graph_with_dirs(folder)

    # both books carry a strong (tag) author -> the folder resolves to that consensus author,
    # and the books, already strongly authored, are left untouched by the down-fill
    t1 = _book(folder, ["Real Author"], Provenance.TAG.value)
    t2 = _book(folder, ["Real Author"], Provenance.TAG.value)
    _resolve(graph, [t1, t2], root)

    node = graph.directories[DirectoryNode.id_for(folder)]
    assert node.kind == "author" and node.author == "Real Author"
    assert t1.authors == ["Real Author"] and t1.provenance["authors"] == Provenance.TAG.value


def test_scan_root_of_loose_books_stays_container(tmp_path):
    root = tmp_path / "lib"               # loose books directly in root (a bucket)
    graph = _graph_with_dirs(root)

    # the scan-root container prior outweighs a lone structural-author vote, so a bare root of
    # loose books is not named after the upload folder and no book inherits that name
    b1 = _book_with_series(root, "x", "Some Series", 1)
    b2 = _book(root, [])
    _resolve(graph, [b1, b2], root)

    assert graph.directories[DirectoryNode.id_for(root)].kind == "container"
    assert b2.authors == []


def test_structural_author_is_idempotent(tmp_path):
    from colophon.core.node_classify import classify_nodes

    root = tmp_path / "lib"
    author_dir = root / "stella Rimington"
    graph = _graph_with_dirs(author_dir)
    b1 = _book_with_series(author_dir, "Close Call", "Liz Carlyle", 8)
    b2 = _book_with_series(author_dir, "Secret Asset", "Liz Carlyle", 2)

    _resolve(graph, [b1, b2], root)
    first = list(b1.authors)
    classify_nodes(graph, [b1, b2], root=root, overrides={})   # second pass must not change anything
    assert b1.authors == first == ["stella Rimington"]
    assert b1.provenance["authors"] == Provenance.GRAPHING.value


def test_multibook_folder_named_like_a_book_still_becomes_author(tmp_path):
    # A true multibook folder cannot be an accurately-named *title* folder (its books have
    # distinct titles), so even when the folder name matches one of those titles it resolves to
    # author — the only surviving candidate once title is ruled out and series is not unanimous.
    root = tmp_path / "lib"
    folder = root / "Legion"             # name matches one held book, but two DISTINCT books live here
    graph = _graph_with_dirs(folder)

    b1 = BookUnit.new(source_folder=folder)
    b1.title = "Legion"
    b2 = BookUnit.new(source_folder=folder)
    b2.title = "Elantris"
    _resolve(graph, [b1, b2], root)

    node = graph.directories[DirectoryNode.id_for(folder)]
    assert node.kind == "author"
    assert node.author == "Legion"
    assert b1.authors == ["Legion"] and b2.authors == ["Legion"]
