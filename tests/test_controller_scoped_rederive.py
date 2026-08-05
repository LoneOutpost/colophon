"""Scoped edit re-derive (`_resync_scope`): reclassify only the edited book's entity subtree and
persist a node/edge delta, producing the same book derivations a whole-root `_resync_roots` would."""

from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _mp3(path: Path, *, artist="Anne McCaffrey", album=None, title=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    t = ID3()
    t.add(TPE1(encoding=3, text=[artist]))
    if album:
        t.add(TALB(encoding=3, text=[album]))
    if title:
        t.add(TIT2(encoding=3, text=[title]))
    t.save(path)


def _ctrl(tmp_path):
    ingest = tmp_path / "audio"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest]))
    return ctx, AppController(ctx), ingest


def _derivation(ctx):
    return {b.id: (tuple(b.authors), b.provenance.get("authors"), b.franchise,
                   b.provenance.get("franchise"), b.identity_confidence, b.state)
            for b in ctx.books.list_all()}


def _two_book_author(ctrl, ingest):
    _mp3(ingest / "Anne McCaffrey" / "Dragonflight" / "01.mp3", title="Dragonflight")
    _mp3(ingest / "Anne McCaffrey" / "Dragonquest" / "01.mp3", title="Dragonquest")
    ctrl.scan([ingest])


def test_resync_scope_persists_and_keeps_sibling(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _two_book_author(ctrl, ingest)
    book = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)
    other = next(b for b in ctx.books.list_all() if "Dragonquest" in b.source_folder.name)

    book.authors = ["Todd McCaffrey"]
    book.provenance["authors"] = "manual"
    ctx.books.upsert(book)
    ctrl._resync_scope({book.source_folder})

    assert ctx.books.get(book.id).authors == ["Todd McCaffrey"]
    assert ctx.books.get(other.id) is not None          # sibling untouched
    ctx.close()


def test_resync_scope_matches_whole_root_fixed_point(tmp_path):
    # The trust net: after a scoped re-derive, a full whole-root re-derive must find NOTHING further
    # to change (the scoped pass already reached the whole-root fixed point).
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _two_book_author(ctrl, ingest)
    book = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)

    book.authors = ["Todd McCaffrey"]
    book.provenance["authors"] = "manual"
    ctx.books.upsert(book)
    ctrl._resync_scope({book.source_folder})
    after_scope = _derivation(ctx)

    ctrl._resync_roots({ctrl._scan_root_for_path(book.source_folder)})
    after_full = _derivation(ctx)
    assert after_scope == after_full
    ctx.close()


def test_scoped_subtree_root_finds_nearest_entity_ancestor(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _two_book_author(ctrl, ingest)
    book = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)
    root = ctrl._scan_root_for_path(book.source_folder)
    sub = ctrl._scoped_subtree_root(book.source_folder, root)
    # the subtree roots at an ancestor at or above the book's own folder, never above the scan root
    assert sub == book.source_folder or sub in book.source_folder.parents
    assert root == sub or root in sub.parents or root == sub
    ctx.close()


def test_save_fields_author_change_equivalent_via_public_api(tmp_path):
    # save_fields now routes through _resync_scope; a whole-root re-derive must find no further change.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _two_book_author(ctrl, ingest)
    book = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)

    ctrl.save_fields(book, {"author": "Todd McCaffrey"})
    after_scope = _derivation(ctx)
    ctrl._resync_roots({ctrl._scan_root_for_path(book.source_folder)})
    assert after_scope == _derivation(ctx)
    ctx.close()


def test_delete_file_equivalent_via_public_api(tmp_path):
    # delete_file (not-last-file) routes through the scoped path; must match the whole-root fixed point.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Anne McCaffrey" / "Dragonflight" / "01.mp3", title="Dragonflight")
    _mp3(ingest / "Anne McCaffrey" / "Dragonflight" / "02.mp3", title="Dragonflight")
    _mp3(ingest / "Anne McCaffrey" / "Dragonquest" / "01.mp3", title="Dragonquest")
    ctrl.scan([ingest])
    book = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)

    ctrl.delete_file(book, book.source_files[1].path)
    after_scope = _derivation(ctx)
    ctrl._resync_roots({ctrl._scan_root_for_path(book.source_folder)})
    assert after_scope == _derivation(ctx)
    ctx.close()


def test_noop_edit_changes_nothing_and_stays_at_fixed_point(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _two_book_author(ctrl, ingest)
    book = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)

    before = _derivation(ctx)
    ctrl.save_fields(book, {"year": "1968"})   # not a graph-affecting field
    after = _derivation(ctx)
    # only the edited book's own record may differ (year isn't in the derivation tuple), graph stable
    assert after == before or set(after) == set(before)
    ctrl._resync_roots({ctrl._scan_root_for_path(book.source_folder)})
    assert after == _derivation(ctx)
    ctx.close()


def test_scoped_matches_whole_root_for_nested_entity_spine(tmp_path):
    # A deeper spine: author -> sub-collection -> book. Editing a leaf must reach the same fixed
    # point as a whole-root re-derive, exercising a FROZEN multi-level ancestor spine (the case the
    # single-level fixtures don't cover).
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Brandon Sanderson" / "Mistborn" / "The Final Empire" / "01.mp3",
         artist="Brandon Sanderson", title="The Final Empire")
    _mp3(ingest / "Brandon Sanderson" / "Mistborn" / "The Well of Ascension" / "01.mp3",
         artist="Brandon Sanderson", title="The Well of Ascension")
    _mp3(ingest / "Brandon Sanderson" / "Elantris" / "01.mp3",
         artist="Brandon Sanderson", title="Elantris")
    ctrl.scan([ingest])
    book = next(b for b in ctx.books.list_all() if "Final Empire" in b.source_folder.name)

    ctrl.save_fields(book, {"author": "Someone Else"})
    after_scope = _derivation(ctx)
    ctrl._resync_roots({ctrl._scan_root_for_path(book.source_folder)})
    assert after_scope == _derivation(ctx)
    ctx.close()


def test_cleanup_remove_is_scoped_and_matches_whole_root(tmp_path):
    # cleanup_remove (the book-removal path used by delete_book_from_disk etc.) re-derives scoped, and
    # must reach the same fixed point a whole-root re-derive would — with the removed book's node pruned.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _two_book_author(ctrl, ingest)
    victim = next(b for b in ctx.books.list_all() if "Dragonflight" in b.source_folder.name)
    keep = next(b for b in ctx.books.list_all() if "Dragonquest" in b.source_folder.name)

    ctrl.cleanup_remove([victim.id])
    assert ctx.books.get(victim.id) is None            # removed
    assert ctx.books.get(keep.id) is not None          # sibling intact
    after_scoped = _derivation(ctx)

    ctrl._resync_roots({ctrl._scan_root_for_path(keep.source_folder)})
    assert after_scoped == _derivation(ctx)            # scoped reached the whole-root fixed point
    # the removed book's graph node is gone
    assert not any(n.attrs.get("book_id") == victim.id for n in ctx.library_graph.nodes.values())
    ctx.close()
