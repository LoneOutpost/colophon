"""corroborate_title: derive a residual title from tag / filename / folder (subtracting author,
series, franchise, numbering) and decide agree / abstain / contradict. Confidence-only slice — the
advisory suggested_title is never applied to the stored title."""

import pytest

from colophon.core.title_corroborate import (
    book_title_verdict,
    corroborate_title,
    is_abbreviation_of,
)


def _v(tag, files, folder, author, series=None):
    return corroborate_title(tag, files, folder, author, series).verdict


def test_agree_series_code_tag_matches_folder():
    # 'DC 02 - Tears of War' vs a folder 'A D Trosper - Dragons Call Bk02 - Tears of War'
    tc = corroborate_title(
        "DC 02 - Tears of War", [], "A D Trosper - Dragons Call Bk02 - Tears of War",
        ["A D Trosper"], "Dragons Call",
    )
    assert tc.verdict == "agree"
    assert "folder" in tc.agreeing_sources


def test_series_named_after_book_one_abstains_not_contradicts():
    # 'SB 01 - StarBridge', folder 'AC Crispin - StarBridge 01 - StarBridge', series == title.
    # Folder residual empties once the series name is subtracted -> abstain (high confidence kept).
    assert _v("SB 01 - StarBridge", [], "AC Crispin - StarBridge 01 - StarBridge",
              ["AC Crispin"], "StarBridge") == "abstain"


def test_contradict_tag_disagrees_with_folder():
    assert _v("Splintered", [], "A D Trosper - Dragons Call Bk02 - Tears of War",
              ["A D Trosper"], "Dragons Call") == "contradict"


def test_abstain_author_folder_no_filename_title():
    # Author folder (name subtracts to empty) + chaptered files -> only the tag carries a title.
    assert _v("Monster", ["Track 001", "Track 002"], "A Lee Martinez", ["A Lee Martinez"]) == "abstain"


def test_abstain_placeholder_tag_title():
    # A placeholder tag title contributes no residual (A1) -> abstain, even with a real folder title.
    assert _v("Track 001 - Opening Theme", [], "Dead in the Water", ["Sandy Mitchell"]) == "abstain"


def test_abstain_echo_tag_title():
    # Tag title echoes the author (A3) -> tag contributes no residual -> abstain.
    assert _v("Alexei Panshin", [], "Alexei Panshin", ["Alexei Panshin"]) == "abstain"


def test_agree_filename_carries_shared_title():
    # Chaptered files that share the title word corroborate the tag.
    assert _v("Cujo", ["Cujo - Chapter 1", "Cujo - Chapter 2"], "Stephen King", ["Stephen King"]) == "agree"


def test_agree_plain_folder_equals_title():
    assert _v("At Risk", [], "At Risk", ["Stella Rimington"]) == "agree"


def test_franchise_token_subtracted_from_both_sides():
    # Franchise word is noise; residual distills to the real title on tag and folder alike.
    tc = corroborate_title(
        "Star Wars - Heir to the Empire", [], "Timothy Zahn - Heir to the Empire",
        ["Timothy Zahn"], None, "Star Wars",
    )
    assert tc.verdict == "agree"


def test_abstain_edition_marker_tag():
    # 'Unabridged' is an edition marker, not a title -> tag carries no residual -> abstain.
    assert _v("Unabridged", [], "Bernard Cornwell - Sharpe's Eagle", ["Bernard Cornwell"]) == "abstain"


def test_abstain_no_title_placeholder():
    # A literal 'no Title' placeholder -> abstain, folder title stands.
    assert _v("no Title", [], "David Eddings - The Diamond Throne", ["David Eddings"]) == "abstain"


def test_abstain_glued_disc_track_tag():
    # 'Disk 01 - Track 01' is a per-file label even after the affix strips to 'Track 01' -> abstain.
    assert _v("Disk 01 - Track 01", [], "Greg Iles - The Footprints of God", ["Greg Iles"]) == "abstain"


def test_no_false_agree_on_shared_stopword():
    # 'The Cat' vs a folder 'The Dog' must not agree merely by sharing 'the'.
    assert _v("The Cat", [], "Aesop - The Dog", ["Aesop"]) == "contradict"


def test_title_words_agree_across_a_dropped_apostrophe():
    from colophon.core.title_corroborate import _title_words
    assert _title_words("Sharpe's Gold") == _title_words("Sharpes Gold")
    assert _title_words("Old Man's War") & _title_words("Old Mans War Bk01")


# --- A ripper's abbreviation of the folder's title --------------------------------------------


@pytest.mark.parametrize(("short", "long"), [
    ("Slov-cd-01", "Skylark of Valeron"),
    ("Slov Cd", "Skylark of Valeron"),
    ("Bf", "The Body Farm"),
    ("To-cd-1", "The Overlook"),
    ("Tsd-cd-01", "Three Shirt Deal"),
])
def test_is_abbreviation_of_true(short, long):
    assert is_abbreviation_of(short, long)


@pytest.mark.parametrize(("short", "long"), [
    ("Timeline", "Michael Crichton"),
    ("Legacy", "R. A. Salvatore"),
    ("Bf", "Black Friday Sale Event"),   # content words left uncovered
    ("Dune", "Dune"),                    # the word itself, not an abbreviation of it
    ("Dune", "Dune Messiah"),            # a content word of the long title, not an abbreviation
    ("S", "Skylark"),                    # one letter is too little to be an abbreviation
    ("Skylarkv", "Skylark of Valeron"),  # 7+ letters is a word, not an abbreviation
    ("The Mind Pool", "The Mind Pool"),  # multi-word short
    ("Tmp", "The Mind Pool Extra"),      # 'extra' contributes nothing
])
def test_is_abbreviation_of_false(short, long):
    assert not is_abbreviation_of(short, long)


def _abbrev_book(tmp_path, folder, stems, tag_titles, title):
    from colophon.core.models import BookUnit, EmbeddedTags, Provenance, SourceFile

    d = tmp_path / folder
    b = BookUnit.new(source_folder=d)
    b.title, b.authors, b.provenance["title"] = title, ["E. E. Smith"], Provenance.TAG.value
    b.source_files = [
        SourceFile(path=d / f"{stem}.opus", size=1, duration_seconds=60.0, ext="opus",
                   tags=EmbeddedTags(title=t))
        for stem, t in zip(stems, tag_titles, strict=True)
    ]
    return b


def test_title_abbreviating_the_folder_title_contradicts(tmp_path):
    """'Slov Cd' agreed with its filenames only because the same ripper wrote both; the folder names
    the real title, which the abbreviation stands for, so the verdict must side with the folder."""
    b = _abbrev_book(tmp_path, "EE Smith.-.Skylark Bk3.-.Skylark of Valeron",
                     [f"SLOV-CD-0{i}" for i in (1, 2, 3)], [f"Slov-cd-0{i}" for i in (1, 2, 3)],
                     "Slov Cd")
    tc = book_title_verdict(b)
    assert tc.verdict == "contradict"
    assert tc.suggested_title == "Skylark of Valeron"
    assert tc.suggested_from == "folder"
    assert tc.evidence == 'metadata title "Slov Cd" vs folder "Skylark of Valeron"'


def test_title_agreeing_with_the_folder_still_agrees(tmp_path):
    b = _abbrev_book(tmp_path, "EE Smith.-.Skylark Bk3.-.Skylark of Valeron",
                     [f"Skylark of Valeron - 0{i}" for i in (1, 2)], ["Skylark of Valeron"] * 2,
                     "Skylark of Valeron")
    assert book_title_verdict(b).verdict == "agree"
