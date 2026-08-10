"""metadata_quality: shared detectors for junk metadata values (placeholder/index titles, authors
that are really a title)."""
from colophon.core.metadata_quality import (
    is_index_title,
    is_junk_title,
    is_placeholder_title,
    is_title_shaped_author,
)


def test_is_placeholder_title():
    assert is_placeholder_title("Track 3") is True
    assert is_placeholder_title("Unknown Album") is True
    assert is_placeholder_title("Untitled") is True
    assert is_placeholder_title("Orphan Star") is False
    assert is_placeholder_title(None) is False


def test_is_index_title():
    assert is_index_title("15") is True
    assert is_index_title("01 of 15") is True          # NN of MM (the gap being closed)
    assert is_index_title("1984") is True              # bare 4-digit (matches _BARE_NUM_TITLE today)
    assert is_index_title("Fahrenheit 451") is False
    assert is_index_title(None) is False


def test_is_junk_title():
    assert is_junk_title("Track 001 - Opening Theme") is True   # leading track marker
    assert is_junk_title("01 of 15") is True                    # index
    assert is_junk_title("Disc 2") is True
    assert is_junk_title("") is True                            # empty is not a real title
    assert is_junk_title(None) is True
    assert is_junk_title("The Way of Kings") is False


def test_is_title_shaped_author():
    assert is_title_shaped_author("The End of the Matter (Flinx 03)", "The End of the Matter") is True
    assert is_title_shaped_author("02 - Yendi", "Yendi") is True     # sequence affix
    assert is_title_shaped_author("Restoree", "Restoree") is True    # echoes the title
    assert is_title_shaped_author("Anne McCaffrey", "Restoree") is False
    assert is_title_shaped_author(None, None) is False
