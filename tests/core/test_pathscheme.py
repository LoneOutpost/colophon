from pathlib import Path

import pytest

from colophon.adapters.lazylibrarian import PathPatterns
from colophon.core.models import BookUnit, SeriesRef, SourceFile
from colophon.core.pathscheme import (
    build_reorg_targets,
    build_target_path,
    ensure_part_placeholder,
    expand_pattern,
    sanitize_segment,
)


def _book() -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "The Way of Kings"
    b.authors = ["Brandon Sanderson"]
    b.series = [SeriesRef(name="Stormlight Archive", sequence=1.0)]
    b.publish_year = 2010
    return b


def test_expand_common_tokens():
    b = _book()
    assert expand_pattern("$Author/$Series/$Title", b) == "Brandon Sanderson/Stormlight Archive/The Way of Kings"
    assert expand_pattern("$PubYear - $Title", b) == "2010 - The Way of Kings"


def test_padnum_zero_pads_sernum():
    b = _book()
    assert expand_pattern("$Series $PadNum", b) == "Stormlight Archive 01"
    assert expand_pattern("$Series $SerNum", b) == "Stormlight Archive 1"


def test_unknown_token_expands_empty():
    assert expand_pattern("$Bogus$Title", _book()) == "The Way of Kings"


def test_missing_field_expands_empty():
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Solo"
    assert expand_pattern("$Author/$Title", b) == "/Solo"  # empty author segment


def test_sanitize_segment_strips_illegal_chars():
    assert sanitize_segment('a/b:c"d?e') == "abcde"
    assert sanitize_segment("  spaced  ") == "spaced"
    assert sanitize_segment("trailing.") == "trailing"


def test_sanitize_segment_neutralizes_traversal():
    assert sanitize_segment("..") == ""
    assert sanitize_segment(".") == ""
    assert sanitize_segment("???") == ""
    assert sanitize_segment("   ") == ""


def test_build_target_path_uses_single_file_name(tmp_path):
    b = _book()
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")
    target = build_target_path(tmp_path, pats, b)
    assert target == tmp_path / "Brandon Sanderson" / "The Way of Kings" / "The Way of Kings.m4b"


def test_build_target_path_falls_back_to_title_when_single_file_empty(tmp_path):
    b = _book()
    pats = PathPatterns(folder="$Author", single_file="")
    target = build_target_path(tmp_path, pats, b)
    assert target == tmp_path / "Brandon Sanderson" / "The Way of Kings.m4b"


def test_build_target_path_sanitizes_each_segment(tmp_path):
    b = _book()
    b.authors = ["AC/DC Author"]
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")
    target = build_target_path(tmp_path, pats, b)
    # the '/' in the author value must NOT create an extra directory level
    assert target.relative_to(tmp_path).parts[0] == "ACDC Author"


def test_build_target_path_authorless_collapses_segment(tmp_path):
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Solo"
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")
    target = build_target_path(tmp_path, pats, b)
    # the empty author segment collapses — Path swallows ""
    assert target == tmp_path / "Solo" / "Solo.m4b"


def test_narrator_token_uses_first_narrator():
    b = _book()
    b.narrators = ["Michael Kramer", "Kate Reading"]
    assert expand_pattern("$Narrator", b) == "Michael Kramer"  # first, mirroring $Author


def test_narrator_empty_when_absent():
    assert expand_pattern("$Narrator", _book()) == ""


def test_abridged_token_renders_word():
    b = _book()
    b.abridged = True
    assert expand_pattern("$Abridged", b) == "Abridged"
    b.abridged = False
    assert expand_pattern("$Abridged", b) == "Unabridged"


def test_abridged_empty_when_unknown():
    assert expand_pattern("$Abridged", _book()) == ""  # None by default


def test_double_dollar_is_literal():
    assert expand_pattern("$$Author", _book()) == "$Author"
    assert expand_pattern("$$Title = $Title", _book()) == "$Title = The Way of Kings"


def test_sample_target_renders_relative_path():
    from colophon.core.pathscheme import sample_target

    out = sample_target("$Author/$Series #$PadNum - $Title", "$Title")
    assert out == "Brandon Sanderson/The Stormlight Archive #01 - The Way of Kings/The Way of Kings.m4b"


def test_sample_target_falls_back_on_empty_patterns():
    from colophon.core.pathscheme import sample_target

    out = sample_target("", "")
    assert out == "Brandon Sanderson/The Way of Kings/The Way of Kings.m4b"


def test_conditional_group_dropped_when_token_empty():
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Solo"
    b.authors = ["Ann Leckie"]  # no series, so $SerNum is empty
    assert expand_pattern("[$SerNum - ]$Author - $Title", b) == "Ann Leckie - Solo"


def test_conditional_group_kept_when_token_present():
    b = _book()  # series sequence 1.0 -> $SerNum == "1"
    assert expand_pattern("[$SerNum - ]$Title", b) == "1 - The Way of Kings"


def test_conditional_group_dropped_if_any_token_empty():
    b = _book()  # has a series name but no narrator
    # $Series is present, $Narrator is empty -> drop-if-any-empty drops the whole group
    assert expand_pattern("[$Series read by $Narrator - ]$Title", b) == "The Way of Kings"


