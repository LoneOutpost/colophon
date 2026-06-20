from pathlib import Path

from colophon.core.models import BookUnit, EmbeddedTags, SeriesRef
from colophon.core.tag_projection import project_tags


def _book(**kw) -> BookUnit:
    book = BookUnit.new(source_folder=Path("/x"))
    for k, v in kw.items():
        setattr(book, k, v)
    return book


def test_projects_all_fields():
    book = _book(
        title="Mistborn", authors=["Brandon Sanderson"],
        narrators=["Michael Kramer", "Kate Reading"],
        series=[SeriesRef(name="Mistborn", sequence=1.0)],
        publish_year=2006, description="Heist with magic.", asin="B002UZMUVK",
    )
    assert project_tags(book) == EmbeddedTags(
        title="Mistborn", album="Mistborn", artist="Brandon Sanderson",
        narrator="Michael Kramer; Kate Reading", series="Mistborn", sequence=1.0,
        year=2006, genre=None, description="Heist with magic.", asin="B002UZMUVK",
    )


def test_empty_lists_and_missing_series_become_none():
    book = _book(title="Untitled")
    tags = project_tags(book)
    assert tags.title == "Untitled"
    assert tags.album == "Untitled"
    assert tags.artist is None
    assert tags.narrator is None
    assert tags.series is None
    assert tags.sequence is None


def test_project_tags_joins_genres(tmp_path):
    from colophon.core.models import BookUnit
    from colophon.core.tag_projection import project_tags
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.genres = ["Fantasy", "Epic"]
    assert project_tags(b).genre == "Fantasy; Epic"


def test_project_tags_genre_none_without_genres(tmp_path):
    from colophon.core.models import BookUnit
    from colophon.core.tag_projection import project_tags
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.tags = ["to-relisten"]
    et = project_tags(b)
    assert et.genre is None
    assert not hasattr(et, "tag")
