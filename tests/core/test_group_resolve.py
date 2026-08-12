from __future__ import annotations

from pathlib import Path

from colophon.core import evidence_weights as W
from colophon.core.classify import FileFeatures
from colophon.core.group_resolve import (
    GroupBallot,
    GroupDecision,
    ax_cohort_constancy,
    ax_enumeration,
    ax_index_titles,
    ax_series_of_books,
    ax_uniform_author,
    ax_uniform_work_key,
    resolve_grouping,
)
from colophon.core.models import EmbeddedTags


def test_grouping_weights_exist():
    for name in (
        "W_G_PRIOR", "W_G_ENUMERATION", "W_G_INDEX_TITLES", "W_G_CONSTANCY_PER_TOKEN",
        "W_G_UNIFORM_KEY", "W_G_UNIFORM_AUTHOR", "W_G_SERIES", "W_G_SERIES_MIN_SECONDS",
    ):
        assert getattr(W, name) > 0
    assert W.W_G_CONSTANCY_CAP >= 1
    # reliable signals outweigh the prior alone; corroborators alone do not.
    assert W.W_G_ENUMERATION > W.W_G_PRIOR
    assert W.W_G_UNIFORM_KEY + W.W_G_UNIFORM_AUTHOR + W.W_G_CONSTANCY_PER_TOKEN * W.W_G_CONSTANCY_CAP < W.W_G_PRIOR


def _feat(path: str, dur: float = 210.0, **tags) -> FileFeatures:
    return FileFeatures(path=Path(path), ext=Path(path).suffix.lstrip("."),
                        duration_seconds=dur, tags=EmbeddedTags(**tags))


def _voyager(n: int, dur: float = 210.0) -> list[FileFeatures]:
    return [
        _feat(f"/lib/Diana Gabaldon - (Outlander 3) Voyager - D01.{i:02d}-23.opus",
              dur=dur, album="Voyager", artist="Diana Gabaldon", title=f"D01.{i:02d}-23")
        for i in range(1, n + 1)
    ]


def test_ax_enumeration_votes_one_on_numbered_parts():
    assert [b.outcome for b in ax_enumeration(_voyager(3))] == ["one"]


def test_ax_enumeration_silent_on_distinct_residues():
    group = [_feat("/lib/01 - Title.opus"), _feat("/lib/02 - Prologue.opus")]
    assert ax_enumeration(group) == []


def test_ax_index_titles_votes_one_when_all_structural():
    group = [_feat("/lib/a.opus", title="Chapter 01"), _feat("/lib/b.opus", title="Chapter 02")]
    assert [b.outcome for b in ax_index_titles(group)] == ["one"]


def test_ax_index_titles_silent_with_a_real_title():
    group = [_feat("/lib/a.opus", title="Real"), _feat("/lib/b.opus", title="Chapter 02")]
    assert ax_index_titles(group) == []


def test_ax_cohort_constancy_scales_with_tokens():
    assert ax_cohort_constancy(_voyager(2))[0].weight > 0


def test_ax_uniform_work_key_and_author_vote_one():
    assert [b.outcome for b in ax_uniform_work_key(_voyager(2))] == ["one"]
    assert [b.outcome for b in ax_uniform_author(_voyager(2))] == ["one"]


def test_ax_series_of_books_votes_many_for_long_files():
    long = [_feat("/lib/Series - Foundation 1.opus", dur=36000.0),
            _feat("/lib/Series - Foundation 2.opus", dur=36000.0)]
    assert [b.outcome for b in ax_series_of_books(long)] == ["many"]


def test_ax_series_of_books_silent_for_short_files():
    assert ax_series_of_books(_voyager(3, dur=210.0)) == []


def test_resolve_voyager_is_one():
    d = resolve_grouping(_voyager(5))
    assert isinstance(d, GroupDecision) and d.outcome == "one"


def test_resolve_long_numbered_series_is_many():
    group = [_feat("/lib/Foundation Series - Foundation 1.opus", dur=36000.0, artist="Isaac Asimov"),
             _feat("/lib/Foundation Series - Foundation 2.opus", dur=36000.0, artist="Isaac Asimov")]
    assert resolve_grouping(group).outcome == "many"


def test_resolve_distinct_title_shelf_is_many():
    group = [_feat("/lib/James Corey - The Expanse - Leviathan Wakes.opus", artist="James Corey"),
             _feat("/lib/James Corey - The Expanse - Calibans War.opus", artist="James Corey")]
    assert resolve_grouping(group).outcome == "many"


def test_resolve_no_signal_defaults_to_many():
    group = [_feat("/lib/a.opus"), _feat("/lib/b.opus")]
    assert resolve_grouping(group).outcome == "many"


def test_ballots_recorded_including_the_prior():
    d = resolve_grouping(_voyager(3))
    assert all(isinstance(b, GroupBallot) for b in d.ballots)
    assert any(b.outcome == "many" and "prior" in b.reason for b in d.ballots)


_CHAPTER_NAMES = ["Introduction", "Julie", "Holden", "Miller", "Naomi", "Amos", "Prologue",
                  "Epilogue", "Bran", "Arya", "Tyrion", "Cersei", "Jon", "Sansa", "Davos",
                  "Theon", "Jaime", "Brienne", "Sam", "Dany"]


def _album_chapters(n, album="Below Zero", dur=300.0):
    # distinct chapter NAMES (enumeration can't fire) under one uniform album -> the real miss shape.
    return [_feat(f"/lib/C J Box - Below Zero - {i:03d} {_CHAPTER_NAMES[i % len(_CHAPTER_NAMES)]}.opus",
                  dur=dur, album=album, artist="C J Box",
                  title=f"{i:03d} {_CHAPTER_NAMES[i % len(_CHAPTER_NAMES)]}") for i in range(1, n + 1)]


def test_ax_uniform_album_chapters_votes_one_for_many_short_files():
    from colophon.core.group_resolve import ax_uniform_album_chapters
    assert [b.outcome for b in ax_uniform_album_chapters(_album_chapters(12, dur=300.0))] == ["one"]


def test_ax_uniform_album_chapters_silent_for_few_files():
    # a few-file box set / shelf must NOT collapse via this axiom (it splits via the clusterer).
    from colophon.core.group_resolve import ax_uniform_album_chapters
    assert ax_uniform_album_chapters(_album_chapters(3, dur=300.0)) == []


def test_ax_uniform_album_chapters_silent_for_long_files():
    from colophon.core.group_resolve import ax_uniform_album_chapters
    assert ax_uniform_album_chapters(_album_chapters(12, dur=36000.0)) == []


def test_ax_uniform_album_chapters_silent_when_albums_differ():
    from colophon.core.group_resolve import ax_uniform_album_chapters
    g = [_feat(f"/lib/{i}.opus", dur=300.0, album=chr(65 + i)) for i in range(12)]
    assert ax_uniform_album_chapters(g) == []


def test_ax_uniform_album_chapters_silent_without_durations():
    from colophon.core.group_resolve import ax_uniform_album_chapters
    assert ax_uniform_album_chapters(_album_chapters(12, dur=0.0)) == []


def test_resolve_chapter_named_uniform_album_book_is_one():
    # distinct chapter names (enumeration can't fire) but uniform album + many short files -> one book.
    assert resolve_grouping(_album_chapters(20, dur=300.0)).outcome == "one"


def test_resolve_uniform_album_long_files_stays_many():
    # a whole-book-length uniform-album series folder stays split (series axiom wins).
    assert resolve_grouping(_album_chapters(12, dur=36000.0)).outcome == "many"
