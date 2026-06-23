from pathlib import Path

from colophon.core.models import BookUnit, SourceFile
from colophon.services.encode import EncodeResult, encode_book


def _book_from(paths_durations: list[tuple[Path, float]]) -> BookUnit:
    b = BookUnit.new(source_folder=paths_durations[0][0].parent)
    b.title = "Test Book"
    b.source_files = [
        SourceFile(path=p, size=p.stat().st_size, duration_seconds=d, ext=p.suffix.lstrip("."))
        for p, d in paths_durations
    ]
    return b


def test_encode_book_produces_verified_m4b(make_audio, tmp_path):
    a = make_audio("01.mp3", seconds=1)
    b = make_audio("02.mp3", seconds=1)
    book = _book_from([(a, 1.0), (b, 1.0)])
    out = tmp_path / "out" / "book.m4b"

    result = encode_book(book, out, bitrate="64k")

    assert isinstance(result, EncodeResult)
    assert result.verified is True
    assert result.output_path == out and out.exists()
    assert result.deleted_sources is False  # delete not requested
    assert a.exists() and b.exists()        # originals untouched


def test_encode_book_deletes_sources_only_when_confirmed(make_audio, tmp_path):
    a = make_audio("01.mp3", seconds=1)
    book = _book_from([(a, 1.0)])
    out = tmp_path / "single.m4b"

    result = encode_book(book, out, bitrate="64k", delete_sources=True, confirm_delete=True)

    assert result.verified is True
    assert result.deleted_sources is True
    assert not a.exists()  # deleted after verified success


def test_encode_book_keeps_sources_when_not_confirmed(make_audio, tmp_path):
    a = make_audio("01.mp3", seconds=1)
    book = _book_from([(a, 1.0)])
    out = tmp_path / "single.m4b"
    # delete requested but not confirmed -> must NOT delete
    result = encode_book(book, out, bitrate="64k", delete_sources=True, confirm_delete=False)
    assert result.deleted_sources is False
    assert a.exists()


def test_encode_book_with_no_sources_returns_unverified(tmp_path):
    book = BookUnit.new(source_folder=tmp_path)
    result = encode_book(book, tmp_path / "x.m4b", bitrate="64k")
    assert result.verified is False
    assert result.error is not None


def test_encode_book_unverified_when_duration_mismatch(make_audio, tmp_path):
    a = make_audio("real.mp3", seconds=1)
    book = _book_from([(a, 1.0)])
    # Lie about the duration so expected (~100s) diverges wildly from the real (~1s).
    book.source_files[0].duration_seconds = 100.0
    out = tmp_path / "out" / "mismatch.m4b"

    result = encode_book(book, out, bitrate="64k")

    assert result.verified is False
    assert result.error is not None
    assert not out.exists()  # bad artifact cleaned up


def test_encode_book_remuxes_single_aac(make_audio, tmp_path):
    a = make_audio("solo.m4a", seconds=1)  # codec aac
    book = _book_from([(a, 1.0)])
    out = tmp_path / "out" / "solo.m4b"

    result = encode_book(book, out, bitrate="64k")

    assert result.verified is True
    assert out.exists()


def test_encode_book_produces_untagged_output(make_audio, tmp_path):
    from colophon.adapters.tags import read_embedded_tags

    a = make_audio("a.mp3", seconds=2)
    book = _book_from([(a, 2.0)])
    book.title = "Tagged Title"
    book.authors = ["An Author"]
    out = tmp_path / "out.m4b"

    result = encode_book(book, out, bitrate="64k")

    assert result.verified and result.output_path == out
    assert read_embedded_tags(out).title != "Tagged Title"
