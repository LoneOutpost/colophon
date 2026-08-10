"""recompute_identity: library-wide, graph-only refresh of identity/repair state (no disk)."""
import shutil

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import graph_records
from colophon.core.models import BookState, Phase, PhaseState
from colophon.core.node_classify import classify_nodes
from colophon.core.phases import mark, resync_state
from colophon.services.graph_build import build_graph


def _seed(tmp_path):
    """Two titles under one author folder, persisted as a completed local scan would leave them."""
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
    classify_nodes(g, books, root=ingest, overrides={})
    for b in books:
        mark(b, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(b)
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(ingest), *graph_records(g, books, root=ingest))
    return ctx, ingest


def test_recompute_is_disk_free_and_persists_verdict(tmp_path):
    ctx, ingest = _seed(tmp_path)
    # Corrupt the stored confidence + blank the verdict so a correct re-derive must change them.
    for b in ctx.books.list_all():
        b.identity_confidence = 0.0
        b.title_corroboration = None
        ctx.books.upsert(b)
    ctrl = AppController(ctx)
    shutil.rmtree(ingest)  # prove no filesystem access: the source tree is gone

    summary = ctrl.recompute_identity()

    assert summary.updated >= 2
    for b in ctx.books.list_all():
        assert b.identity_confidence > 0
        assert b.title_corroboration in {"agree", "abstain", "contradict"}
    ctx.close()


def test_recompute_reports_out_of_review_movement(tmp_path):
    ctx, _ingest = _seed(tmp_path)
    # Force one stored book to look stuck in review; the graph gives a strong author, so a
    # recompute re-derives it back to identified -> it leaves review.
    victim = ctx.books.list_all()[0]
    victim.state = BookState.NEEDS_REVIEW
    victim.identity_confidence = 5.0
    ctx.books.upsert(victim)

    summary = AppController(ctx).recompute_identity()

    assert summary.out_of_review == 1
    assert summary.updated >= 1
    ctx.close()


def test_recompute_is_idempotent(tmp_path):
    ctx, _ = _seed(tmp_path)
    ctrl = AppController(ctx)
    ctrl.recompute_identity()                 # first run settles any drift
    second = ctrl.recompute_identity()
    assert second.updated == 0
    assert second.into_review == 0 and second.out_of_review == 0
    ctx.close()


def test_recompute_empty_scan_paths_is_a_noop(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", scan_paths=[]))
    summary = AppController(ctx).recompute_identity()
    assert (summary.updated, summary.into_review, summary.out_of_review) == (0, 0, 0)
    ctx.close()


def test_recompute_cleans_dirty_title_and_year(tmp_path):
    ctx, _ = _seed(tmp_path)
    victim = ctx.books.list_all()[0]
    victim.title = "SB 01 - " + victim.title   # e.g. "SB 01 - Elantris"
    victim.publish_year = 1
    ctx.books.upsert(victim)
    ctrl = AppController(ctx)

    ctrl.recompute_identity()

    healed = ctx.books.get(victim.id)
    assert healed.title == "Elantris"
    assert healed.publish_year is None
    assert ctrl.recompute_identity().updated == 0   # idempotent: second pass changes nothing
    ctx.close()
