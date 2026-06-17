import httpx

from colophon.adapters.cover import CoverImage, fetch_cover

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


async def test_fetch_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cover = await fetch_cover("https://covers.example/missing.jpg", client=_client(handler))
    assert cover is None
