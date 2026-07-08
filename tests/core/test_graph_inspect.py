from colophon.core.graph_inspect import NodeInspection, inspect
from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph


def _n(id, *, physical=None, semantic=None, name=None, book_id=None) -> NodeRecord:
    attrs = {}
    if name:
        attrs["name"] = name
    if book_id:
        attrs["book_id"] = book_id
    return NodeRecord(id=id, physical=physical, semantic=semantic, root="/lib", attrs=attrs)


def _e(src, kind, dst) -> EdgeRecord:
    return EdgeRecord(src=src, kind=kind, dst=dst, root="/lib")


def _graph() -> LibraryGraph:
    nodes = [
        _n("dir", physical="directory", name="Liz Carlyle"),
        _n("book:b1", semantic="book", book_id="b1"),
        _n("book:b2", semantic="book", book_id="b2"),
        _n("f1", physical="file", name="01.mp3"),
        _n("author:aa", semantic="author", name="Stella Rimington"),
        _n("series:ss", semantic="series", name="Liz Carlyle"),
    ]
    edges = [
        _e("dir", "contains", "book:b1"), _e("dir", "contains", "book:b2"),
        _e("dir", "contains", "f1"),
        _e("book:b1", "owns", "f1"),
        _e("book:b1", "author", "author:aa"), _e("book:b2", "author", "author:aa"),
        _e("book:b1", "series", "series:ss"), _e("book:b2", "series", "series:ss"),
    ]
    return LibraryGraph.from_records(nodes, edges)


def _name(node) -> str:
    return str(node.attrs.get("name") or node.id)


def _inspect(g, focal):
    return inspect(g, focal, name_of=_name, confidence_of=lambda n: None,
                   provenance_of=lambda n: [])


def test_inspect_tolerates_dangling_edges():
    # A pruned node can leave edges behind (dangling endpoints). Inspecting a node touched by
    # such edges must not raise — it should just skip the unresolvable endpoints.
    nodes = [_n("d", physical="directory", name="D"), _n("book:b1", semantic="book", book_id="b1")]
    edges = [
        _e("d", "contains", "ghostchild"),          # folder framing: child missing
        _e("ghostparent", "contains", "book:b1"),   # book framing: parent missing
        _e("book:b1", "owns", "ghostfile"),         # files list: dst missing
        _e("book:b1", "author", "ghostauthor"),     # author names: dst missing
    ]
    g = LibraryGraph.from_records(nodes, edges)
    vf = _inspect(g, "d")
    vb = _inspect(g, "book:b1")
    assert vf.id == "d" and vb.id == "book:b1"
    assert any(lbl == "Contains" for lbl, _ in vf.rows)


def test_series_reads_books_in_series_and_linked_folders():
    got = _inspect(_graph(), "series:ss")
    assert got.kind == "series"
    assert ("Books in series", "2") in got.rows
    assert got.linked_folders == ["Liz Carlyle"]  # both books live in the one folder


def test_author_reads_books_and_linked_folders():
    got = _inspect(_graph(), "author:aa")
    assert got.kind == "author"
    assert ("Books by this author", "2") in got.rows
    assert got.linked_folders == ["Liz Carlyle"]


def test_book_reads_folder_author_and_files():
    got = _inspect(_graph(), "book:b1")
    assert got.kind == "book"
    assert ("In folder", "Liz Carlyle") in got.rows
    assert ("Author", "Stella Rimington") in got.rows
    assert got.files == ["01.mp3"]


def test_folder_reads_contains_breakdown():
    got = _inspect(_graph(), "dir")
    assert got.kind == "folder"
    assert ("Contains", "2 books, 0 folders, 1 file") in got.rows


def test_file_reads_part_of_book():
    got = _inspect(_graph(), "f1")
    assert got.kind == "file"
    assert ("Part of book", "book:b1") in got.rows  # name_of falls back to the book node id here
    assert ("In folder", "Liz Carlyle") in got.rows


def test_classified_folder_uses_folder_structure_and_display_kind():
    # a physical directory carrying a semantic="author" facet: caption/links follow display_kind,
    # but relationships are framed structurally (as a folder), not as a logical author entity.
    nodes = [
        _n("af", physical="directory", semantic="author", name="Brandon Sanderson"),
        _n("book:b9", semantic="book", book_id="b9"),
    ]
    edges = [_e("af", "contains", "book:b9")]
    g = LibraryGraph.from_records(nodes, edges)
    got = _inspect(g, "af")
    assert got.kind == "author"                                # caption/links follow display_kind
    assert any(label == "Contains" for label, _ in got.rows)   # framed as a folder
    assert got.linked_folders == []


def test_missing_focal_is_empty():
    got = _inspect(_graph(), "nope")
    assert got == NodeInspection(id="nope", label="", kind="", type_caption="", confidence=None,
                                 rows=[], linked_folders=[], files=[], provenance=[], links=[])


def test_inspect_type_caption_distinguishes_folder_from_entity():
    folder = _n("af", physical="directory", semantic="author", name="Clive Barker")
    entity = _n("ae", semantic="author", name="Clive Barker")
    g = LibraryGraph.from_records([folder, entity], [])
    assert _inspect(g, "af").type_caption == "Author Folder"
    assert _inspect(g, "af").kind == "author"   # kind unchanged (routes links)
    assert _inspect(g, "ae").type_caption == "Author"


def test_provenance_passthrough():
    got = inspect(_graph(), "dir", name_of=_name, confidence_of=lambda n: None,
                  provenance_of=lambda n: ["Classified as author"])
    assert got.provenance == ["Classified as author"]


def test_links_per_kind():
    from colophon.core.graph_inspect import NodeLink, _links_for

    author = _links_for("author", "Stella Rimington", "author:aa")
    assert NodeLink("Open in Library", "/?filter=Stella%20Rimington") in author
    assert NodeLink("Manage → Authors", "/manage?kind=author&filter=Stella%20Rimington") in author

    series = _links_for("series", "Liz Carlyle", "series:ss")
    assert NodeLink("Manage → Series", "/manage?kind=series&filter=Liz%20Carlyle") in series

    # franchise gets a Library link but no Manage vocabulary
    franchise = _links_for("franchise", "Cosmere", "franchise:cc")
    assert any(link.url.startswith("/?filter=") for link in franchise)
    assert not any("/manage" in link.url for link in franchise)

    # a book opens in the library filtered by its title
    book = _links_for("book", "Dune", "book:b1")
    assert book == [NodeLink("Open in Library", "/?filter=Dune")]

    # _links_for itself has no owner id, so it returns nothing for a file...
    assert _links_for("file", "01.mp3", "f1") == []
    # ...the file→book jump is added by inspect(), which knows the owning book:
    got = _inspect(_graph(), "f1")
    assert got.links == [NodeLink("Jump to its book", "/graph?focal=book%3Ab1")]

    # a folder has no external link yet (classify lands in 3.2)
    assert _links_for("folder", "Some Folder", "dir") == []
