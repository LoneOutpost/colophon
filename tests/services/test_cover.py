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
    assert path == tmp_path / "cover.png"
    assert path.read_bytes() == _PNG
    assert book.cover_path == path


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
