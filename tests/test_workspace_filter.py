from pathlib import Path

from colophon.core.models import BookUnit
from colophon.ui.dialogs import _fmt_series_label
from colophon.ui.workspace import _editor_text, book_haystack


def test_fmt_series_label_drops_trailing_zero():
    assert _fmt_series_label("Stormlight", 1.0) == "Stormlight #1"


def test_fmt_series_label_keeps_fractional_sequence():
    assert _fmt_series_label("Stormlight", 2.5) == "Stormlight #2.5"


def test_fmt_series_label_no_sequence():
    assert _fmt_series_label("Stormlight", None) == "Stormlight"


def test_fmt_series_label_empty_name_is_blank():
    assert _fmt_series_label(None, 1.0) == ""
    assert _fmt_series_label("", 1.0) == ""


def test_book_haystack_includes_genres_and_tags():
    b = BookUnit.new(source_folder=Path("/x"))
    b.title = "The Hobbit"
    b.authors = ["J.R.R. Tolkien"]
    b.genres = ["Fantasy"]
    b.tags = ["to-relisten"]
    hay = book_haystack(b)
    assert "fantasy" in hay
    assert "to-relisten" in hay
    assert "the hobbit" in hay
    assert "tolkien" in hay


def test_triage_mode_removed_and_needs_work_facet_absent():
    import inspect

    from colophon.core.triage import FACET_DEFAULTS
    from colophon.ui import workspace

    assert not hasattr(workspace, "_opening_mode")
    assert "needs_work" not in FACET_DEFAULTS
    src = inspect.getsource(workspace)
    assert "Triage" not in src
    assert '"triage"' not in src


def test_editor_text_joins_list_values():
    class _W:
        def __init__(self, value):
            self.value = value
    assert _editor_text(_W(["Fantasy", "Epic"])) == "Fantasy; Epic"
    assert _editor_text(_W(["  ", "Epic"])) == "Epic"
    assert _editor_text(_W("plain")) == "plain"
    assert _editor_text(_W(None)) == ""


def test_state_filter_options_cover_every_book_state():
    # A missing state silently hides those books from the State filter (Organized/Encoded/Skipped
    # were absent). Keep the filter's options exhaustive against the enum.
    from colophon.core.models import BookState
    from colophon.ui.workspace import _STATE_FILTER_OPTIONS

    assert set(_STATE_FILTER_OPTIONS) == {s.value for s in BookState}


def test_the_uncertain_state_reads_unsure_not_needs_review():
    from colophon.core.models import BookState
    from colophon.ui.workspace import _STATE_BADGE, _STATE_FILTER_OPTIONS, _STATUS_BADGES
    assert _STATE_BADGE[BookState.NEEDS_REVIEW][0] == "Unsure"
    assert _STATE_FILTER_OPTIONS[BookState.NEEDS_REVIEW.value] == "Unsure"
    assert ("needs_review", "Unsure", "warning") in _STATUS_BADGES
