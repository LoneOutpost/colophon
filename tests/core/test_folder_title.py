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
