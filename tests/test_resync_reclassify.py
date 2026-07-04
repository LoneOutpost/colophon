"""_resync_roots re-derives directory classification in memory (no disk walk) and persists it,
so the maintained graph carries current classification after every edit."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph import DirectoryNode
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import graph_records
from colophon.core.node_classify import classify_nodes
from colophon.services.graph_build import build_graph


def test_resync_rederives_directory_classification_in_memory(tmp_path):
    ingest = tmp_path / "ingest"
    for t in ["Elantris", "Warbreaker"]:
        d = ingest / "Brandon Sanderson" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")

    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    # Seed as a scan would: build the graph, persist skeleton + book records + books.
    g = build_graph(ctx.books, ingest, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    for b in books:
        ctx.books.upsert(b)
    classify_graph(g, root=ingest)
    classify_nodes(g, books, root=ingest, overrides={})
    ctx.library_graph.replace_root(str(ingest), *graph_records(g, books, root=ingest))

    # Blank the persisted classification so the assertion can only pass via re-derivation.
    for n in ctx.library_graph.nodes.values():
        if n.physical == "directory":
            n.attrs["kind"] = "unknown"
            n.attrs.pop("kind_value", None)

    AppController(ctx)._resync_books(books)  # an edit's resync path

    rec = ctx.library_graph.nodes[DirectoryNode.id_for(ingest / "Brandon Sanderson")]
    assert rec.attrs["kind"] == "author"
    assert rec.attrs["kind_value"] == "Brandon Sanderson"
    ctx.close()
