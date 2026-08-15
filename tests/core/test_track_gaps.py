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


def test_n_of_m_total_catches_trailing_truncation():
    # files 1..3 of 5 with no embedded tracks: the index-only guess cannot see the truncation, but the
    # 'N of M' total does -> parts 4 and 5 are missing.
    stems = ["Book 1 of 5", "Book 2 of 5", "Book 3 of 5"]
    f = missing_tracks_finding([None, None, None], stems)
    assert f is not None and "4" in f.detail and "5" in f.detail


def test_n_of_m_total_complete_is_none():
    stems = [f"Book {i} of 4" for i in range(1, 5)]
    assert missing_tracks_finding([None] * 4, stems) is None


def test_n_of_m_range_span_partitions_and_reports_complete():
    # 'NN-NN of M' files each COVER a run of parts; 1-6 + 7-20 partitions 1..20, so the book is complete
    # (no false 'missing' for the un-listed interior parts).
    stems = ["Dragonflight 01-06 of 20", "Dragonflight 07-20 of 20"]
    assert missing_tracks_finding([None, None], stems) is None


def test_n_of_m_range_span_detects_a_gap_between_runs():
    # 1-6 then 12-20 of 20 leaves parts 7..11 uncovered.
    stems = ["Book 01-06 of 20", "Book 12-20 of 20"]
    f = missing_tracks_finding([None, None], stems)
    assert f is not None and "7" in f.detail and "11" in f.detail


def test_overlapping_coverage_defers():
    # two files both claiming '01-10 of 20' overlap -> not a clean partition -> defer, no finding.
    stems = ["Book 01-10 of 20", "Book 01-10 of 20"]
    assert missing_tracks_finding([None, None], stems) is None


def test_multi_track_per_disc_does_not_false_flag():
    # every file reads 'Disc 01 of 11' (duplicate index) -> the total-based check defers, no finding.
    stems = [f"Book Disc 01 of 11 - Track {i:02d}" for i in range(1, 6)]
    assert missing_tracks_finding([None] * 5, stems) is None
