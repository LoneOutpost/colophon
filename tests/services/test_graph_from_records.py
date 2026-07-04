"""`graph_from_records` rebuilds the structural Graph from the persisted library-graph records
(no filesystem walk) and re-classifies to the SAME result as a fresh disk build — the basis for
making the classic tree a view of the maintained graph instead of a fresh rebuild."""

from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.graph import DirectoryNode
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import graph_from_records, graph_records
from colophon.core.node_classify import classify_nodes
from colophon.services.graph_build import build_graph


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn)


def _classification(graph) -> dict[str, tuple[str, str | None]]:
    return {str(d.path): (d.kind, d.kind_value) for d in graph.directories.values()}


def test_reconstructed_graph_classifies_identically_to_a_fresh_build(tmp_path):
    ingest = tmp_path / "ingest"
    layout = {"Brandon Sanderson": ["Elantris", "Warbreaker"],
              "Robin Hobb": ["Assassins Apprentice"]}
    for author, titles in layout.items():
        for t in titles:
            d = ingest / author / t
            d.mkdir(parents=True)
            (d / "01.mp3").write_bytes(b"")

    # Fresh: build the structure from disk, then classify.
    fresh = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")
    books = [bn.book for bn in fresh.books.values()]
    # classify_nodes mutates books (fill_down), so snapshot clean copies for the recon pass.
    clean = {b.id: b.model_copy(deep=True) for b in books}
    classify_graph(fresh, root=ingest)
    classify_nodes(fresh, books, root=ingest, overrides={})

    # Persist to records, rebuild WITHOUT disk from the records + books, then re-classify.
    nodes, edges = graph_records(fresh, books, root=ingest)
    recon = graph_from_records(nodes, edges, clean, root=ingest)
    recon_books = [bn.book for bn in recon.books.values()]
    classify_graph(recon, root=ingest)
    classify_nodes(recon, recon_books, root=ingest, overrides={})

    # The reconstruction carries enough structure to classify identically.
    assert _classification(recon) == _classification(fresh)
    # ...and it actually classified something (guards a degenerate all-unknown match).
    assert (ingest / "Brandon Sanderson") and \
        _classification(recon)[str(ingest / "Brandon Sanderson")][0] == "author"
    assert _classification(recon)[str(ingest / "Brandon Sanderson" / "Elantris")][0] == "title"


def test_reconstruction_handles_a_multi_book_folder(tmp_path):
    # A folder whose files fan out into multiple leaf book nodes must round-trip through records.
    ingest = tmp_path / "ingest"
    loose = ingest / "Loose"
    loose.mkdir(parents=True)
    (loose / "Alpha Story.mp3").write_bytes(b"")
    (loose / "Beta Tale.mp3").write_bytes(b"")

    fresh = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")
    books = [bn.book for bn in fresh.books.values()]
    clean = {b.id: b.model_copy(deep=True) for b in books}
    classify_graph(fresh, root=ingest)
    classify_nodes(fresh, books, root=ingest, overrides={})

    nodes, edges = graph_records(fresh, books, root=ingest)
    recon = graph_from_records(nodes, edges, clean, root=ingest)
    assert len(recon.books) == len(fresh.books)  # every leaf book node came back
    classify_graph(recon, root=ingest)
    classify_nodes(recon, [bn.book for bn in recon.books.values()], root=ingest, overrides={})
    assert _classification(recon) == _classification(fresh)


def test_reconstruction_respects_a_manual_override(tmp_path):
    from colophon.core.models import NodeOverride

    ingest = tmp_path / "ingest"
    for t in ["Elantris", "Warbreaker"]:
        d = ingest / "Brandon Sanderson" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")
    overrides = {str(ingest / "Brandon Sanderson"): NodeOverride(kind="series", value="Cosmere")}

    fresh = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")
    books = [bn.book for bn in fresh.books.values()]
    clean = {b.id: b.model_copy(deep=True) for b in books}
    classify_graph(fresh, root=ingest)
    classify_nodes(fresh, books, root=ingest, overrides=overrides)

    nodes, edges = graph_records(fresh, books, root=ingest)
    recon = graph_from_records(nodes, edges, clean, root=ingest)
    classify_graph(recon, root=ingest)
    classify_nodes(recon, [bn.book for bn in recon.books.values()], root=ingest, overrides=overrides)

    assert _classification(recon) == _classification(fresh)
    assert _classification(recon)[str(ingest / "Brandon Sanderson")] == ("series", "Cosmere")


def test_read_path_restores_persisted_classification_without_reclassify(tmp_path):
    # The read path: reconstruct with restore_classification=True and render the STORED classification
    # (incl. kind_value), no reclassify. Proves kind_value survives the record round-trip.
    ingest = tmp_path / "ingest"
    for t in ["Elantris", "Warbreaker"]:
        d = ingest / "Brandon Sanderson" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")

    fresh = build_graph(_repo(tmp_path), ingest, template="$Author - $Title")
    books = [bn.book for bn in fresh.books.values()]
    classify_graph(fresh, root=ingest)
    classify_nodes(fresh, books, root=ingest, overrides={})

    nodes, edges = graph_records(fresh, books, root=ingest)
    recon = graph_from_records(
        nodes, edges, {b.id: b for b in books}, root=ingest, restore_classification=True
    )
    assert _classification(recon) == _classification(fresh)  # no reclassify needed
    author = recon.directories[DirectoryNode.id_for(ingest / "Brandon Sanderson")]
    assert author.kind == "author" and author.kind_value == "Brandon Sanderson"
