from pathlib import Path

from colophon.adapters.repository.store import OperationRepo, connect, migrate
from colophon.adapters.tags import read_embedded_tags, write_embedded_tags
from colophon.core.models import BookUnit, EmbeddedTags, SeriesRef, SourceFile
from colophon.services.tag_ops import commit_tag, plan_tag, revert_tag_batch, tag_file


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


def _ops(tmp_path: Path) -> OperationRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return OperationRepo(conn)


def test_commit_writes_tags_and_logs_prior_values(tmp_path: Path):
    f = tmp_path / "01.mp3"
    f.write_bytes(b"")
    write_embedded_tags(f, EmbeddedTags(title="Old Title"))
    book = _book_with_file(f)
    ops = _ops(tmp_path)
    result = commit_tag(book, operations=ops, batch_id="b1")
    assert result.written == 1 and result.failed == 0
    assert read_embedded_tags(f).title == "Mistborn"
    logged = ops.list_batch("b1")
    assert len(logged) == 1 and logged[0].outcome == "ok"
    assert "Old Title" in (logged[0].before or "")


def test_revert_restores_prior_tags(tmp_path: Path):
    f = tmp_path / "01.mp3"
    f.write_bytes(b"")
    write_embedded_tags(f, EmbeddedTags(title="Old Title"))
    book = _book_with_file(f)
    ops = _ops(tmp_path)
    commit_tag(book, operations=ops, batch_id="b1")
    assert read_embedded_tags(f).title == "Mistborn"
    restored = revert_tag_batch(ops, "b1")
    assert restored == 1
    assert read_embedded_tags(f).title == "Old Title"
    assert ops.latest_batch_id() is None


def test_commit_embeds_cover_when_present(tmp_path: Path):
    from mutagen.id3 import ID3
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f9f0000000049454e44ae426082"
    )
    f = tmp_path / "01.mp3"
    f.write_bytes(b"")
    cover = tmp_path / "cover.png"
    cover.write_bytes(png)
    book = _book_with_file(f)
    book.cover_path = cover
    commit_tag(book, operations=_ops(tmp_path), batch_id="b1")
    apic = ID3(f).getall("APIC")
    assert apic and apic[0].data == png


def test_tag_file_writes_to_a_single_path_and_logs(tmp_path: Path):
    out = tmp_path / "book.mp3"
    out.write_bytes(b"")
    book = _book_with_file(out)
    ops = _ops(tmp_path)
    ok = tag_file(out, book, operations=ops, batch_id="b1")
    assert ok is True
    assert read_embedded_tags(out).title == "Mistborn"
    logged = ops.list_batch("b1")
    assert len(logged) == 1 and logged[0].target == str(out) and logged[0].outcome == "ok"


def test_write_output_metadata_writes_tags(make_audio, tmp_path):
    from colophon.adapters.tags import read_embedded_tags
    from colophon.core.models import BookUnit
    from colophon.services.tag_ops import write_output_metadata
    audio = make_audio("a.mp3", seconds=1)
    book = BookUnit.new(source_folder=tmp_path)
    book.title = "Dune"
    book.authors = ["Frank Herbert"]
    ok = write_output_metadata(book, audio)
    assert ok is True
    tags = read_embedded_tags(audio)
    assert tags.title == "Dune"
    assert tags.artist == "Frank Herbert"


def test_write_output_metadata_embeds_cover_when_present(make_audio, tmp_path):
    from colophon.core.models import BookUnit
    from colophon.services.tag_ops import write_output_metadata
    audio = make_audio("a.mp3", seconds=1)
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"covermock")
    book = BookUnit.new(source_folder=tmp_path)
    book.title = "Dune"
    book.cover_path = cover
    assert write_output_metadata(book, audio) is True


def test_write_output_metadata_returns_false_on_unsupported_target(tmp_path):
    from colophon.core.models import BookUnit
    from colophon.services.tag_ops import write_output_metadata
    book = BookUnit.new(source_folder=tmp_path)
    book.title = "X"
    target = tmp_path / "not_audio.xyz"
    target.write_bytes(b"nope")
    assert write_output_metadata(book, target) is False
