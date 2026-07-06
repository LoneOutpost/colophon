from colophon.core.guidance import FixAction, finding_guidance, review_guidance
from colophon.core.models import FindingCode


def test_every_finding_code_has_guidance():
    for code in FindingCode:
        g = finding_guidance(code)
        assert g.suggestion, f"{code} has no suggestion"
        assert isinstance(g.actions, tuple)


def test_corrupt_audio_points_at_acquire_and_reprobe_not_acknowledge():
    g = finding_guidance(FindingCode.EMPTY_AUDIO)
    assert g.actions == (FixAction.ACQUIRE, FixAction.REPROBE)
    assert FixAction.ACKNOWLEDGE not in g.actions  # blocking findings aren't acknowledgeable
    # Acquire is a convenience, not the mandate: the real fix leads.
    assert "replace it" in g.suggestion.lower()


def test_mixed_works_points_at_organize():
    for code in (FindingCode.MIXED_WORKS, FindingCode.MULTI_IN_AUTHOR,
                 FindingCode.MULTI_IN_UNDETERMINED):
        assert finding_guidance(code).actions == (FixAction.ORGANIZE,)


def test_duplicates_offer_files_and_acknowledge():
    for code in (FindingCode.DUP_FORMAT, FindingCode.DUP_EDITION):
        assert finding_guidance(code).actions == (FixAction.FILES, FixAction.ACKNOWLEDGE)


def test_structure_unclear_is_acknowledge_only():
    assert finding_guidance(FindingCode.STRUCTURE_UNCLEAR).actions == (FixAction.ACKNOWLEDGE,)


def test_review_guidance_points_at_matches():
    assert review_guidance().actions == (FixAction.MATCHES,)
