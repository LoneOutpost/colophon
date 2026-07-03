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
