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


def test_folder_last_first_matches_artist_is_author():
    # "King, Stephen" folder vs "Stephen King" tag: the same author under the canonical entity key,
    # which node_classify already uses. Plain casefold ("king, stephen") would miss it.
    feats = [_feat("/lib/King, Stephen/Legion.mp3", artist="Stephen King")]
    kind, _ = classify_folder_kind(
        Path("/lib/King, Stephen"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.AUTHOR


def test_folder_diacritic_matches_artist_is_author():
    # Diacritic-folded entity key: "Bela Bartok" folder matches a "Béla Bartók" tag.
    feats = [_feat("/lib/Bela Bartok/x.mp3", artist="Béla Bartók")]
    kind, _ = classify_folder_kind(
        Path("/lib/Bela Bartok"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.AUTHOR


def test_reordered_title_does_not_match_album_stays_casefold():
    # Album detection stays on a plain casefold key, NOT the person-name entity key: a reordered
    # "Gathering, The" must not be canonicalized into matching the folder "The Gathering".
    feats = [_feat("/lib/The Gathering/part1.mp3", album="Gathering, The")]
    kind, _ = classify_folder_kind(
        Path("/lib/The Gathering"), Path("/lib"), feats,
        template_pattern=TEMPLATE, scheme_patterns=SCHEME,
    )
    assert kind is FolderKind.UNDETERMINED


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


def test_force_single_collapses_distinct_works_into_one_book():
    # distinct albums normally split into MULTI; a Combine (force_single) overrides that so every
    # file is one book's chapter, keeping the grouping stuck across a rescan.
    feats = [
        _feat("/a/d/Legion.mp3", album="Legion", artist="Brandon Sanderson"),
        _feat("/a/d/Elantris.mp3", album="Elantris", artist="Brandon Sanderson"),
    ]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME,
                 force_single=True)
    assert r.content_kind is CK.SINGLE
    assert len(r.detected_works) == 1
    assert len(r.detected_works[0].files) == 2


def test_numbered_files_no_tags_are_one_sequence():
    feats = [_feat("/a/d/track 1.mp3"), _feat("/a/d/track 2.mp3"), _feat("/a/d/track 3.mp3")]
    works, signals = group_works(feats)
    assert len(works) == 1
    assert content_kind_for(works, signals) is CK.SINGLE


def test_untagged_distinct_files_are_multi():
    # distinct-title unkeyed files are handed to the clusterer, which reads them as two works
    # (matching the fully-untagged classify() path — one grouper, one answer)
    feats = [_feat("/a/d/alpha.mp3"), _feat("/a/d/bravo.mp3")]
    works, signals = group_works(feats)
    assert len(works) == 2
    assert content_kind_for(works, signals) is CK.MULTI


def test_single_file_prefers_title_tag_over_album_for_label():
    # A single-file book where the Album tag holds the *series* ("Thrawn Ascendancy") and the Title
    # tag holds the real book title ("Chaos Rising"). The work must be labeled from the Title tag,
    # not the series-bearing Album, so the series name doesn't masquerade as the title.
    feats = [_feat("/a/STAR WARS/Chaos Rising (Thrawn Ascendancy 1).mp3",
                   title="Chaos Rising", album="Thrawn Ascendancy", artist="Timothy Zahn")]
    works, _ = group_works(feats)
    assert len(works) == 1
    assert works[0].label == "Chaos Rising"


def test_chaptered_book_keeps_album_as_label():
    # Several files sharing one album with per-file *chapter* titles are one book: the Album tag is
    # the book's title and the per-file Titles are chapters, so the label stays the Album. This
    # guards the single-file title preference from leaking into the chaptered case.
    feats = [
        _feat("/a/d/01.mp3", album="The Way of Kings", title="Chapter 1"),
        _feat("/a/d/02.mp3", album="The Way of Kings", title="Chapter 2"),
    ]
    works, _ = group_works(feats)
    assert len(works) == 1
    assert works[0].label == "The Way of Kings"


def test_chaptered_book_uses_uniform_title_when_album_varies():
    # Files confirmed one book (shared ASIN) but the Album tag differs per file while the Title is
    # identical: the tag that *matches across the files* names the book, so the shared Title wins over
    # the varying per-file Album.
    feats = [
        _feat("/a/d/01.mp3", asin="B01", title="The Silmarillion", album="Ainulindale"),
        _feat("/a/d/02.mp3", asin="B01", title="The Silmarillion", album="Valaquenta"),
    ]
    works, _ = group_works(feats)
    assert len(works) == 1
    assert works[0].label == "The Silmarillion"


def test_reversed_title_album_tags_use_filename_to_pick_title():
    # Some files misfile the tags: the Title holds the *series* and the Album holds the book title.
    # The filename ("Allies (Fate Of The Jedi 5)") arbitrates — its parenthetical is the series, so a
    # Title tag equal to that series is rejected and the real title is used.
    feats = [_feat("/a/SW/Allies (Fate Of The Jedi 5).mp3",
                   title="Fate Of The Jedi", album="Allies", artist="Aaron Allston")]
    works, _ = group_works(feats)
    assert works[0].label == "Allies"


def test_album_titles_a_single_file_when_filename_is_unstructured():
    # No Title tag and a filename with no series parenthetical: the Album is the book's real title
    # ("Darth Plagueis"), preferred over the mangled filename stem ("Darth Plague is").
    feats = [_feat("/a/SW/Darth Plague is.mp3", album="Darth Plagueis", artist="James Luceno")]
    works, _ = group_works(feats)
    assert works[0].label == "Darth Plagueis"


def test_shouting_tag_title_is_proper_cased():
    # An all-caps Title tag is not an intentional spelling; de-shout it like any other weak title.
    feats = [_feat("/a/SW/Heir to the Empire (Thrawn Trilogy 1).mp3",
                   title="HEIR TO THE EMPIRE", album="volume 1", artist="Timothy Zahn")]
    works, _ = group_works(feats)
    assert works[0].label == "Heir To The Empire"   # de-shouted (proper_case_if_shouting)


def test_placeholder_title_tag_falls_back_to_filename():
    # Junk placeholder tags ("Track 1", "Unknown Album ...") are ignored so the real title comes
    # from the filename ("Vortex"), not the useless tag.
    feats = [_feat("/a/SW/Vortex (Fate Of The Jedi 6).mp3",
                   title="Track 1", album="Unknown Album (11/30/2010)", artist="Troy Denning")]
    works, _ = group_works(feats)
    assert works[0].label == "Vortex"


def test_shared_album_split_favors_tag_title():
    # Books that share a series album ("X WING") split into per-file works. Each work's title should
    # now come from its Title tag ("The Krytos Trap"), not the barer filename ("Krytos Trap").
    feats = [
        _feat("/a/SW/Krytos Trap (X-Wing 3).mp3", title="The Krytos Trap", album="X WING", artist="M. Stackpole"),
        _feat("/a/SW/Wedge's Gamble (X-Wing 2).mp3", title="Wedge's Gamble", album="X WING", artist="M. Stackpole"),
    ]
    works, _ = group_works(feats)
    assert "The Krytos Trap" in {w.label for w in works}


def test_single_file_tag_typo_defers_to_filename():
    # A Title tag that is a near-duplicate of the filename but differs by more than a leading article
    # is a rip typo ("Issard's" for the correct "Isard's") — the filename wins.
    feats = [_feat("/a/SW/Isard's Revenge.mp3", title="Issard's Revenge", album="X WING", artist="M. Stackpole")]
    works, _ = group_works(feats)
    assert works[0].label == "Isard's Revenge"


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


def test_same_title_no_numbers_split_as_separate_editions():
    # "foo.mp3" and "foo - bar.mp3" share the leading title but differ by a non-numeric trailing
    # chunk and carry no chapter numbers -> not one multi-chapter book; two separate editions.
    feats = [
        _feat("/lib/Brandon Sanderson/foo.mp3"),
        _feat("/lib/Brandon Sanderson/foo - bar.mp3"),
    ]
    r = _classify("/lib/Brandon Sanderson", "/lib", feats)
    assert r.content_kind is ContentKind.MULTI
    assert len(r.detected_works) == 2
    assert not any(f.code is FC.STRUCTURE_UNCLEAR for f in r.findings)


def test_unknown_content_emits_structure_unclear():
    # Numbers vary but a trailing chunk hides distinguishing text the clusterer could not compare
    # ("01 - Foo" vs "02") -> genuinely UNKNOWN structure, surfaced for review.
    feats = [
        _feat("/lib/Brandon Sanderson/01 - Foo.mp3"),
        _feat("/lib/Brandon Sanderson/02.mp3"),
    ]
    r = _classify("/lib/Brandon Sanderson", "/lib", feats)
    assert r.content_kind is ContentKind.UNKNOWN
    su = [f for f in r.findings if f.code is FC.STRUCTURE_UNCLEAR]
    assert su and su[0].severity is FindingSeverity.INFO


def test_fully_untagged_distinct_filenames_are_multi():
    # Fully-untagged files with distinct stems: cluster() identifies two works → MULTI.
    feats = [_feat("/lib/Brandon Sanderson/alpha.mp3"), _feat("/lib/Brandon Sanderson/bravo.mp3")]
    r = _classify("/lib/Brandon Sanderson", "/lib", feats)
    assert r.content_kind is ContentKind.MULTI


def test_clean_single_title_still_has_no_findings_after_unclear_rule():
    feats = [_feat("/lib/Legion/Legion.mp3", album="Legion")]
    r = _classify("/lib/Legion", "/lib", feats)
    assert r.findings == []


def test_structure_unclear_not_added_when_other_finding_present():
    # Two distinct albums in a title-named folder -> MIXED_WORKS (error), content MULTI.
    feats = [
        _feat("/lib/Legion/Legion.mp3", album="Legion"),
        _feat("/lib/Legion/Elantris.mp3", album="Elantris"),
    ]
    r = _classify("/lib/Legion", "/lib", feats)
    assert all(f.code is not FC.STRUCTURE_UNCLEAR for f in r.findings)


def test_series_album_with_distinct_titles_splits_into_separate_books():
    # stella Rimington regression: distinct novels share album="Liz Carlyle" (the SERIES name).
    # The shared album key must NOT collapse different-title books into one work.
    feats = [
        _feat("/a/d/Close Call (Liz Carlyle 8).mp3", album="Liz Carlyle", artist="Stella Rimington"),
        _feat("/a/d/Dead Line (Liz Carlyle 4).mp3", album="Liz Carlyle", artist="Stella Rimington"),
        _feat("/a/d/Breaking Cover (Liz Carlyle 9).mp3", album="Liz Carlyle", artist="Stella Rimington"),
    ]
    works, signals = group_works(feats)
    assert len(works) == 3
    assert content_kind_for(works, signals) is CK.MULTI
    # each split work carries its own filename title + the series/sequence the clusterer read
    assert {w.label for w in works} == {"Close Call", "Dead Line", "Breaking Cover"}


def test_title_album_with_numbered_parts_stays_one_book():
    # when the album really is the book title, its numbered parts still merge into one work
    feats = [
        _feat("/a/d/The Winter Sea Part 1.mp3", album="The Winter Sea", artist="Susanna Kearsley"),
        _feat("/a/d/The Winter Sea Part 2.mp3", album="The Winter Sea", artist="Susanna Kearsley"),
    ]
    works, signals = group_works(feats)
    assert len(works) == 1
    assert content_kind_for(works, signals) is CK.SINGLE


def test_empty_audio_file_is_flagged(tmp_path):
    # A nonempty file with zero readable duration (corrupt / incomplete download) is flagged ERROR.
    folder = tmp_path / "Some Author" / "Some Book"
    folder.mkdir(parents=True)
    p = folder / "01.mp3"
    p.write_bytes(b"\x00" * (128 * 1024))  # 128KB, no audio
    feat = FileFeatures(path=p, ext="mp3", duration_seconds=0.0, tags=EmbeddedTags())

    r = _classify(folder, tmp_path, [feat])

    empty = [f for f in r.findings if f.code is FC.EMPTY_AUDIO]
    assert empty and empty[0].severity is FindingSeverity.ERROR


def test_tiny_zero_duration_file_not_flagged(tmp_path):
    # A sub-64KB stray with no duration is ignored — not a real audiobook file.
    folder = tmp_path / "x"
    folder.mkdir()
    p = folder / "blip.mp3"
    p.write_bytes(b"\x00" * 1024)
    feat = FileFeatures(path=p, ext="mp3", duration_seconds=0.0, tags=EmbeddedTags())

    r = _classify(folder, tmp_path, [feat])

    assert not any(f.code is FC.EMPTY_AUDIO for f in r.findings)


def test_real_duration_file_not_flagged(tmp_path):
    folder = tmp_path / "a" / "b"
    folder.mkdir(parents=True)
    p = folder / "01.mp3"
    p.write_bytes(b"\x00" * (128 * 1024))
    feat = FileFeatures(path=p, ext="mp3", duration_seconds=3600.0, tags=EmbeddedTags())

    r = _classify(folder, tmp_path, [feat])

    assert not any(f.code is FC.EMPTY_AUDIO for f in r.findings)


def test_partition_groups_files_into_named_books():
    feats = [
        _feat("/a/d/01.mp3", album="X"), _feat("/a/d/02.mp3", album="X"),
        _feat("/a/d/03.mp3", album="Y"),
    ]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME,
                 partition=[["01.mp3", "02.mp3"], ["03.mp3"]])
    assert r.content_kind is CK.MULTI
    assert len(r.detected_works) == 2
    assert sorted(len(w.files) for w in r.detected_works) == [1, 2]


def test_partition_unlisted_file_becomes_its_own_work():
    feats = [_feat("/a/d/01.mp3"), _feat("/a/d/02.mp3"), _feat("/a/d/new.mp3")]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME,
                 partition=[["01.mp3", "02.mp3"]])
    assert len(r.detected_works) == 2  # the group + the unlisted "new.mp3"
    labels = {tuple(sorted(p.name for p in w.files)) for w in r.detected_works}
    assert ("new.mp3",) in labels


def test_partition_single_group_is_single():
    feats = [_feat("/a/d/01.mp3"), _feat("/a/d/02.mp3")]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME,
                 partition=[["01.mp3", "02.mp3"]])
    assert r.content_kind is CK.SINGLE
    assert len(r.detected_works) == 1


