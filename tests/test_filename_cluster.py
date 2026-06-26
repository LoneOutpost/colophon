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
)


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
