from pathlib import Path

from colophon.core.filename_cluster import (
    DIFFERENT_TEXT,
    IDENTICAL,
    MATCH_EXCEPT_NUMBER,
    _chunks,
    _is_num,
    _multi_work,
    _parts_work,
    _relationship,
    _series_and_seq,
    _spaced,
    _strip_trailing_number,
    _text_sig,
    _tokens,
    _trailing_number,
    cluster,
)
from colophon.core.models import ContentKind


def test_chunks_split_on_separators():
    assert _chunks("Father of Lies (Darkly Disturbing Trilogy 1)") == \
        ["Father of Lies", "Darkly Disturbing Trilogy 1"]
    assert _chunks("girlblue-01-cd01-01") == ["girlblue", "01", "cd01", "01"]
    assert _chunks("7thSigmaUnabridgedPart1_ep6") == ["7thSigmaUnabridgedPart1", "ep6"]


def test_spaced_splits_camel_and_letter_then_digit_only():
    assert _spaced("7thSigmaUnabridgedPart1") == "7th Sigma Unabridged Part 1"  # ordinal "7th" intact
    assert _spaced("ep6") == "ep 6"
    assert _spaced("Olento Research, 1") == "Olento Research 1"  # comma -> space


def test_tokens_lowercased_words_and_numbers():
    assert _tokens("Darkly Disturbing Trilogy 1") == ["darkly", "disturbing", "trilogy", "1"]


def test_is_num_handles_int_and_decimal():
    assert _is_num("12") and _is_num("0.5")
    assert not _is_num("7th") and not _is_num("cd01")


def test_text_sig_drops_number_tokens():
    assert _text_sig(["darkly", "disturbing", "trilogy", "1"]) == ("darkly", "disturbing", "trilogy")
    assert _text_sig(["7th", "sigma", "part", "1"]) == ("7th", "sigma", "part")  # 7th kept (not a number)


def test_trailing_number_helpers():
    assert _trailing_number("Darkly Disturbing Trilogy 1") == 1.0
    assert _trailing_number("Duchess of Love Trilogy 0.5") == 0.5
    assert _trailing_number("Owlmen") is None
    assert _strip_trailing_number("Darkly Disturbing Trilogy 1") == "Darkly Disturbing Trilogy"
    assert _strip_trailing_number("Owlmen") == "Owlmen"


def test_relationship_identical():
    assert _relationship([["ep", "6"], ["ep", "6"]]) == IDENTICAL


def test_relationship_match_except_number():
    assert _relationship([["trilogy", "1"], ["trilogy", "2"]]) == MATCH_EXCEPT_NUMBER
    # constant non-varying number ("7th"->'7th' is text, not number) still matches
    assert _relationship([["7th", "sigma", "part", "1"], ["7th", "sigma", "part", "2"]]) \
        == MATCH_EXCEPT_NUMBER


def test_relationship_different_text():
    assert _relationship([["father", "of", "lies"], ["tanners", "dell"]]) == DIFFERENT_TEXT


def test_series_and_seq_from_chunks():
    assert _series_and_seq(["Darkly Disturbing Trilogy 1"]) == ("Darkly Disturbing Trilogy", 1.0)
    assert _series_and_seq(["Olento Research, 1"]) == ("Olento Research", 1.0)
    assert _series_and_seq([]) == (None, None)


def test_multi_work_extracts_title_series_seq():
    w = _multi_work(Path("/a/Father of Lies (Darkly Disturbing Trilogy 1).mp3"),
                    ["Father of Lies", "Darkly Disturbing Trilogy 1"])
    assert w.label == "Father of Lies"
    assert w.series == "Darkly Disturbing Trilogy" and w.sequence == 1.0
    assert w.files == [Path("/a/Father of Lies (Darkly Disturbing Trilogy 1).mp3")]


