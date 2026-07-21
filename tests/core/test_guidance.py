from colophon.core.guidance import FixAction, finding_guidance, review_guidance
from colophon.core.models import FindingCode


def test_every_finding_code_has_guidance():
    for code in FindingCode:
        g = finding_guidance(code)
        assert g.suggestion, f"{code} has no suggestion"
        assert isinstance(g.actions, tuple)


def test_corrupt_audio_points_at_acquire_reprobe_delete_not_acknowledge():
    g = finding_guidance(FindingCode.EMPTY_AUDIO)
    assert g.actions == (FixAction.ACQUIRE, FixAction.REPROBE, FixAction.DELETE)
    assert FixAction.ACKNOWLEDGE not in g.actions  # blocking findings aren't acknowledgeable
    # Acquire is a convenience, not the mandate: the real fix leads.
    assert "replace it" in g.suggestion.lower()


def test_mixed_works_points_at_organize_and_can_be_dismissed():
    # Organize is the remedy, but the user can also dismiss the note when the split already
    # looks right (e.g. a franchise folder whose books are each their own file).
    for code in (FindingCode.MIXED_WORKS, FindingCode.MULTI_IN_AUTHOR,
                 FindingCode.MULTI_IN_UNDETERMINED):
        assert finding_guidance(code).actions == (FixAction.ORGANIZE, FixAction.ACKNOWLEDGE)


def test_duplicates_offer_files_and_acknowledge():
    for code in (FindingCode.DUP_FORMAT, FindingCode.DUP_EDITION):
        assert finding_guidance(code).actions == (FixAction.FILES, FixAction.ACKNOWLEDGE)


def test_structure_unclear_is_acknowledge_only():
    assert finding_guidance(FindingCode.STRUCTURE_UNCLEAR).actions == (FixAction.ACKNOWLEDGE,)


def test_review_guidance_points_at_matches():
    assert review_guidance().actions == (FixAction.MATCHES,)


def test_mixed_quality_has_guidance():
    g = finding_guidance(FindingCode.MIXED_QUALITY)
    assert g.suggestion and len(g.actions) >= 1


def test_empty_audio_offers_delete_not_acknowledge():
    from colophon.core.guidance import FixAction, finding_guidance
    from colophon.core.models import FindingCode

    actions = finding_guidance(FindingCode.EMPTY_AUDIO).actions
    assert FixAction.DELETE in actions
    assert FixAction.ACKNOWLEDGE not in actions


def test_advisory_findings_offer_acknowledge():
    from colophon.core.guidance import FixAction, finding_guidance
    from colophon.core.models import FindingCode

    for code in (FindingCode.MULTI_IN_AUTHOR, FindingCode.MIXED_WORKS,
                 FindingCode.DUP_FORMAT, FindingCode.MIXED_QUALITY):
        assert FixAction.ACKNOWLEDGE in finding_guidance(code).actions
