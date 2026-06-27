from pathlib import Path

from colophon.adapters.sidecar import SidecarMetadata
from colophon.core.models import BookUnit, EmbeddedTags
from colophon.core.reconcile import reconcile


def _unit() -> BookUnit:
    return BookUnit.new(source_folder=Path("/ingest/The Way of Kings"))


def test_embedded_tags_take_precedence_over_filename():
    book = _unit()
    embedded = EmbeddedTags(title="Embedded Title", artist="Brandon Sanderson")
    filename_fields = {"title": "Filename Title", "author": "Someone Else"}
    reconcile(book, embedded=embedded, dir_title="Folder Title", filename_fields=filename_fields)
    assert book.title == "Embedded Title"
    assert book.authors == ["Brandon Sanderson"]
    assert book.provenance["title"] == "tag"
    assert book.provenance["authors"] == "tag"


def test_embedded_album_fills_title_below_embedded_title():
    # title ladder: embedded.title -> embedded.album -> ... ; album is the fallback
    # when there's no embedded title.
    book = _unit()
    reconcile(book, embedded=EmbeddedTags(album="Album As Title"), dir_title="Folder Title",
              filename_fields={"title": "Filename Title"})
    assert book.title == "Album As Title"
    assert book.provenance["title"] == "tag"


def test_directory_fills_title_when_tags_absent():
    book = _unit()
    reconcile(book, embedded=EmbeddedTags(), dir_title="Folder Title", filename_fields={})
    assert book.title == "Folder Title"
    assert book.provenance["title"] == "directory"


def test_filename_is_last_resort():
    book = _unit()
    reconcile(
        book,
        embedded=EmbeddedTags(),
        dir_title=None,
        filename_fields={"author": "Andy Weir", "year": "2021"},
    )
    assert book.authors == ["Andy Weir"]
    assert book.publish_year == 2021
    assert book.provenance["authors"] == "filename"
    assert book.provenance["publish_year"] == "filename"


def test_filename_decimal_sequence_is_preserved():
    book = _unit()
    reconcile(
        book,
        embedded=EmbeddedTags(),
        dir_title=None,
        filename_fields={"series": "Stormlight", "sequence": "1.5"},
    )
    assert book.series[0].name == "Stormlight"
    assert book.series[0].sequence == 1.5
    assert book.provenance["series"] == "filename"


def test_series_from_embedded_builds_series_ref():
    book = _unit()
    reconcile(
        book,
        embedded=EmbeddedTags(series="Stormlight Archive", sequence=1.0),
        dir_title=None,
        filename_fields={},
    )
    assert book.series[0].name == "Stormlight Archive"
    assert book.series[0].sequence == 1.0
    assert book.provenance["series"] == "tag"


def test_sidecar_fills_gaps_below_embedded():
    book = _unit()
    embedded = EmbeddedTags(title="Embedded Title", artist="Douglas Adams")  # no series/year/narrator
    sidecar = SidecarMetadata(
        title="Sidecar Title", authors=["Someone Else"], narrators=["Douglas Adams"],
        series_name="Dirk Gently", series_sequence=1.0, publish_year=2010,
        description="desc", asin="B0041G6CSI",
    )
    reconcile(book, embedded=embedded, sidecar=sidecar, dir_title="Folder", filename_fields={})
    # embedded wins where present:
    assert book.title == "Embedded Title" and book.provenance["title"] == "tag"
    assert book.authors == ["Douglas Adams"] and book.provenance["authors"] == "tag"
    # sidecar fills the gaps embedded lacked:
    assert book.narrators == ["Douglas Adams"] and book.provenance["narrators"] == "datafile"
    assert book.series[0].name == "Dirk Gently" and book.series[0].sequence == 1.0
    assert book.provenance["series"] == "datafile"
    assert book.publish_year == 2010 and book.provenance["publish_year"] == "datafile"
    assert book.asin == "B0041G6CSI" and book.provenance["asin"] == "datafile"
    assert book.description == "desc" and book.provenance["description"] == "datafile"


def test_embedded_isbn_is_normalized_onto_book():
    book = _unit()
    embedded = EmbeddedTags(title="T", isbn="978-0-306-40615-7")
    reconcile(book, embedded=embedded, dir_title=None, filename_fields={})
    assert book.isbn == "9780306406157" and book.provenance["isbn"] == "tag"


def test_sidecar_isbn_fills_when_embedded_lacks_it():
    book = _unit()
    sidecar = SidecarMetadata(isbn="0-306-40615-2")
    reconcile(book, embedded=EmbeddedTags(title="T"), sidecar=sidecar, dir_title=None, filename_fields={})
    assert book.isbn == "0306406152" and book.provenance["isbn"] == "datafile"