def test_literal_brackets_via_double_escape():
    b = _book()
    assert expand_pattern("[[$Title]]", b) == "[The Way of Kings]"


def test_literal_only_group_always_renders():
    b = _book()  # a group with no token is degenerate and always emits its literals
    assert expand_pattern("[static]$Title", b) == "staticThe Way of Kings"


def test_unbalanced_open_bracket_raises():
    with pytest.raises(ValueError, match=r"[Bb]racket"):
        expand_pattern("[$SerNum - $Title", _book())


def test_unbalanced_close_bracket_raises():
    with pytest.raises(ValueError, match=r"[Bb]racket"):
        expand_pattern("$Title]", _book())


def test_nested_group_raises():
    with pytest.raises(ValueError, match=r"[Nn]est"):
        expand_pattern("[$Series[$SerNum] ]$Title", _book())


def test_group_within_segment_renders_in_build(tmp_path):
    b = _book()  # $SerNum == "1"
    pats = PathPatterns(folder="$Author/$Series", single_file="[$SerNum - ]$Title")
    target = build_target_path(tmp_path, pats, b)
    assert target == tmp_path / "Brandon Sanderson" / "Stormlight Archive" / "1 - The Way of Kings.m4b"


def test_group_drops_in_filename_while_folder_segment_collapses(tmp_path):
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Solo"  # no author (segment collapses), no series (group drops)
    pats = PathPatterns(folder="$Author/$Title", single_file="[$SerNum - ]$Title")
    target = build_target_path(tmp_path, pats, b)
    assert target == tmp_path / "Solo" / "Solo.m4b"


def test_renderer_keys_match_build_tokens():
    from pathlib import Path

    from colophon.core.models import BookUnit
    from colophon.core.pathscheme import _token_values
    from colophon.core.tokens import BUILD_TOKENS
    keys = set(_token_values(BookUnit.new(source_folder=Path("/x"))))
    assert keys == {t.name for t in BUILD_TOKENS}


def test_part_total_empty_by_default():
    # single-file / no part context -> tokens empty, conditional group drops
    assert expand_pattern("$Title[ - Part $Part of $Total]", _book()) == "The Way of Kings"


def test_part_total_populated_and_padded():
    b = _book()
    assert expand_pattern("$Title[ - Part $Part of $Total]", b, part=1, total=12) == \
        "The Way of Kings - Part 01 of 12"
    assert expand_pattern("$Title[ - Part $Part of $Total]", b, part=10, total=12) == \
        "The Way of Kings - Part 10 of 12"


def test_part_total_pad_width_follows_total():
    b = _book()
    # 100 parts -> 3-digit width for both
    assert expand_pattern("$Part of $Total", b, part=7, total=100) == "007 of 100"
    # single-digit total still pads to min two
    assert expand_pattern("$Part of $Total", b, part=3, total=9) == "03 of 09"


def test_ensure_part_placeholder_appends_when_missing():
    assert ensure_part_placeholder("$Title") == "$Title ($Part of $Total)"


def test_ensure_part_placeholder_noop_when_present():
    assert ensure_part_placeholder("$Title[ - Part $Part of $Total]") == \
        "$Title[ - Part $Part of $Total]"


def test_ensure_part_placeholder_ignores_lookalike_tokens():
    # $Partition must not count as $Part
    assert ensure_part_placeholder("$Partition") == "$Partition ($Part of $Total)"


def _sf(name: str, ext: str) -> SourceFile:
    return SourceFile(path=Path(f"/ingest/x/{name}"), size=1, duration_seconds=1.0, ext=ext)


def test_build_reorg_targets_single_file_no_part():
    b = _book()
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title")
    files = [_sf("whole.mp3", ".mp3")]
    targets = build_reorg_targets(Path("/lib"), pats, b, files)
    assert targets == [Path("/lib/Brandon Sanderson/The Way of Kings/The Way of Kings.mp3")]


def test_build_reorg_targets_multipart_numbered_with_ext():
    b = _book()
    pats = PathPatterns(folder="$Author/$Title", single_file="$Title[ - Part $Part of $Total]")
    files = [_sf("a.mp3", ".mp3"), _sf("b.mp3", ".mp3"), _sf("c.mp3", ".mp3")]
    targets = build_reorg_targets(Path("/lib"), pats, b, files)
    base = Path("/lib/Brandon Sanderson/The Way of Kings")
    assert targets == [
        base / "The Way of Kings - Part 01 of 03.mp3",
        base / "The Way of Kings - Part 02 of 03.mp3",
        base / "The Way of Kings - Part 03 of 03.mp3",
    ]


def test_build_reorg_targets_multipart_autoappends_missing_part():
    b = _book()
    pats = PathPatterns(folder="$Author", single_file="$Title")  # no $Part
    files = [_sf("a.m4a", ".m4a"), _sf("b.m4a", ".m4a")]
    targets = build_reorg_targets(Path("/lib"), pats, b, files)
    base = Path("/lib/Brandon Sanderson")
    assert targets == [
        base / "The Way of Kings (01 of 02).m4a",
        base / "The Way of Kings (02 of 02).m4a",
    ]
