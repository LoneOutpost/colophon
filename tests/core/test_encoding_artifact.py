from __future__ import annotations

from colophon.core.sequence_affix import strip_encoding_artifact


def test_trailing_ua_bitrate():
    assert strip_encoding_artifact("Sliding Scales UA 16@64.44m") == "Sliding Scales"


def test_parenthetical_unabr_bitrate_with_space_before_at():
    assert strip_encoding_artifact("Fevre Dream (Unabr @40k-44m)") == "Fevre Dream"


def test_bare_at_token():
    assert strip_encoding_artifact("Redemption Ark 46@64.44m") == "Redemption Ark"


def test_no_at_is_untouched():
    assert strip_encoding_artifact("The Fiery Cross") == "The Fiery Cross"
    assert strip_encoding_artifact("2001 - A Space Odyssey") == "2001 - A Space Odyssey"


def test_all_artifact_leaves_input_unchanged():
    # nothing but the artifact -> keep the input rather than blank the title
    assert strip_encoding_artifact("16@64.44m") == "16@64.44m"


def test_empty():
    assert strip_encoding_artifact("") == ""
