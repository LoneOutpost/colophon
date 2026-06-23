from pathlib import Path

from colophon.core.models import BookUnit
from colophon.ui.workspace import _editor_text, _fmt_series_label, book_haystack


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


def test_editor_text_joins_list_values():
    class _W:
        def __init__(self, value):
            self.value = value
    assert _editor_text(_W(["Fantasy", "Epic"])) == "Fantasy; Epic"
    assert _editor_text(_W(["  ", "Epic"])) == "Epic"
    assert _editor_text(_W("plain")) == "plain"
    assert _editor_text(_W(None)) == ""
