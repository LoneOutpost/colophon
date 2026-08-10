"""corroborate_title: derive a residual title from tag / filename / folder (subtracting author,
series, franchise, numbering) and decide agree / abstain / contradict. Confidence-only slice — the
advisory suggested_title is never applied to the stored title."""

from colophon.core.title_corroborate import author_looks_like_title, corroborate_title


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


def test_author_looks_like_title_detects_series_paren():
    assert author_looks_like_title("The End of the Matter (Flinx 03)", "The End of the Matter") is True
    assert author_looks_like_title("Anne McCaffrey", "Restoree") is False


def test_author_looks_like_title_detects_echo_and_sequence():
    assert author_looks_like_title("Restoree", "Restoree") is True
    assert author_looks_like_title("02 - Yendi", "Yendi") is True
