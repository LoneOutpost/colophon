from colophon.ui.workspace import _move_focus


def test_move_focus_empty_list_is_none():
    assert _move_focus([], None, 1) is None
    assert _move_focus([], "a", 1) is None


def test_move_focus_from_none_goes_to_first_or_last():
    assert _move_focus(["a", "b", "c"], None, 1) == "a"   # Down from nothing -> first
    assert _move_focus(["a", "b", "c"], None, -1) == "c"  # Up from nothing -> last


def test_move_focus_steps_within_list():
    assert _move_focus(["a", "b", "c"], "a", 1) == "b"
    assert _move_focus(["a", "b", "c"], "b", 1) == "c"
    assert _move_focus(["a", "b", "c"], "b", -1) == "a"


def test_move_focus_clamps_at_ends():
    assert _move_focus(["a", "b", "c"], "c", 1) == "c"   # already last
    assert _move_focus(["a", "b", "c"], "a", -1) == "a"  # already first


def test_move_focus_stale_current_resets_to_edge():
    # focused id no longer present (e.g. after a filter change)
    assert _move_focus(["a", "b", "c"], "gone", 1) == "a"
    assert _move_focus(["a", "b", "c"], "gone", -1) == "c"
