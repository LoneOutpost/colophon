from __future__ import annotations

import pytest

from colophon.core.sequence_affix import clean_title


@pytest.mark.parametrize("legit", [
    "Slaughterhouse 5", "Catch 22", "Fahrenheit 451", "2001 - A Space Odyssey", "1984", "2312",
    "The Land: Forging: Chaos Seeds, Book 2", "Children of Time",
    "God's Eye: Awakening: A Labyrinth World LitRPG Novel",
])
def test_legitimate_titles_are_untouched(legit):
    assert clean_title(legit) == legit


@pytest.mark.parametrize("dirty,expected", [
    ("Acorna - 01", "Acorna"),
    ("Rogue Angel 01", "Rogue Angel"),
    ("ABC-2 output", "ABC"),
    ("Coyote Horizon - Unb-001", "Coyote Horizon"),
    ("Hotwire 3-04", "Hotwire"),
    ("01_28_The_Coming_of_the_Ship", "The Coming of the Ship"),
    ("Children of Time (Unabridged)", "Children of Time"),
    ("TheHive", "The Hive"),
    ("StarBridge", "Star Bridge"),
    ("01 of 01 Murder on the Orient Express", "Murder on the Orient Express"),
    ("Innocence in Death Cd08", "Innocence in Death"),
    ("Cold Mountain Part 2", "Cold Mountain"),
])
def test_dirty_titles_are_cleaned(dirty, expected):
    assert clean_title(dirty) == expected


def test_all_junk_title_is_left_not_blanked():
    # nothing salvageable -> keep the input rather than return empty (MATCH/flag territory)
    assert clean_title("01_02_03") == "01_02_03"


def test_empty():
    assert clean_title("") == ""


@pytest.mark.parametrize("dirty,expected", [
    ("[Carrier #02] - Viper Strike", "Viper Strike"),
    ("[Carrier #03]-Armageddon Mode", "Armageddon Mode"),
    ("[Deathlands #00]{R-Rated} - Encounter", "Encounter"),
])
def test_leading_series_reference_is_stripped(dirty, expected):
    assert clean_title(dirty) == expected


def test_whole_series_reference_is_left_for_the_folder_title():
    # nothing but a series ref -> clean_title keeps it (title_junk flags it so the folder title wins)
    assert clean_title("[Pip & Flinx #12]") == "[Pip & Flinx #12]"


def test_parenthetical_subtitle_is_not_a_series_ref():
    assert clean_title("We Are Legion (We Are Bob)") == "We Are Legion (We Are Bob)"
