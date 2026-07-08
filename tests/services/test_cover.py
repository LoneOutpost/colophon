from pathlib import Path

import httpx

from colophon.core.models import BookUnit
from colophon.services.cover import ensure_cached_cover

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9f0000000049454e44ae426082"
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_caches_cover_and_sets_cover_path(tmp_path: Path):
    book = BookUnit.new(source_folder=tmp_path)
    book.cover_url = "https://covers.example/x.png"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    path = await ensure_cached_cover(book, dest_dir=tmp_path, client=_client(handler))
    assert path == tmp_path / f"cover-{book.id}.png"  # per-book name, not a folder-shared "cover.png"
    assert path.read_bytes() == _PNG
    assert book.cover_path == path


async def test_books_sharing_a_folder_get_distinct_cover_paths(tmp_path: Path):
    # Multi-book directory: two clustered books share one source folder but own distinct
    # ids and distinct cover URLs. Caching must key on book identity, not the folder, or
    # they collide on one file and all show the same image.
    a = BookUnit.new(source_folder=tmp_path)
    a.id, a.cover_url = "aaaa000000000001", "https://covers.example/a.png"
    b = BookUnit.new(source_folder=tmp_path)
    b.id, b.cover_url = "bbbb000000000002", "https://covers.example/b.png"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    pa = await ensure_cached_cover(a, dest_dir=tmp_path, client=_client(handler))
    pb = await ensure_cached_cover(b, dest_dir=tmp_path, client=_client(handler))
    assert pa != pb
    assert a.cover_path != b.cover_path


async def test_no_cover_url_returns_none(tmp_path: Path):
    book = BookUnit.new(source_folder=tmp_path)
    path = await ensure_cached_cover(book, dest_dir=tmp_path, client=_client(lambda r: httpx.Response(200)))
    assert path is None
    assert book.cover_path is None


async def test_failed_download_returns_none(tmp_path: Path):
    book = BookUnit.new(source_folder=tmp_path)
    book.cover_url = "https://covers.example/missing.jpg"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    path = await ensure_cached_cover(book, dest_dir=tmp_path, client=_client(handler))
    assert path is None
    assert book.cover_path is None


async def test_disk_write_failure_returns_none(tmp_path: Path):
    # dest_dir's parent is a regular file, so mkdir raises OSError (NotADirectoryError).
    (tmp_path / "blocker").write_bytes(b"x")
    book = BookUnit.new(source_folder=tmp_path)
    book.cover_url = "https://covers.example/x.png"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    path = await ensure_cached_cover(book, dest_dir=tmp_path / "blocker" / "sub", client=_client(handler))
    assert path is None
    assert book.cover_path is None


def _make_image(path: Path, size=(400, 600)) -> None:
    from PIL import Image
    Image.new("RGB", size, (180, 90, 60)).save(path)


def test_thumbnail_downscales_and_caches_beside_source(tmp_path: Path):
    from PIL import Image

    from colophon.services.cover import THUMB_MAX_PX, _thumb_path, thumbnail_bytes
    src = tmp_path / "cover.jpg"
    _make_image(src, (400, 600))
    result = thumbnail_bytes(src)
    assert result is not None
    data, mime = result
    assert mime == "image/jpeg"
    assert _thumb_path(src).exists()
    assert len(data) < src.stat().st_size  # smaller payload than the full cover
    from io import BytesIO
    with Image.open(BytesIO(data)) as im:
        assert max(im.size) <= THUMB_MAX_PX  # longest edge bounded
        assert im.size == (64, 96)           # aspect ratio preserved (400x600 -> 64x96)


def test_thumbnail_regenerates_when_source_is_newer(tmp_path: Path):
    import os

    from colophon.services.cover import _thumb_path, thumbnail_bytes
    src = tmp_path / "cover.jpg"
    _make_image(src, (400, 600))
    thumbnail_bytes(src)
    first_mtime = _thumb_path(src).stat().st_mtime
    # Replace the cover with a newer file; the next call must rebuild the thumb.
    _make_image(src, (300, 300))
    os.utime(src, (first_mtime + 10, first_mtime + 10))
    thumbnail_bytes(src)
    from io import BytesIO

    from PIL import Image
    with Image.open(BytesIO(thumbnail_bytes(src)[0])) as im:
        assert im.size == (96, 96)  # reflects the new square source


def test_thumbnail_missing_source_returns_none(tmp_path: Path):
    from colophon.services.cover import thumbnail_bytes
    assert thumbnail_bytes(tmp_path / "nope.jpg") is None


def test_thumbnail_non_image_returns_none(tmp_path: Path):
    from colophon.services.cover import thumbnail_bytes
    bad = tmp_path / "cover.jpg"
    bad.write_text("not an image")
    assert thumbnail_bytes(bad) is None  # falls back to full cover at the call site
