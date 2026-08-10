"""Tag cache (Slice A): SourceFile.tags populated by SEARCH, consumed by CATEGORIZE/IDENTIFY."""
from colophon.core.dirinfer import parse_scheme
from colophon.core.filename_parser import compile_template
from colophon.core.models import BookUnit, EmbeddedTags, Phase, SourceFile
from colophon.services.ingest import run_local_phases


def _args(tmp_path, template="$Title"):
    return dict(root=tmp_path, pattern=compile_template(template), scheme=parse_scheme(""))


def test_search_populates_cached_tags(tmp_path):
    from colophon.adapters.tags import write_embedded_tags
    d = tmp_path / "Author" / "Book"
    d.mkdir(parents=True)
    f = d / "Book.mp3"
    f.write_bytes(b"")
    write_embedded_tags(f, EmbeddedTags(title="Chapter 1", artist="Brandon Sanderson"))
    book = BookUnit.new(source_folder=d)
    run_local_phases(book, frozenset({Phase.SEARCH}), force=False, unit_files=[f], **_args(tmp_path))
    assert book.source_files[0].tags is not None
    assert book.source_files[0].tags.artist == "Brandon Sanderson"


def test_categorize_uses_cached_tags_without_disk(tmp_path):
    # The file path does not exist on disk; CATEGORIZE must classify from sf.tags without reading it.
    book = BookUnit.new(source_folder=tmp_path / "A" / "B")
    book.source_files = [SourceFile(path=tmp_path / "A" / "B" / "gone.mp3", size=1,
                                    duration_seconds=60.0, ext="mp3",
                                    tags=EmbeddedTags(album="The Cached Book"))]
    run_local_phases(book, frozenset({Phase.CATEGORIZE}), force=True, **_args(tmp_path))
    assert book.detected_works and book.detected_works[0].label == "The Cached Book"


def test_identify_uses_cached_tags_without_disk(tmp_path):
    book = BookUnit.new(source_folder=tmp_path / "A" / "B")
    book.source_files = [SourceFile(path=tmp_path / "A" / "B" / "gone.mp3", size=1,
                                    duration_seconds=60.0, ext="mp3",
                                    tags=EmbeddedTags(title="Real Title", artist="Real Author"))]
    run_local_phases(book, frozenset({Phase.CATEGORIZE, Phase.IDENTIFY}), force=True,
                     **_args(tmp_path, template="$Author - $Title"))
    assert book.title == "Real Title"
    assert book.authors == ["Real Author"]
