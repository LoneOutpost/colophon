import pytest

from colophon.core.folder_title import FolderTitle, parse_folder_title


@pytest.mark.parametrize("name,expected", [
    ("1981 - Cujo (read by Lorna Raver)", FolderTitle("Cujo", 1981, ["Lorna Raver"])),
    ("2001 - Dreamcatcher", FolderTitle("Dreamcatcher", 2001, [])),
    ("1990 - The Stand (Complete and Uncut Edition - read by Garrick Hagon)",
     FolderTitle("The Stand (Complete and Uncut Edition)", 1990, ["Garrick Hagon"])),
    ("2004 - Faithful (Nonfiction - read by Adam Grupper and Ron McLarty)",
     FolderTitle("Faithful (Nonfiction)", 2004, ["Adam Grupper", "Ron McLarty"])),
    ("Some Book", FolderTitle("Some Book", None, [])),
    ("1979 - The Long Walk (read by Kirby Heybourne)",
     FolderTitle("The Long Walk", 1979, ["Kirby Heybourne"])),
])
def test_parse_folder_title(name, expected):
    assert parse_folder_title(name) == expected


def test_series_paren_prefix_extracts_title_series_sequence():
    from colophon.core.folder_title import parse_folder_title
    r = parse_folder_title("(Old Man's War Book #6) The End of All Things")
    assert r.title == "The End of All Things"
    assert r.series == "Old Man's War"
    assert r.sequence == 6.0


def test_hamish_series_paren_prefix():
    from colophon.core.folder_title import parse_folder_title
    r = parse_folder_title("(A Hamish Macbeth Mystery Book #1) Death of a Gossip")
    assert r.title == "Death of a Gossip"
    assert r.series == "A Hamish Macbeth Mystery"
    assert r.sequence == 1.0


def test_series_paren_without_book_word():
    from colophon.core.folder_title import parse_folder_title
    r = parse_folder_title("(Stormlight #3) Words of Radiance")
    assert r.title == "Words of Radiance"
    assert r.series == "Stormlight"
    assert r.sequence == 3.0


def test_bare_book_number_prefix_sets_sequence_no_series():
    from colophon.core.folder_title import parse_folder_title
    r = parse_folder_title("#1 - John Dies at the End")
    assert r.title == "John Dies at the End"
    assert r.sequence == 1.0
    assert r.series is None


def test_no_prefix_and_edition_paren_unchanged():
    from colophon.core.folder_title import parse_folder_title
    assert parse_folder_title("Crome Yellow").title == "Crome Yellow"
    r = parse_folder_title("(Unabridged) Some Title")
    assert r.series is None and r.sequence is None  # no book number -> not a series prefix
    assert parse_folder_title("1981 - Cujo").year == 1981  # year still works


def test_series_paren_comma_export_style_strips_comma():
    from colophon.core.folder_title import parse_folder_title
    r = parse_folder_title("(The Expanse, #4) Cibola Burn")
    assert r.series == "The Expanse" and r.sequence == 4.0 and r.title == "Cibola Burn"
    r2 = parse_folder_title("(A Song of Ice and Fire, Book #1) A Game of Thrones")
    assert r2.series == "A Song of Ice and Fire" and r2.sequence == 1.0
