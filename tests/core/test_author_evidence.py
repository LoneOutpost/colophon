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
    # 'ElfQuest' is canonicalized to 'Elf Quest' (glued PascalCase split) before voting.
    assert "Wendy Pini" in values and "Elf Quest" in values


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


def test_corroborated_folder_beats_lone_tag_and_stamps_folder_provenance():
    # tag names one author (3.0); folder + filename both name another (2.5 + 1.5 = 4.0) -> corroboration
    # wins and provenance is DIRECTORY (folder), not TAG.
    b = _book("/lib/A/Wendy Pini", "Wendy Pini - Book.opus", artist="Wrong Tag Author")
    r = resolve_author(
        b, author_depth_folder="Wendy Pini", classified_author_name=None,
        datafile_authors=[], filename_author="Wendy Pini", sibling_consensus={})
    assert r.value == "Wendy Pini"
    assert b.authors == ["Wendy Pini"]
    assert b.provenance["authors"] == Provenance.DIRECTORY.value


def test_multi_author_tag_is_preserved():
    b = _book("/lib/A/Folder", "01.opus", artist="Neil Gaiman & Terry Pratchett")
    resolve_author(b, author_depth_folder="Folder", classified_author_name=None,
                   datafile_authors=[], filename_author=None, sibling_consensus={})
    assert b.authors == ["Neil Gaiman", "Terry Pratchett"]


def test_a3_corroborated_structure_overturns_lone_real_tag():
    # A genuine (non-junk) tag author, but folder + filename both name a different author -> the
    # corroborated structure (2.5 + 1.5 = 4.0) overturns the lone tag (3.0).
    b = _book("/lib/A/Ursula K. Le Guin", "Ursula K. Le Guin - A Wizard of Earthsea.opus",
              artist="Wrongly Tagged Name")
    r = resolve_author(b, author_depth_folder="Ursula K. Le Guin", classified_author_name=None,
                       datafile_authors=[], filename_author="Ursula K. Le Guin", sibling_consensus={})
    assert r.value == "Ursula K. Le Guin"
    assert b.provenance["authors"] == Provenance.DIRECTORY.value


def test_a2_lone_real_tag_survives_a_single_folder():
    # Only the folder competes (no filename corroboration): the lone real tag (3.0) beats folder (2.5).
    b = _book("/lib/misc/Some Folder", "01.opus", artist="Real Tagged Author")
    r = resolve_author(b, author_depth_folder="Some Folder", classified_author_name=None,
                       datafile_authors=[], filename_author=None, sibling_consensus={})
    assert r.value == "Real Tagged Author"
    assert b.provenance["authors"] == Provenance.TAG.value


def test_tag_vote_recovered_from_book_when_sourcefile_untagged():
    # SourceFile carries no cached tags, but the book already has a tag-provenance author; the ballot
    # must still weigh it (so a lone folder can't silently overwrite a real tag).
    b = _book("/lib/A/Folder Name", "01.opus", artist=None)
    b.authors = ["Committed Tag Author"]
    b.provenance["authors"] = Provenance.TAG.value
    r = resolve_author(b, author_depth_folder="Folder Name", classified_author_name=None,
                       datafile_authors=[], filename_author=None, sibling_consensus={})
    assert r.value == "Committed Tag Author"


def test_structural_artist_is_penalized_out_of_author_ballot():
    from pathlib import Path

    from colophon.core.author_evidence import resolve_author
    from colophon.core.models import BookUnit, EmbeddedTags, SourceFile
    d = Path("/lib/A/Real Author")
    b = BookUnit.new(source_folder=d)
    b.source_files = [SourceFile(path=d / "01.opus", size=1, duration_seconds=60.0, ext="opus",
                                 tags=EmbeddedTags(artist="Chapter"))]
    r = resolve_author(b, author_depth_folder="Real Author", classified_author_name=None,
                       datafile_authors=[], filename_author=None, sibling_consensus={})
    assert r.value == "Real Author"          # the structural "Chapter" tag lost to the folder


def test_collect_author_evidence_canonicalizes_and_merges_variants():
    from pathlib import Path

    from colophon.core.models import BookUnit, EmbeddedTags, SourceFile

    b = BookUnit.new(source_folder=Path("/lib/George RR Martin"))
    b.source_files = [SourceFile(path=Path("/lib/x.opus"), size=1, duration_seconds=0.0, ext="opus",
                                 tags=EmbeddedTags(artist="George RR Martin"))]
    ev = collect_author_evidence(
        b, author_depth_folder="George R. R. Martin", classified_author_name=None,
        datafile_authors=[], filename_author=None, sibling_consensus={})
    vals = {e.value for e in ev if e.weight > 0}
    assert vals == {"George R. R. Martin"}
