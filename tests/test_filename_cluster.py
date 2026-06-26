from colophon.core.filename_cluster import (
    _chunks, _spaced, _tokens, _is_num, _text_sig,
    _trailing_number, _strip_trailing_number,
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
