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


def test_literal_field_name_placeholders_are_junk():
    # A tagger that leaves the ID3 field NAME as its value ("Artist", "Album", ...) is not an identity
    # for any field, so the folder/filename wins instead (the R. A. Salvatore case).
    from colophon.core.metadata_quality import author_junk, title_junk
    for v in ["Artist", "Album", "Title", "Composer", "Album Artist", "artist", "ALBUM"]:
        assert is_placeholder_title(v) is True, v
        assert author_junk(v) == 1.0 and title_junk(v) == 1.0, v
    # real values are untouched
    for v in ["The Artist's Way", "Album of the Damned", "R. A. Salvatore"]:
        assert is_placeholder_title(v) is False, v


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


def test_is_structural_marker_true_cases():
    from colophon.core.metadata_quality import is_structural_marker as m
    for v in ["Chapter 01", "Chapter", "chapter", "Part 3", "Part", "Disc", "Disc 2", "CD 1",
              "Track 7", "Track 007 - Opening Theme", "Volume 1", "Vol 2", "", "   ", "15",
              "01 of 15", "Unknown Album", "Untitled",
              "R", "G", "A", "z"]:   # a single-char A-Z index-bucket folder is not an identity value
        assert m(v) is True, v


def test_is_structural_marker_false_cases():
    from colophon.core.metadata_quality import is_structural_marker as m
    for v in ["Journey to Sorrow's End", "The Bacta War", "Mistborn", "Elantris",
              "A Wizard of Earthsea", "Book One",
              "Part of the Pattern", "Chapter and Verse", "Discworld", "The Parting", "Chapters"]:
        assert m(v) is False, v


def test_is_junk_title_delegates_to_is_structural_marker():
    from colophon.core.metadata_quality import is_junk_title, is_structural_marker
    for v in ["Chapter 01", "", "15", "Track 3", "Real Title", "Mistborn"]:
        assert is_junk_title(v) == is_structural_marker(v), v


def test_is_title_shaped_author():
    assert is_title_shaped_author("The End of the Matter (Flinx 03)", "The End of the Matter") is True
    assert is_title_shaped_author("02 - Yendi", "Yendi") is True     # sequence affix
    assert is_title_shaped_author("Restoree", "Restoree") is True    # echoes the title
    assert is_title_shaped_author("Anne McCaffrey", "Restoree") is False
    assert is_title_shaped_author(None, None) is False
