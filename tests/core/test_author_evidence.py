from pathlib import Path

from colophon.core.author_evidence import collect_author_evidence, resolve_author
from colophon.core.models import BookUnit, EmbeddedTags, Provenance, SourceFile


def _book(folder: str, fname: str, artist=None):
    d = Path(folder)
    b = BookUnit.new(source_folder=d)
    b.source_files = [SourceFile(path=d / fname, size=1, duration_seconds=60.0, ext="opus",
                                 tags=EmbeddedTags(artist=artist))]
    return b


def test_collect_includes_folder_and_penalizes_blank_tag():
    b = _book("/lib/P/Wendy Pini", "ElfQuest - Chapter01.opus", artist=None)
    ev = collect_author_evidence(
        b, author_depth_folder="Wendy Pini", classified_author_name=None,
        datafile_authors=[], filename_author="ElfQuest", sibling_consensus={})
    values = {e.value for e in ev}
    assert "Wendy Pini" in values and "ElfQuest" in values


def test_resolve_folder_beats_competing_filename_when_tag_junk():
    b = _book("/lib/P/Wendy Pini", "ElfQuest - Chapter01.opus", artist=None)
    r = resolve_author(
        b, author_depth_folder="Wendy Pini", classified_author_name=None,
        datafile_authors=[], filename_author="ElfQuest", sibling_consensus={})
    assert r.value == "Wendy Pini"
    assert b.authors == ["Wendy Pini"]
    assert b.provenance["authors"] == Provenance.DIRECTORY.value


def test_resolve_title_shaped_tag_is_penalized_out():
    b = _book("/lib/A/Real Author", "01.opus", artist="(Wheel of Time #3) The Dragon Reborn")
    r = resolve_author(
        b, author_depth_folder="Real Author", classified_author_name=None,
        datafile_authors=[], filename_author=None, sibling_consensus={})
    assert r.value == "Real Author"


def test_resolve_lone_real_tag_wins_over_lone_folder():
    b = _book("/lib/misc/Bucket", "01.opus", artist="Brandon Sanderson")
    r = resolve_author(
        b, author_depth_folder="Bucket", classified_author_name=None,
        datafile_authors=[], filename_author=None, sibling_consensus={})
    assert r.value == "Brandon Sanderson"
    assert b.provenance["authors"] == Provenance.TAG.value


def test_manual_author_is_hard_and_wins():
    b = _book("/lib/A/Folder", "01.opus", artist="Tagged")
    b.authors = ["Manually Set"]
    b.provenance["authors"] = Provenance.MANUAL.value
    r = resolve_author(
        b, author_depth_folder="Folder", classified_author_name=None,
        datafile_authors=[], filename_author=None, sibling_consensus={})
    assert r.value == "Manually Set"
    assert b.authors == ["Manually Set"]
