import pytest

from colophon.core.models import FindingCode
from colophon.core.track_gaps import index_sequence, missing_tracks_finding, sequence_gaps


@pytest.mark.parametrize("indices,expected", [
    ([1, 2, 4, 5], [3]),          # interior hole
    ([3, 4, 5], [1, 2]),          # bounded leading edge
    ([2, 3, 4, 5], [1]),          # leading edge of one
    ([5, 6, 7, 8], []),           # lo too high -> no leading inference
    ([51, 52, 53, 54], []),       # continuation volume -> nothing
    ([1, 50], []),                # too few files
    ([1, 2, 50], []),             # sparse -> density gate
    ([1, 2, 3], []),              # complete
])
def test_sequence_gaps(indices, expected):
    assert sequence_gaps(indices) == expected


def test_index_sequence_uses_distinct_tags():
    assert index_sequence([1, 2, 4], ["a", "b", "c"]) == [1, 2, 4]


def test_index_sequence_falls_back_to_filenames_when_tags_missing():
    assert index_sequence([None, None, None], ["01", "02", "04"]) == [1, 2, 4]


def test_index_sequence_none_when_tags_duplicate_and_names_unusable():
    assert index_sequence([1, 1, 2], ["cd01-01", "cd01-02", "cd02-01"]) is None


def test_index_sequence_none_when_a_file_is_unparseable():
    assert index_sequence([None, None, None], ["01", "02", "Interview"]) is None


def test_missing_tracks_finding_flags_a_hole():
    f = missing_tracks_finding([1, 2, 4], ["01", "02", "04"])
    assert f is not None and f.code is FindingCode.MISSING_TRACKS
    assert "3" in f.detail


def test_missing_tracks_finding_none_when_complete():
    assert missing_tracks_finding([1, 2, 3], ["01", "02", "03"]) is None
