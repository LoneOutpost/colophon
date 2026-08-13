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


def test_confident_classified_author_node_beats_a_lone_tag():
    # A book whose only tag says one thing, sitting under a confidently-classified author folder that
    # says another: the confident node (conf 1.0 -> weight 4.0) out-votes the lone tag (3.0).
    b = _book("/lib/G/Diana Gabaldon", "01.opus", artist="Recorded Books")
    r = resolve_author(
        b, author_depth_folder=None, classified_author_name="Diana Gabaldon",
        classified_author_confidence=1.0, datafile_authors=[], filename_author=None,
        sibling_consensus={})
    assert r.value == "Diana Gabaldon"


def test_weak_classified_author_node_loses_to_a_tag():
    # The same shape but a shaky node (conf 0.3 -> weight ~2.95, still below the tag 3.0): the tag wins,
    # so the folder stays a ballot rather than a veto.
    b = _book("/lib/G/Diana Gabaldon", "01.opus", artist="Recorded Books")
    r = resolve_author(
        b, author_depth_folder=None, classified_author_name="Diana Gabaldon",
        classified_author_confidence=0.3, datafile_authors=[], filename_author=None,
        sibling_consensus={})
    assert r.value == "Recorded Books"


def test_tag_artist_agreement_across_files_reinforces_but_caps():
    from colophon.core import evidence_weights as W
    from colophon.core.models import EmbeddedTags, SourceFile

    d = Path("/lib/G/George Orwell/1984")
    b = BookUnit.new(source_folder=d)
    b.source_files = [SourceFile(path=d / f"1984 - {i}.opus", size=1, duration_seconds=0.0, ext="opus",
                                 tags=EmbeddedTags(artist="George Orwell")) for i in range(1, 10)]
    ev = collect_author_evidence(b, author_depth_folder=None, classified_author_name=None,
                                 datafile_authors=[], filename_author=None, sibling_consensus={})
    tag = [e for e in ev if e.source == "tag"]
    assert len(tag) == 1 and tag[0].value == "George Orwell"
    assert W.W_A_TAG < tag[0].weight <= W.W_A_TAG_MAX          # 9-file agreement reinforces, capped

    # a single-file book is unchanged (one file -> exactly W_A_TAG)
    s = _book("/lib/x", "a.opus", artist="Solo")
    ev1 = collect_author_evidence(s, author_depth_folder=None, classified_author_name=None,
                                  datafile_authors=[], filename_author=None, sibling_consensus={})
    assert [e for e in ev1 if e.source == "tag"][0].weight == W.W_A_TAG


def test_leaf_folder_author_beats_filename_and_supplies_the_swapped_author():
    # A `.-.` leaf folder (Kim Stanley Robinson.-.Galileos Dream.-.Unb) whose files are named
    # 'Title - suffix' (no author): the filename $Author parse reads the TITLE as author. The leaf
    # folder author must out-weigh it so the real author wins.
    b = _book("/lib/R/Kim Stanley Robinson.-.Galileos Dream.-.Unb", "Galileos Dream - Unb-001.opus")
    r = resolve_author(
        b, author_depth_folder=None, classified_author_name=None, datafile_authors=[],
        filename_author="Galileos Dream", sibling_consensus={},
        leaf_folder_author="Kim Stanley Robinson")
    assert r.value == "Kim Stanley Robinson"


def test_leaf_folder_author_loses_to_a_real_tag():
    # A tag author out-weighs the leaf `.-.` folder author (2.75 < 3.0): the leaf is a corrective
    # prior, not an override of a tagged author.
    b = _book("/lib/R/M C Beaton.-.Kissing Christmas Goodbye", "01.opus", artist="M. C. Beaton")
    r = resolve_author(
        b, author_depth_folder=None, classified_author_name=None, datafile_authors=[],
        filename_author=None, sibling_consensus={}, leaf_folder_author="M C Beaton")
    assert r.value == "M. C. Beaton"
