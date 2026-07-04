"""book_identity_confidence: a book's local-identification confidence (0-100) rolled up from the
graph evidence + the book's own provenance — how sure we are we know it locally, pre-match."""

from colophon.core.graph import DirectoryNode, Graph
from colophon.core.models import BookUnit, Provenance, SeriesRef
from colophon.core.node_classify import book_identity_confidence


def _author_node(g: Graph, folder, *, conf: float, name: str) -> None:
    d = DirectoryNode(path=folder, kind="author", kind_confidence=conf, kind_value=name, author=name)
    g.directories[d.id] = d


def _series_node(g: Graph, folder, *, conf: float, name: str) -> None:
    d = DirectoryNode(path=folder, kind="series", kind_confidence=conf, kind_value=name)
    g.directories[d.id] = d


def _book(folder, *, title="At Risk", author=None, a_prov=None, series=None, s_prov=None) -> BookUnit:
    b = BookUnit.new(source_folder=folder)
    b.title = title
    if author:
        b.authors = [author]
        b.provenance["authors"] = a_prov
    if series:
        b.series = [SeriesRef(name=series)]
        b.provenance["series"] = s_prov
    return b


def test_graph_inherited_author_inherits_node_confidence(tmp_path):
    g = Graph()
    af = tmp_path / "Stella Rimington"
    _author_node(g, af, conf=0.9, name="Stella Rimington")
    book = _book(af / "At Risk", author="Stella Rimington", a_prov=Provenance.GRAPHING.value)
    assert book_identity_confidence(book, g, tmp_path) == 90


def test_tag_author_is_strong_regardless_of_graph(tmp_path):
    book = _book(tmp_path / "loose" / "At Risk", author="Stella Rimington", a_prov=Provenance.TAG.value)
    assert book_identity_confidence(book, Graph(), tmp_path) == 90  # the file itself says so


def test_folder_name_author_leans_on_node_confidence(tmp_path):
    g = Graph()
    af = tmp_path / "Stella Rimington"
    _author_node(g, af, conf=0.9, name="Stella Rimington")
    book = _book(af / "At Risk", author="Stella Rimington", a_prov=Provenance.DIRECTORY.value)
    assert book_identity_confidence(book, g, tmp_path) == 90  # backed by a confident author node


def test_folder_name_author_without_classification_is_low(tmp_path):
    af = tmp_path / "Stella Rimington"
    book = _book(af / "At Risk", author="Stella Rimington", a_prov=Provenance.DIRECTORY.value)
    assert book_identity_confidence(book, Graph(), tmp_path) == 0  # graph never confirmed it's an author


def test_no_author_no_series_is_zero(tmp_path):
    book = _book(tmp_path / "mystery", title="Some Title")
    assert book_identity_confidence(book, Graph(), tmp_path) == 0


def test_series_corroborates_author(tmp_path):
    g = Graph()
    af = tmp_path / "Stella Rimington"
    sf = af / "Liz Carlyle"
    _author_node(g, af, conf=0.9, name="Stella Rimington")
    _series_node(g, sf, conf=0.8, name="Liz Carlyle")
    book = _book(sf / "At Risk", author="Stella Rimington", a_prov=Provenance.GRAPHING.value,
                 series="Liz Carlyle", s_prov=Provenance.GRAPHING.value)
    assert book_identity_confidence(book, g, tmp_path) == 100  # 0.9 + 0.1 corroboration


def test_missing_title_discounts(tmp_path):
    book = _book(tmp_path / "x", title="", author="Stella Rimington", a_prov=Provenance.TAG.value)
    assert book_identity_confidence(book, Graph(), tmp_path) == 63  # 0.9 * 0.7
