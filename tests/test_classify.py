from pathlib import Path

from colophon.core.classify import FileFeatures, classify_folder_kind, content_kind_for, group_works
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import ContentKind as CK
from colophon.core.models import EmbeddedTags, FolderKind


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
