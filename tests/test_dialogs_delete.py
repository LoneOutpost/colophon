from pathlib import Path

from colophon.ui.dialogs import delete_summary


def test_delete_summary_lists_files():
    lines = delete_summary([Path("/a/02.mp3"), Path("/a/05.mp3")], book_removed=False)
    assert "02.mp3" in lines and "05.mp3" in lines
    assert "permanently" in lines.lower()


def test_delete_summary_missing_book():
    lines = delete_summary([], book_removed=True)
    assert "record" in lines.lower() or "book" in lines.lower()
