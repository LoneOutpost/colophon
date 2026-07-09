from pathlib import Path

from colophon.core.models import SourceFile
from colophon.core.part_order import resolve_part_order


def _sf(name: str) -> SourceFile:
    return SourceFile(path=Path(f"/x/{name}"), size=1, duration_seconds=1.0, ext=".mp3")


def test_uses_track_numbers_when_complete():
    files = [_sf("b.mp3"), _sf("a.mp3"), _sf("c.mp3")]
    tracks = [2, 1, 3]
    ordered = resolve_part_order(files, tracks)
    assert [f.path.name for f in ordered] == ["a.mp3", "b.mp3", "c.mp3"]


def test_falls_back_to_natural_filename_sort():
    files = [_sf("Part 10.mp3"), _sf("Part 2.mp3"), _sf("Part 1.mp3")]
    tracks = [None, None, None]
    ordered = resolve_part_order(files, tracks)
    assert [f.path.name for f in ordered] == ["Part 1.mp3", "Part 2.mp3", "Part 10.mp3"]


def test_gappy_track_numbers_fall_through_to_filename_sort():
    files = [_sf("Part 1.mp3"), _sf("Part 2.mp3")]
    tracks = [1, 5]  # not a complete 1..N -> ignore tracks, sort names
    ordered = resolve_part_order(files, tracks)
    assert [f.path.name for f in ordered] == ["Part 1.mp3", "Part 2.mp3"]


def test_ambiguous_identical_sort_keys_blocks():
    files = [_sf("track.mp3"), _sf("track.mp3")]
    assert resolve_part_order(files, [None, None]) is None


def test_single_file_returns_itself():
    files = [_sf("whole.mp3")]
    assert [f.path.name for f in resolve_part_order(files, [None])] == ["whole.mp3"]


# Extra edge-case tests


def test_duplicate_track_numbers_fall_through_to_filename_sort():
    # tracks [1, 1] is not a complete 1..2 set -> falls through to filename sort
    files = [_sf("Part 2.mp3"), _sf("Part 1.mp3")]
    tracks = [1, 1]
    ordered = resolve_part_order(files, tracks)
    assert [f.path.name for f in ordered] == ["Part 1.mp3", "Part 2.mp3"]


def test_mixed_some_none_tracks_fall_through_to_filename_sort():
    # Not every track is non-None -> falls through to filename sort
    files = [_sf("Part 3.mp3"), _sf("Part 1.mp3"), _sf("Part 2.mp3")]
    tracks = [3, None, 2]
    ordered = resolve_part_order(files, tracks)
    assert [f.path.name for f in ordered] == ["Part 1.mp3", "Part 2.mp3", "Part 3.mp3"]