def test_parts_work_strips_varying_number_from_title():
    files = [Path("/a/7thSigmaUnabridgedPart1_ep6.mp3"), Path("/a/7thSigmaUnabridgedPart2_ep6.mp3")]
    per_file = [["7thSigmaUnabridgedPart1", "ep6"], ["7thSigmaUnabridgedPart2", "ep6"]]
    w = _parts_work(files, per_file)
    assert w.label == "7th Sigma Unabridged Part"
    assert w.files == files and w.series is None


def _paths(*names):
    return [Path("/lib/Author") / n for n in names]


def test_cluster_single_file_is_single():
    r = cluster(_paths("Hillary.mp3"))
    assert r.content_kind is ContentKind.SINGLE
    assert len(r.detected_works) == 1 and r.detected_works[0].label == "Hillary"


def test_cluster_parts_of_one_book_is_single():
    r = cluster(_paths("7thSigmaUnabridgedPart1_ep6.mp3", "7thSigmaUnabridgedPart2_ep6.mp3"))
    assert r.content_kind is ContentKind.SINGLE
    assert len(r.detected_works) == 1
    assert r.detected_works[0].label == "7th Sigma Unabridged Part"


def test_cluster_cd_tracks_are_single():
    r = cluster(_paths(*(f"girlblue-{i:02d}-cd0{1 + i // 6}-{i % 6 + 1:02d}.mp3" for i in range(1, 13))))
    assert r.content_kind is ContentKind.SINGLE
    assert r.detected_works[0].label == "girlblue"


def test_cluster_series_of_separate_books_is_multi():
    r = cluster(_paths("Father of Lies (Darkly Disturbing Trilogy 1).mp3",
                       "Tanners Dell (Darkly Disturbing Trilogy 2).mp3"))
    assert r.content_kind is ContentKind.MULTI
    assert {w.label for w in r.detected_works} == {"Father of Lies", "Tanners Dell"}
    assert all(w.series == "Darkly Disturbing Trilogy" for w in r.detected_works)
    assert {w.sequence for w in r.detected_works} == {1.0, 2.0}


def test_cluster_separate_books_no_series_is_multi():
    r = cluster(_paths("Pearl Harbor Christmas.mp3", "eleven Days in December.mp3"))
    assert r.content_kind is ContentKind.MULTI
    assert all(w.series is None for w in r.detected_works)


def test_cluster_ragged_distinct_titles_is_multi():
    r = cluster(_paths("Father of Lies (Darkly Disturbing Trilogy 1).mp3",
                       "Owlmen.mp3",
                       "Tanners Dell (Darkly Disturbing Trilogy 2).mp3"))
    assert r.content_kind is ContentKind.MULTI
    assert len(r.detected_works) == 3
    # series links the two that carry it
    assert sum(w.series == "Darkly Disturbing Trilogy" for w in r.detected_works) == 2


def test_cluster_ragged_trailing_title_is_not_single():
    # The distinguishing title is in a trailing chunk past the shorter sibling.
    r = cluster(_paths("Series-1.mp3", "Series-2-Other Title.mp3"))
    assert r.content_kind is not ContentKind.SINGLE  # must not silently merge two books


def test_title_chunks_drops_leading_number_chunks():
    from colophon.core.filename_cluster import _title_chunks
    assert _title_chunks(["1", "The Gunslinger"]) == ["The Gunslinger"]
    assert _title_chunks(["Alpha Wolf", "Olento Research 1"]) == ["Alpha Wolf", "Olento Research 1"]
    assert _title_chunks(["01"]) == ["01"]            # keeps the last chunk (degenerate)
    assert _title_chunks([]) == []


def test_single_file_label_uses_leading_text_chunk():
    r = cluster([Path("1_ The Gunslinger.mp3")])
    assert r.content_kind is ContentKind.SINGLE
    assert r.detected_works[0].label == "The Gunslinger"


def test_single_file_leading_text_label_unchanged():
    r = cluster([Path("Alpha Wolf (Olento Research 1).mp3")])
    assert r.detected_works[0].label == "Alpha Wolf"


def test_single_file_lone_number_label_degenerate():
    r = cluster([Path("01.mp3")])
    assert r.detected_works[0].label == "01"
