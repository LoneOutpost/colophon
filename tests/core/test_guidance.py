from colophon.core.guidance import (
    FixAction,
    finding_guidance,
    finding_phrase,
    finding_scope,
    review_guidance,
)
from colophon.core.models import Finding, FindingCode, FindingSeverity


def _f(code: FindingCode, detail: str = "x") -> Finding:
    return Finding(code=code, severity=FindingSeverity.WARN, detail=detail)


def test_every_finding_code_has_guidance():
    for code in FindingCode:
        g = finding_guidance(code)
        assert g.suggestion, f"{code} has no suggestion"
        assert isinstance(g.actions, tuple)


def test_corrupt_audio_points_at_reprobe_delete_not_acknowledge():
    g = finding_guidance(FindingCode.EMPTY_AUDIO)
    assert g.actions == (FixAction.REPROBE, FixAction.DELETE)
    assert FixAction.ACKNOWLEDGE not in g.actions  # blocking findings aren't acknowledgeable
    # the real fix is a good copy of the file.
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


def test_missing_tracks_offers_acknowledge():
    from colophon.core.guidance import FixAction, finding_guidance
    from colophon.core.models import FindingCode

    actions = finding_guidance(FindingCode.MISSING_TRACKS).actions
    assert actions == (FixAction.ACKNOWLEDGE,)


def test_metadata_conflict_scope_follows_which_check_raised_it():
    # Three unrelated checks share this code; only the author one is about a folder's claim.
    assert finding_scope(_f(FindingCode.METADATA_CONFLICT,
                            "author: tag 'A' vs folder 'B'")) == "author_folder"
    assert finding_scope(_f(FindingCode.METADATA_CONFLICT,
                            'metadata title "X" vs folder "Y"')) == "book"
    assert finding_scope(_f(FindingCode.METADATA_CONFLICT, 'folder "Dune" vs tag "Emma"')) == "book"
    # The folder-name check questions the author folder itself, so it groups there too.
    assert finding_scope(_f(FindingCode.METADATA_CONFLICT,
                            "folder name: folder 'Neal Stephenson' vs tags 'Top 100 Sci-Fi Books'")
                         ) == "author_folder"


def test_structure_findings_are_about_the_books_own_folder():
    for code in (FindingCode.MIXED_WORKS, FindingCode.MULTI_IN_AUTHOR,
                 FindingCode.MULTI_IN_UNDETERMINED, FindingCode.STRUCTURE_UNCLEAR):
        assert finding_scope(_f(code)) == "own_folder"


def test_everything_else_is_about_the_book():
    for code in (FindingCode.MIXED_QUALITY, FindingCode.MISSING_TRACKS,
                 FindingCode.EXTENSION_MISMATCH, FindingCode.DUP_FORMAT):
        assert finding_scope(_f(code)) == "book"


def test_finding_phrase_splits_metadata_conflict_by_which_check_raised_it():
    assert finding_phrase(_f(FindingCode.METADATA_CONFLICT,
                             "author: tag 'A' vs folder 'B'")) == "tags name a different author"
    assert finding_phrase(_f(FindingCode.METADATA_CONFLICT,
                             'metadata title "X" vs folder "Y"')) == "title disagrees with the folder"
    assert finding_phrase(_f(FindingCode.METADATA_CONFLICT,
                             'folder "Dune" vs tag "Emma"')) == "tags disagree with the folder"
    assert finding_phrase(_f(FindingCode.METADATA_CONFLICT,
                             "folder name: folder 'A' vs tags 'B'")
                          ) == "the folder's name disagrees with its books' tags"


def test_finding_phrase_falls_back_for_an_unlisted_code():
    assert finding_phrase(_f(FindingCode.LOOSE_IN_AUTHOR)) == "needs a look"
