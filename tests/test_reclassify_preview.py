"""preview_node_classification returns a blast-radius diff (ReclassifyPreview) without persisting
anything: the computed changes must match what set_node_classification actually applies."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph_classify import classify_graph
from colophon.core.graph_records import graph_records
from colophon.core.models import Phase, PhaseState
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


def test_preview_matches_apply_for_an_author_reclassify(tmp_path):
    ctx = _seed_author_library(tmp_path)
    c = AppController(ctx)
    author_dir = tmp_path / "ingest" / "Brandon Sanderson"

    preview = c.preview_node_classification(author_dir, "author", "B. Sanderson")

    author_changes = {ch.book_id: ch.after for ch in preview.changes if ch.field == "authors"}
    assert preview.book_count >= 1 and author_changes  # reports the ripple

    c.set_node_classification(author_dir, "author", "B. Sanderson")  # actually apply

    for bid, after in author_changes.items():
        stored = ctx.books.get(bid)
        assert stored is not None
        assert ", ".join(stored.authors) == after  # apply == preview

    ctx.close()


def test_preview_has_no_side_effects(tmp_path):
    ctx = _seed_author_library(tmp_path)
    c = AppController(ctx)
    author_dir = tmp_path / "ingest" / "Brandon Sanderson"

    before_authors = {b.id: list(b.authors) for b in ctx.books.list_all()}
    before_ov = dict(ctx.overrides.all())

    c.preview_node_classification(author_dir, "author", "X")

    assert {b.id: list(b.authors) for b in ctx.books.list_all()} == before_authors  # books untouched
    assert dict(ctx.overrides.all()) == before_ov  # nothing persisted

    ctx.close()


def test_preview_of_a_noop_reclassify_is_empty(tmp_path):
    ctx = _seed_author_library(tmp_path)
    c = AppController(ctx)
    author_dir = tmp_path / "ingest" / "Brandon Sanderson"

    # Re-asserting the folder's existing classification changes nothing — and this holds even though
    # the seed leaves books with stale stored state, because the preview diffs against a fresh
    # without-override baseline, not the stored books, so pre-existing drift is not misattributed.
    preview = c.preview_node_classification(author_dir, "author", "Brandon Sanderson")

    assert preview.changes == [] and preview.book_count == 0

    ctx.close()
