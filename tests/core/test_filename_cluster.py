"""Tests for filename_cluster."""

from pathlib import Path

from colophon.core.filename_cluster import cluster
from colophon.core.models import ContentKind


def test_unspaced_number_compound_title_is_not_stripped():
    # "30-Day Heart Tune-Up" — the 30 is part of the title, not a track index
    cr = cluster([Path("/x/30-Day Heart Tune-Up.mp3")])
    assert cr.detected_works[0].label.startswith("30-Day")


def test_spaced_leading_number_is_still_dropped():
    cr = cluster([Path("/x/01 - Jhereg.mp3")])
    assert cr.detected_works[0].label == "Jhereg"


def test_intraword_hyphen_is_not_a_separator():
    # "X-Wing" is one token: the hyphen has no whitespace around it, so it must not split into
    # "X" | "Wing" and drop the "X". (Was parsing the series as "Wing".)
    w = cluster([Path("/x/Rogue Squadron (X-Wing 1).mp3")]).detected_works[0]
    assert w.label == "Rogue Squadron"
    assert w.series == "X-Wing"
    assert w.sequence == 1


def test_spaced_dash_still_separates():
    # A dash *with* whitespace around it is still a separator ("Author - Title").
    cr = cluster([Path("/x/Ann Leckie - Ancillary Justice (Imperial Radch 1).mp3")])
    w = cr.detected_works[0]
    assert w.series == "Imperial Radch"
    assert w.sequence == 1


def test_dot_numbered_parts_cluster_as_one_book():
    # "Series.01"/"Series.02": the dot sits on a letter->digit boundary, so it splits into
    # "Series"|"01" and the two files read as parts of one book (differ only by number).
    cr = cluster([Path("/x/The Silmarillion.01.mp3"), Path("/x/The Silmarillion.02.mp3")])
    assert cr.content_kind is ContentKind.SINGLE
    assert len(cr.detected_works) == 1


def test_initials_dot_is_not_split():
    # A dot between two letters (initials) must stay whole, not shred into "J"|"R"|"R".
    cr = cluster([Path("/x/J.R.R. Tolkien - The Hobbit.mp3")])
    assert cr.detected_works[0].label.startswith("J.R.R.")


def test_trailing_year_is_not_a_sequence():
    # A 4-digit year must not be read as a series sequence (matches sequence_affix numeric policy).
    work = cluster([Path("/x/Author - Neuromancer 1984.mp3")]).detected_works[0]
    assert work.sequence is None


def test_trailing_small_number_is_a_sequence():
    # A 1-3 digit trailing number is still a sequence, so real series detection is preserved.
    work = cluster([Path("/x/Author - Discworld 3.mp3")]).detected_works[0]
    assert work.series == "Discworld" and work.sequence == 3.0


def test_track_of_total_leading_clusters_as_one_book():
    from colophon.core.models import ContentKind
    stems = [f"/x/Crome Yellow/{i:02d}-12 - Chrome Yellow - Aldous Huxley.opus" for i in range(1, 13)]
    cr = cluster([Path(s) for s in stems])
    assert cr.content_kind is ContentKind.MULTI or len(cr.detected_works) == 1
    assert len(cr.detected_works) == 1
    assert cr.detected_works[0].label == "Chrome Yellow"


def test_track_of_total_trailing_clusters_as_one_book():
    stems = [f"/x/JDatE/David Wong - John Dies at the End {i:02d}-12.opus" for i in range(1, 13)]
    cr = cluster([Path(s) for s in stems])
    assert len(cr.detected_works) == 1


def test_lone_track_of_total_file_titles_by_text_not_number():
    w = cluster([Path("/x/Crome Yellow/01-12 - Chrome Yellow - Aldous Huxley.opus")]).detected_works[0]
    assert w.label == "Chrome Yellow"


def test_is_index_token_recognizes_compound_and_number():
    from colophon.core.filename_cluster import _is_index_token
    for t in ("01", "01-12", "1-40", "02-01", "01/12", "1x40"):
        assert _is_index_token(t), t
    # A pure number ("1984") is an index token as it always has been (_is_num); the compound
    # year-guard is that a 4-digit range is NOT read as a compound index.
    for t in ("chrome", "catch-22", "1984-1985", "30-day"):
        assert not _is_index_token(t), t


def test_distinct_titles_with_numbers_still_split():
    cr = cluster([Path("/x/Dune 01.mp3"), Path("/x/Foundation 01.mp3")])
    assert len(cr.detected_works) == 2
