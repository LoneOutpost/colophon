from colophon.core.detail_pane import DetailPane, Pane


def _pane(*selected: str) -> tuple[DetailPane, set[str]]:
    """A pane sharing a live selection set, as the workspace wires it."""
    ids = set(selected)
    return DetailPane(ids), ids


def test_nothing_selected_is_the_empty_state():
    pane, _ids = _pane()
    assert pane.current() == Pane("empty")


def test_one_selected_book_shows_that_book():
    pane, _ids = _pane("a")
    assert pane.current() == Pane("single", "a")


def test_two_or_more_selected_is_a_bulk_edit():
    pane, _ids = _pane("a", "b")
    assert pane.current() == Pane("bulk")


def test_opening_a_book_outranks_the_selection():
    # A row click, a ?open= deep link, or an action on one book: the user asked for that book,
    # so it wins over a bulk selection that happens to be live.
    pane, _ids = _pane("a", "b", "c")
    assert pane.open("b") == Pane("single", "b")
    assert pane.current() == Pane("single", "b")


def test_a_selection_change_retires_the_explicit_open():
    pane, ids = _pane("a")
    pane.open("a")
    ids.update({"b", "c"})
    assert pane.selection_changed() == Pane("bulk")


def test_opening_none_clears_to_the_empty_state():
    pane, ids = _pane("a", "b")
    pane.open("a")
    ids.clear()
    assert pane.open(None) == Pane("empty")


def test_reading_the_live_set_means_no_stale_answer():
    # The workspace mutates selected_ids in place from dozens of closures; the pane must read it,
    # not hold a copy taken when it was built.
    pane, ids = _pane()
    assert pane.current() == Pane("empty")
    ids.add("a")
    assert pane.current() == Pane("single", "a")
    ids.add("b")
    assert pane.current() == Pane("bulk")


def test_an_open_book_survives_a_repaint_that_changed_nothing():
    # repaint() re-renders panes after a mutation. It must not stomp a book the user deliberately
    # opened just because a bulk selection is still live behind it.
    pane, _ids = _pane("a", "b")
    pane.open("a")
    assert pane.current() == Pane("single", "a")


def test_bulk_is_recognisable_for_a_repaint_refresh():
    pane, _ids = _pane("a", "b")
    assert pane.current().is_bulk
    pane.open("a")
    assert not pane.current().is_bulk
