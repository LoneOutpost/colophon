from pathlib import Path

from colophon.core.models import BookUnit
from colophon.ui.workspace import _editor_text, book_haystack


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
