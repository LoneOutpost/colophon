from pathlib import Path

from colophon.core.classify import (
    FileFeatures,
    classify,
    classify_folder_kind,
    content_kind_for,
    group_works,
)
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import ContentKind, EmbeddedTags, FindingSeverity, FolderKind
from colophon.core.models import ContentKind as CK
from colophon.core.models import FindingCode as FC


def _feat(path: str, **tag_kwargs) -> FileFeatures:
    return FileFeatures(
        path=Path(path), ext=Path(path).suffix.lstrip("."),
        duration_seconds=3600.0, tags=EmbeddedTags(**tag_kwargs),
    )


TEMPLATE = compile_template("$Title")
SCHEME = parse_scheme("$Author/$Title")


def test_folder_name_matching_artist_is_author():
    feats = [_feat("/lib/Brandon Sanderson/Legion.mp3", artist="Brandon Sanderson")]
    kind, signals = classify_folder_kind(
        Path("/lib/Brandon Sanderson"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.AUTHOR
    assert signals and signals[0].points > 0


def test_folder_name_matching_album_is_title():
    feats = [_feat("/lib/The Way of Kings/part1.mp3", album="The Way of Kings")]
    kind, _ = classify_folder_kind(
        Path("/lib/The Way of Kings"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.TITLE


def test_folder_name_matching_file_stem_is_title():
    feats = [_feat("/lib/Legion/Legion.mp3")]  # no tags at all
    kind, _ = classify_folder_kind(
        Path("/lib/Legion"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.TITLE


def test_no_signal_is_undetermined():
    feats = [_feat("/lib/misc/track01.mp3")]
    kind, signals = classify_folder_kind(
        Path("/lib/misc"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.UNDETERMINED
    assert signals == []


def test_shared_album_groups_into_one_work():
    feats = [
        _feat("/a/d/01.mp3", album="The Way of Kings"),
        _feat("/a/d/02.mp3", album="The Way of Kings"),
    ]
    works, signals = group_works(feats)
    assert len(works) == 1
    assert len(works[0].files) == 2
    assert content_kind_for(works, signals) is CK.SINGLE


def test_distinct_albums_are_multi():
    feats = [
        _feat("/a/d/Legion.mp3", album="Legion", artist="Brandon Sanderson"),
        _feat("/a/d/Elantris.mp3", album="Elantris", artist="Brandon Sanderson"),
    ]
    works, signals = group_works(feats)
    assert len(works) == 2
    assert content_kind_for(works, signals) is CK.MULTI


def test_numbered_files_no_tags_are_one_sequence():
    feats = [_feat("/a/d/track 1.mp3"), _feat("/a/d/track 2.mp3"), _feat("/a/d/track 3.mp3")]
    works, signals = group_works(feats)
    assert len(works) == 1
    assert content_kind_for(works, signals) is CK.SINGLE


def test_untagged_unsequenced_files_are_unknown():
    feats = [_feat("/a/d/alpha.mp3"), _feat("/a/d/bravo.mp3")]
    works, signals = group_works(feats)
    assert len(works) == 2
    assert content_kind_for(works, signals) is CK.UNKNOWN


def test_asin_beats_album_for_grouping():
    feats = [
        _feat("/a/d/a.mp3", asin="B001", album="X"),
        _feat("/a/d/b.mp3", asin="B001", album="Y"),
    ]
    works, _ = group_works(feats)
    assert len(works) == 1


def _classify(folder, root, feats):
    return classify(Path(folder), Path(root), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME)


def test_multi_in_author_folder_flags_actionable():
    feats = [
        _feat("/lib/Brandon Sanderson/Legion.mp3", album="Legion", artist="Brandon Sanderson"),
        _feat("/lib/Brandon Sanderson/Elantris.mp3", album="Elantris", artist="Brandon Sanderson"),
    ]
    r = _classify("/lib/Brandon Sanderson", "/lib", feats)
    assert r.content_kind is ContentKind.MULTI
    assert r.folder_kind is FolderKind.AUTHOR
    assert any(f.code is FC.MULTI_IN_AUTHOR for f in r.findings)


def test_single_loose_in_author_folder():
    feats = [_feat("/lib/Brandon Sanderson/Legion.mp3", artist="Brandon Sanderson")]
    r = _classify("/lib/Brandon Sanderson", "/lib", feats)
    assert r.content_kind is ContentKind.SINGLE
    assert any(f.code is FC.LOOSE_IN_AUTHOR for f in r.findings)


def test_format_variants_in_title_folder_are_info():
    feats = [
        _feat("/lib/Legion/Legion.mp3", album="Legion"),
        _feat("/lib/Legion/Legion.m4b", album="Legion"),
    ]
    r = _classify("/lib/Legion", "/lib", feats)
    assert r.content_kind is ContentKind.SINGLE
    assert r.folder_kind is FolderKind.TITLE
    dup = [f for f in r.findings if f.code is FC.DUP_FORMAT]
    assert dup and dup[0].severity is FindingSeverity.INFO


def test_mixed_works_in_title_folder_is_error():
    feats = [
        _feat("/lib/Legion/Legion.mp3", album="Legion"),
        _feat("/lib/Legion/Elantris.mp3", album="Elantris"),
    ]
    r = _classify("/lib/Legion", "/lib", feats)
    assert r.folder_kind is FolderKind.TITLE
    err = [f for f in r.findings if f.code is FC.MIXED_WORKS]
    assert err and err[0].severity is FindingSeverity.ERROR


def test_clean_single_title_has_no_findings():
    feats = [_feat("/lib/Legion/Legion.mp3", album="Legion")]
    r = _classify("/lib/Legion", "/lib", feats)
    assert r.findings == []
