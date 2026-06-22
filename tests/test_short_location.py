from pathlib import Path

from colophon.ui.workspace import _short_location


def test_short_location_uses_last_two_segments():
    assert _short_location(Path("/audiobooks/Sanderson/The Way of Kings")) == "Sanderson / The Way of Kings"


def test_short_location_single_segment():
    assert _short_location(Path("/Audiobooks")) == "Audiobooks"


def test_short_location_none():
    assert _short_location(None) == ""
