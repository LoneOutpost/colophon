"""_resync_roots re-derives directory classification in memory (no disk walk) and persists it,
so the maintained graph carries current classification after every edit."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph import DirectoryNode
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import graph_records
from colophon.core.library_graph import LibraryGraph
from colophon.core.models import BookState, Phase, PhaseState, SourceFile
from colophon.core.node_classify import classify_nodes
from colophon.core.phases import mark
from colophon.services.graph_build import build_graph


def _seed_author_library(tmp_path):
    """Two titles under one author folder, seeded as a completed (pre-match) scan would leave them:
    graph persisted + classified, books carrying their GRAPHING author and IDENTIFY marked fresh."""
    ingest = tmp_path / "ingest"
    for t in ["Elantris", "Warbreaker"]:
        d = ingest / "Brandon Sanderson" / t
        d.mkdir(parents=True)
        (d / "01.mp3").write_bytes(b"")
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    g = build_graph(ctx.books, ingest, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    classify_graph(g, root=ingest)
    classify_nodes(g, books, root=ingest, overrides={})  # fill_down the author onto each book
    for b in books:
        mark(b, Phase.IDENTIFY, PhaseState.FRESH)  # identified locally, not yet source-matched
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(ingest), *graph_records(g, books, root=ingest))
    return ctx


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


def test_recompute_backfills_identity_confidence_and_state(tmp_path):
    ctx = _seed_author_library(tmp_path)
    # Simulate a pre-harmonization catalog: confidence + state predate the identity signal.
    for b in ctx.books.list_all():
        b.identity_confidence = 0.0
        b.state = BookState.NEEDS_REVIEW

    updated = AppController(ctx).recompute_all_identity()

    assert updated == 2
    stored = ctx.books.list_all()
    assert all(b.identity_confidence > 0 for b in stored)  # rolled up from the 0.9 author node
    assert all(b.state is BookState.IDENTIFIED for b in stored)  # locally confident, unmatched
    ctx.close()


def test_recompute_is_idempotent(tmp_path):
    ctx = _seed_author_library(tmp_path)
    controller = AppController(ctx)
    controller.recompute_all_identity()  # first pass harmonizes

    assert controller.recompute_all_identity() == 0  # nothing left to move

    ctx.close()


def test_resync_does_not_persist_dangling_edges(tmp_path):
    """A book whose source paths have drifted from the skeleton (a match/organize changed them)
    must not leave the write-through with a dangling owns/contains edge — neither in memory nor
    in the store."""
    ctx = _seed_author_library(tmp_path)
    controller = AppController(ctx)

    # Point a stored book at a file the skeleton has no node for, then re-derive (persist first,
    # per the _resync_roots contract).
    book = ctx.books.list_all()[0]
    book.source_files = [
        SourceFile(path=book.source_folder / "ghost.mp3", size=1, duration_seconds=1.0, ext=".mp3")
    ]
    ctx.books.upsert(book)
    controller._resync_roots({controller._scan_root_for_path(book.source_folder)})

    lib = ctx.library_graph
    ids = set(lib.nodes)
    assert [e for e in lib.edges if e.src not in ids or e.dst not in ids] == []  # in memory
    reloaded = LibraryGraph.from_records(*ctx.graph.load_all())
    rids = set(reloaded.nodes)
    assert [e for e in reloaded.edges if e.src not in rids or e.dst not in rids] == []  # persisted
    ctx.close()
