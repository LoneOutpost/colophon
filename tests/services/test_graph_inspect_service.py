"""Service-level tests for graph_inspect.search()."""

from unittest.mock import MagicMock

from colophon.core.graph_records import NodeRecord
from colophon.core.library_graph import LibraryGraph
from colophon.services.graph_inspect import search


def test_search_caption_labels_classified_folder_vs_entity():
    """search() kind caption must say 'Author Folder' for a classified directory
    and 'Author' for the author entity node — even when both share the same name."""
    nodes = [
        NodeRecord(id="af", physical="directory", semantic="author",
                   root="/lib", attrs={"name": "Clive Barker"}),
        NodeRecord(id="ae", physical=None, semantic="author",
                   root="/lib", attrs={"name": "Clive Barker"}),
    ]
    g = LibraryGraph.from_records(nodes, [])

    # Author nodes have no book_id, so the repo is never dereferenced; a stub that
    # would return None for any get() keeps the test fixture-free and safe.
    books = MagicMock()
    books.get.return_value = None
    hits = search(g, books, "Clive")

    kinds = {h["id"]: h["kind"] for h in hits}
    assert kinds["af"] == "Author Folder"
    assert kinds["ae"] == "Author"


def test_provenance_confidence_rendered_as_percent():
    """A folder's classification confidence (kind_confidence, 0..1) renders as a percent, not a
    raw 0.00-1.00 float, so it reads consistently with the book/match confidences shown elsewhere."""
    from colophon.core.graph_records import NodeRecord
    from colophon.services.graph_inspect import _provenance_of

    prov = _provenance_of(MagicMock())
    folder = NodeRecord(id="af", physical="directory", semantic="author", root="/lib",
                        attrs={"name": "X", "kind": "author", "kind_confidence": 1.0})
    assert "confidence 100%" in prov(folder)[0]
