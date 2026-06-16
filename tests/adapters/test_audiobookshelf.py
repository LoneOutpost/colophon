import httpx
import pytest

from colophon.adapters.audiobookshelf import AbsClient, AbsError, AbsLibrary


def _client(handler) -> AbsClient:
    transport = httpx.MockTransport(handler)
    return AbsClient(
        base_url="http://abs.local",
        token="tok",
        client=httpx.AsyncClient(transport=transport, base_url="http://abs.local",
                                 headers={"Authorization": "Bearer tok"}),
    )


async def test_ping_true_on_success():
    src = _client(lambda req: httpx.Response(200, json={"success": True}))
    assert await src.ping() is True


async def test_ping_false_on_error():
    src = _client(lambda req: httpx.Response(500, text="down"))
    assert await src.ping() is False


async def test_list_libraries_normalizes():
    def handler(req):
        assert req.url.path == "/api/libraries"
        assert req.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"libraries": [
            {"id": "lib_1", "name": "Audiobooks"},
            {"id": "lib_2", "name": "Podcasts"},
        ]})
    libs = await _client(handler).list_libraries()
    assert libs == [AbsLibrary(id="lib_1", name="Audiobooks"), AbsLibrary(id="lib_2", name="Podcasts")]


async def test_scan_library_posts_and_returns_ok():
    def handler(req):
        assert req.method == "POST"
        assert req.url.path == "/api/libraries/lib_1/scan"
        return httpx.Response(200, text="OK")
    assert await _client(handler).scan_library("lib_1") == "OK"


async def test_scan_library_raises_on_auth_failure():
    src = _client(lambda req: httpx.Response(401, text="unauthorized"))
    with pytest.raises(AbsError):
        await src.scan_library("lib_1")
