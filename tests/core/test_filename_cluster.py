"""Tests for filename_cluster."""

from pathlib import Path

from colophon.core.filename_cluster import cluster


def test_unspaced_number_compound_title_is_not_stripped():
    # "30-Day Heart Tune-Up" — the 30 is part of the title, not a track index
    cr = cluster([Path("/x/30-Day Heart Tune-Up.mp3")])
    assert cr.detected_works[0].label.startswith("30-Day")


def test_spaced_leading_number_is_still_dropped():
    cr = cluster([Path("/x/01 - Jhereg.mp3")])
    assert cr.detected_works[0].label == "Jhereg"
