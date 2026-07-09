from pathlib import Path

import httpx

from colophon.adapters.cover import (
    CoverImage,
    ext_for_mime,
    fetch_cover,
    mime_for_suffix,
)


def test_mime_for_suffix():
    assert mime_for_suffix(Path("c.png")) == "image/png"
    assert mime_for_suffix(Path("c.PNG")) == "image/png"
    assert mime_for_suffix(Path("c.jpg")) == "image/jpeg"
    assert mime_for_suffix(Path("c.jpeg")) == "image/jpeg"


def test_ext_for_mime():
    assert ext_for_mime("image/png") == ".png"
    assert ext_for_mime("image/jpeg") == ".jpg"


def test_mime_ext_round_trip():
    assert mime_for_suffix(Path("c" + ext_for_mime("image/png"))) == "image/png"
    assert mime_for_suffix(Path("c" + ext_for_mime("image/jpeg"))) == "image/jpeg"

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9f0000000049454e44ae426082"
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_returns_bytes_and_mime_from_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/png"})

    cover = await fetch_cover("https://covers.example/x.png", client=_client(handler))
    assert cover == CoverImage(data=_PNG, mime="image/png")


async def test_fetch_falls_back_to_url_extension_for_mime():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_PNG)

    cover = await fetch_cover("https://covers.example/x.jpg", client=_client(handler))
    assert cover is not None and cover.mime == "image/jpeg"


async def test_fetch_follows_redirects():
    # OpenLibrary's cover endpoint 302s to an archive.org URL; we must follow it.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "covers.example":
            return httpx.Response(302, headers={"location": "https://archive.example/olcovers/476272-L.jpg"})
        return httpx.Response(200, content=_PNG, headers={"content-type": "image/jpeg"})

    cover = await fetch_cover("https://covers.example/b/id/476272-L.jpg", client=_client(handler))
    assert cover == CoverImage(data=_PNG, mime="image/jpeg")


async def test_fetch_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cover = await fetch_cover("https://covers.example/missing.jpg", client=_client(handler))
    assert cover is None
