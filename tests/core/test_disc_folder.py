from __future__ import annotations

import pytest

from colophon.core.disc_folder import disc_number


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # bare disc names — 4 of the 6 real cases
        ("CD1", 1),
        ("CD01", 1),
        ("CD10", 10),
        ("cd1", 1),
        ("Disc 3", 3),
        ("Disk4", 4),
        ("DVD2", 2),
        ("CD_07", 7),
        ("CD-07", 7),
        # title-carrying disc names — the other 2 real cases
        ("Shadow Warriors - Disk1", 1),
        ("PT Deutermann - The Cat Dancers - cd01", 1),
        ("Some Book (CD 2)", 2),
        ("Some Book [Disc 3]", 3),
        ("Some Book - CD 4 of 12", 4),
    ],
)
def test_a_disc_folder_yields_its_number(name, expected):
    assert disc_number(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # Real folders in this library that a naive '*disc*' match would swallow.
        "Discord's Apple",
        "Lords of Discipline",
        "Discworld",
        # The token must be a whole name or a trailing segment, never mid-name.
        "CD1 Extras and Interviews",
        "Disc Jockey Stories",
        # No number, or an implausible one.
        "CD",
        "Disc",
        "CD1234",
        # An ordinary book folder.
        "Dune",
        "Frank Herbert - Dune",
    ],
)
def test_a_non_disc_folder_yields_none(name):
    assert disc_number(name) is None