def test_sidecar_title_used_when_no_embedded_title():
    book = _unit()
    reconcile(
        book,
        embedded=EmbeddedTags(),
        sidecar=SidecarMetadata(title="Sidecar Title", authors=["A"]),
        dir_title="Folder Title",
        filename_fields={},
    )
    assert book.title == "Sidecar Title"
    assert book.provenance["title"] == "datafile"  # sidecar outranks directory


def test_reconcile_without_sidecar_still_works():
    book = _unit()
    reconcile(book, embedded=EmbeddedTags(title="T", artist="A"), sidecar=None, dir_title=None, filename_fields={})
    assert book.title == "T" and book.authors == ["A"]


def test_comma_joined_embedded_artist_splits_into_authors():
    book = _unit()
    reconcile(book, embedded=EmbeddedTags(artist="Terry Jones, Douglas Adams"),
              sidecar=None, dir_title=None, filename_fields={})
    assert book.authors == ["Terry Jones", "Douglas Adams"]
    assert book.provenance["authors"] == "tag"


def test_comma_joined_embedded_narrator_splits():
    book = _unit()
    reconcile(book, embedded=EmbeddedTags(narrator="Stephen Fry, Martin Freeman"),
              sidecar=None, dir_title=None, filename_fields={})
    assert book.narrators == ["Stephen Fry", "Martin Freeman"]


def test_single_embedded_author_stays_single():
    book = _unit()
    reconcile(book, embedded=EmbeddedTags(artist="Douglas Adams"),
              sidecar=None, dir_title=None, filename_fields={})
    assert book.authors == ["Douglas Adams"]


def test_directory_fields_fill_author_and_series_below_sidecar():
    book = BookUnit.new(source_folder=Path("/x"))
    reconcile(
        book, embedded=EmbeddedTags(title="T"), sidecar=None, dir_title="T",
        filename_fields={}, directory_fields={"author": "Brandon Sanderson", "series": "Stormlight Archive"},
    )
    assert book.authors == ["Brandon Sanderson"]
    assert book.provenance["authors"] == "directory"
    assert book.series[0].name == "Stormlight Archive"
    assert book.provenance["series"] == "directory"


def test_embedded_outranks_directory_fields():
    book = BookUnit.new(source_folder=Path("/x"))
    reconcile(
        book, embedded=EmbeddedTags(artist="Tagged Author"), sidecar=None, dir_title=None,
        filename_fields={}, directory_fields={"author": "Dir Author"},
    )
    assert book.authors == ["Tagged Author"]
    assert book.provenance["authors"] == "tag"


def test_reconcile_fills_only_empty_fields_on_existing_book(tmp_path):
    book = BookUnit.new(source_folder=Path("/x"))
    book.title = "My Edited Title"
    book.authors = ["Edited Author"]
    reconcile(
        book,
        embedded=EmbeddedTags(title="Tag Title", artist="Tag Author", asin="B0TAG"),
        dir_title="Folder",
        filename_fields={},
    )
    assert book.title == "My Edited Title"
    assert book.authors == ["Edited Author"]
    assert book.asin == "B0TAG"


def test_embedded_artist_last_first_kept_as_one_author():
    book = BookUnit.new(source_folder=Path("/ingest/x"))
    reconcile(book, embedded=EmbeddedTags(artist="Herbert, Frank"),
              dir_title=None, filename_fields={})
    # Auto heuristic keeps "Last, First" as one author (was naively split before).
    assert book.authors == ["Herbert, Frank"]


def test_embedded_artist_two_full_names_still_splits():
    book = BookUnit.new(source_folder=Path("/ingest/y"))
    reconcile(book, embedded=EmbeddedTags(artist="Terry Jones, Douglas Adams"),
              dir_title=None, filename_fields={})
    assert book.authors == ["Terry Jones", "Douglas Adams"]


def test_directory_tier_supplies_series_sequence_and_narrator_and_year():
    book = BookUnit.new(source_folder=Path("/x"))
    reconcile(
        book,
        embedded=EmbeddedTags(),
        dir_title=None,
        filename_fields={},
        directory_fields={"series": "Stormlight", "sequence": "1", "narrator": "Kramer", "year": "2010"},
    )
    assert book.series[0].name == "Stormlight"
    assert book.series[0].sequence == 1.0   # was hardcoded None before
    assert book.narrators == ["Kramer"]     # new directory tier
    assert book.publish_year == 2010        # new directory tier
