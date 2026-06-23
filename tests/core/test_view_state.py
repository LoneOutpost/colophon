from pathlib import Path

from colophon.core.view_state import RestoredView, snapshot_to_view, view_to_snapshot


def test_round_trip_basic():
    snap = view_to_snapshot(
        scope={"kind": "author", "key": "Brandon Sanderson"},
        folder_filter={"path": Path("/lib/a")},
        view={"mode": "library", "cwd": None, "multiselect": True, "group_by": "series"},
        filter_text="dune",
        selected_ids={"b2", "b1"},
        open_book_id="b1",
    )
    assert snap["scope"] == {"kind": "author", "key": "Brandon Sanderson"}
    assert snap["folder_filter"] == "/lib/a"
    assert snap["view"]["group_by"] == "series" and snap["view"]["multiselect"] is True
    assert snap["filter"] == "dune"
    assert snap["selected_ids"] == ["b1", "b2"]
    assert snap["open_book_id"] == "b1"

    out = snapshot_to_view(
        snap, known_book_ids={"b1", "b2"},
        known_authors={"Brandon Sanderson"}, known_series=set(),
    )
    assert isinstance(out, RestoredView)
    assert out.scope == {"kind": "author", "key": "Brandon Sanderson"}
    assert out.folder_filter_path == Path("/lib/a")
    assert out.view["multiselect"] is True
    assert out.filter_text == "dune"
    assert out.selected_ids == {"b1", "b2"}
    assert out.open_book_id == "b1"


def test_stale_scope_and_book_and_selection_drop_to_defaults():
    snap = view_to_snapshot(
        scope={"kind": "author", "key": "Ghost Author"},
        folder_filter={"path": None},
        view={"mode": "library", "cwd": None, "multiselect": False, "group_by": "author"},
        filter_text="",
        selected_ids={"b1", "gone"},
        open_book_id="gone",
    )
    out = snapshot_to_view(
        snap, known_book_ids={"b1"}, known_authors=set(), known_series=set(),
    )
    assert out.scope == {"kind": "all", "key": None}
    assert out.selected_ids == {"b1"}
    assert out.open_book_id is None


def test_none_snapshot_yields_defaults():
    out = snapshot_to_view(None, known_book_ids=set(), known_authors=set(), known_series=set())
    assert out.scope == {"kind": "all", "key": None}
    assert out.selected_ids == set()
    assert out.open_book_id is None
    assert out.view["group_by"] == "author"