def test_corrupt_source_files_selects_sized_zero_duration():
    from colophon.core.classify import corrupt_source_files
    from colophon.core.models import SourceFile

    good = SourceFile(path=Path("/a/01.mp3"), size=5_000_000, duration_seconds=1200.0, ext="mp3")
    corrupt = SourceFile(path=Path("/a/02.mp3"), size=5_000_000, duration_seconds=0.0, ext="mp3")
    tiny = SourceFile(path=Path("/a/03.mp3"), size=1000, duration_seconds=0.0, ext="mp3")  # stray

    assert corrupt_source_files([good, corrupt, tiny]) == [Path("/a/02.mp3")]


def test_missing_track_flagged_for_multifile_single_book():
    feats = [
        _feat("/a/d/01.mp3", album="B", track=1),
        _feat("/a/d/02.mp3", album="B", track=2),
        _feat("/a/d/04.mp3", album="B", track=4),
    ]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME)
    assert r.content_kind is CK.SINGLE
    assert FC.MISSING_TRACKS in {f.code for f in r.findings}


def test_complete_multifile_book_has_no_missing_tracks():
    feats = [_feat(f"/a/d/0{i}.mp3", album="B", track=i) for i in (1, 2, 3)]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME)
    assert not any(f.code is FC.MISSING_TRACKS for f in r.findings)


def test_single_file_book_has_no_missing_tracks():
    feats = [_feat("/a/d/05.mp3", album="B", track=5)]
    r = classify(Path("/a/d"), Path("/a"), feats, template_pattern=TEMPLATE, scheme_patterns=SCHEME)
    assert not any(f.code is FC.MISSING_TRACKS for f in r.findings)
