from pathlib import Path

from colophon.adapters.tags import write_embedded_tags
from colophon.core.models import BookUnit, EmbeddedTags, SeriesRef, SourceFile
from colophon.services.tag_ops import plan_tag


def _book_with_file(path: Path) -> BookUnit:
    book = BookUnit.new(source_folder=path.parent)
    book.title = "Mistborn"
    book.authors = ["Brandon Sanderson"]
    book.series = [SeriesRef(name="Mistborn", sequence=1.0)]
    book.source_files = [SourceFile(path=path, size=1, duration_seconds=60.0, ext="mp3")]
    return book


def test_plan_lists_changed_fields_against_current_file_tags(tmp_path: Path):
    f = tmp_path / "01.mp3"
    f.write_bytes(b"")
    write_embedded_tags(f, EmbeddedTags(title="Old Title", artist="Brandon Sanderson"))
    plan = plan_tag(_book_with_file(f))
    assert plan.book_id and plan.title == "Mistborn"
    fp = plan.files[0]
    assert "title" in fp.changed_fields
    assert "artist" not in fp.changed_fields
    assert "series" in fp.changed_fields


def test_plan_surfaces_validation_warnings(tmp_path: Path):
    f = tmp_path / "01.mp3"
    f.write_bytes(b"")
    book = _book_with_file(f)
    book.title = None
    plan = plan_tag(book)
    assert any("title" in w.lower() for w in plan.warnings)
