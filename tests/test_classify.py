from pathlib import Path

from colophon.core.classify import FileFeatures, classify_folder_kind
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
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
