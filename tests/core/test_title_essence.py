from colophon.core.title_essence import title_essence


def _ess(committed, folder="", stems=None, tags=None, authors=None, series=None):
    return title_essence(
        committed, folder_name=folder, file_stems=stems or [], tag_titles=tags or [],
        authors=authors or [], series=series or [],
    )


def test_compound_author_and_series_prefix_reduces_to_the_title():
    # 'Alexey Pehov - Shadow Chaser - Siala Bk 2' committed; folder + filenames agree on 'Shadow Chaser'.
    out = _ess(
        "Alexey Pehov - Shadow Chaser - Siala Bk 2",
        folder="Alexey Pehov - Shadow Chaser - Siala Bk 2",
        stems=["Alexey Pehov - Shadow Chaser - Siala Bk 2 - 01",
               "Alexey Pehov - Shadow Chaser - Siala Bk 2 - 02"],
        authors=["Alexey Pehov"], series=["Siala Bk 2"],
    )
    assert out == "Shadow Chaser"


def test_series_number_prefix_reduces():
    out = _ess(
        "Renegades of Pern 08 - The Skies of Pern",
        folder="Renegades of Pern 08 - The Skies of Pern",
        stems=["The Skies of Pern 01", "The Skies of Pern 02"],
        authors=["Anne McCaffrey"], series=["Renegades of Pern"],
    )
    assert out == "The Skies of Pern"


def test_author_glued_without_separator_does_not_leak():
    # A source keeps the author because it has no ' - ' ('Arthur C Clarke The Fountains of Paradise');
    # word-level author removal keeps it out of the essence.
    out = _ess(
        "Arthur C Clarke The Fountains of Paradise",
        folder="Arthur C Clarke.-.The Fountains of Paradise",
        stems=["Arthur C Clarke The Fountains of Paradise 1",
               "Arthur C Clarke The Fountains of Paradise 2"],
        authors=["Arthur C Clarke"], series=[],
    )
    assert out == "The Fountains of Paradise"


def test_cross_source_typo_is_kept_via_fuzzy():
    # folder says 'Chrome Yellow', a file stem has the typo 'Crome' -> the committed 'Chrome' is kept.
    out = _ess(
        "Chrome Yellow",
        folder="Aldous Huxley - Chrome Yellow",
        stems=["Crome Yellow 01", "Crome Yellow 02"],
        authors=["Aldous Huxley"], series=[],
    )
    assert out is None or "Chrome" in out  # not dropped to just 'Yellow'


def test_hyphen_display_is_preserved():
    out = _ess(
        "The Tar-Aiym Krang",
        folder="Alan Dean Foster.-.Flinx Bk02.-.The Tar-Aiym Krang",
        stems=["The Tar Aiym Krang 01", "The Tar Aiym Krang 02"],
        authors=["Alan Dean Foster"], series=["Flinx Bk02"],
    )
    # unchanged (all words corroborated) -> None; if it renders, the hyphen must survive
    assert out is None or "Tar-Aiym" in out


def test_series_word_that_is_also_a_title_word_is_not_dropped():
    # 'Flinx' is the series AND a real word of 'Flinx in Flux' — it must NOT be removed.
    out = _ess(
        "Flinx in Flux",
        folder="Alan Dean Foster.-.Flinx Bk08.-.Flinx in Flux",
        stems=["Flinx in Flux 01", "Flinx in Flux 02"],
        authors=["Alan Dean Foster"], series=["Flinx"],
    )
    assert out is None or out == "Flinx in Flux"


def test_fewer_than_two_sources_returns_none():
    assert _ess("Some Title", folder="Author.-.Some Title", authors=["Author"]) is None


def test_clean_title_all_corroborated_returns_none():
    out = _ess(
        "Orphan Star",
        folder="Alan Dean Foster.-.Flinx Bk03.-.Orphan Star",
        stems=["Orphan Star 01", "Orphan Star 02"],
        authors=["Alan Dean Foster"], series=["Flinx Bk03"],
    )
    assert out is None   # nothing to drop


def test_result_is_always_a_subset_in_committed_order():
    out = _ess(
        "B V Larson - Steel World - Undying Mercenaries 1",
        folder="B V Larson - Steel World - Undying Mercenaries 1",
        stems=["Steel World 01", "Steel World 02"],
        authors=["B V Larson"], series=["Undying Mercenaries 1"],
    )
    assert out == "Steel World"


def test_author_as_title_person_name_is_left_alone():
    # the committed title IS the author (a mis-derivation) -> essence must not touch it
    assert _ess("Chuck Klosterman", folder="Chuck Klosterrman.-.Downtown Owl.-.UA 135@64.44m",
                stems=["001 - Chuck Klosterrman - Downtown Owl"], authors=["Chuck Klosterrman"]) is None
    assert _ess("Anne McCaffrey and Elizabeth Ann Scarborough",
                folder="Anne McCaffrey and Elizabeth Ann Scarborough.-.Barque Cats 1.-.Catalyst",
                stems=["Catalyst 01"], authors=["Anne McCaffrey"]) is None


def test_ampersand_and_articles_are_kept():
    # '&' and articles are structural glue, never dropped as noise
    assert _ess("Angels & Demons", folder="Dan Brown.-.Angels & Demons",
                stems=["Angels and Demons 01", "Angels and Demons 02"], authors=["Dan Brown"]) is None
    assert _ess("The Book of Fate", folder="Brad Meltzer.-.The Book of Fate",
                stems=["The Book of Fate 01", "The Book of Fate 02"], authors=["Brad Meltzer"]) is None


def test_trailing_glue_left_by_a_dropped_tail_is_trimmed():
    # 'read by Janet' is not corroborated -> dropped; the dangling 'by' is trimmed, not left
    out = _ess("The Red Dahlia read by Janet",
               folder="Mo Hayder.-.The Red Dahlia",
               stems=["The Red Dahlia 01", "The Red Dahlia 02"], authors=["Mo Hayder"])
    assert out == "The Red Dahlia"


def test_for_book_applies_on_noisy_title_and_skips_clean():
    from pathlib import Path

    from colophon.core.models import BookUnit, EmbeddedTags, SourceFile
    from colophon.core.title_essence import title_essence_for_book
    d = Path("/lib/R/Kim Stanley Robinson.-.2312")
    b = BookUnit.new(source_folder=d)
    b.authors = ["Kim Stanley Robinson"]
    b.source_files = [SourceFile(path=d / f"2312 - {i}.opus", size=1, duration_seconds=1.0, ext="opus",
                                 tags=EmbeddedTags(title="2312")) for i in range(1, 3)]
    b.title = "Kim Stanley Robinson - 2312"          # noisy: author prefix + ' - '
    assert title_essence_for_book(b) == "2312"
    b.title = "The Cat Who Sang for the Birds"        # clean, series-shaped prefix -> left alone
    assert title_essence_for_book(b) is None
