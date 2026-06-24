import httpx
import pytest

from colophon.adapters.realdebrid import (
    RdTorrent,
    RdTorrentInfo,
    RdUnrestrictedLink,
    RdUser,
    RealDebridClient,
    RealDebridError,
)


def _client(handler, token="tok"):
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RealDebridClient(token, client=mock)


async def test_user_parses_and_sends_bearer():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/1.0/user"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"id": 1, "username": "alex", "premium": 100})

    user = await _client(handler).user()
    assert isinstance(user, RdUser)
    assert user.username == "alex"
    assert user.premium == 100


async def test_list_torrents_filters_nothing_and_passes_limit():
    body = [
        {"id": "a", "filename": "Mistborn", "bytes": 5, "status": "downloaded",
         "progress": 100.0, "links": ["http://rd/1"]},
        {"id": "b", "filename": "movie", "bytes": 9, "status": "magnet_error",
         "progress": 0.0, "links": []},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/1.0/torrents"
        assert request.url.params["limit"] == "100"
        return httpx.Response(200, json=body)

    torrents = await _client(handler).list_torrents()
    assert [t.id for t in torrents] == ["a", "b"]
    assert isinstance(torrents[0], RdTorrent)
    assert torrents[0].links == ["http://rd/1"]


async def test_torrent_info_parses_files():
    body = {
        "id": "a", "filename": "Mistborn", "bytes": 5, "status": "downloaded",
        "progress": 100.0, "links": ["http://rd/1"],
        "files": [{"id": 1, "path": "/Mistborn/01.mp3", "bytes": 3, "selected": True}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/1.0/torrents/info/a"
        return httpx.Response(200, json=body)

    info = await _client(handler).torrent_info("a")
    assert isinstance(info, RdTorrentInfo)
    assert info.files[0].path == "/Mistborn/01.mp3"
    assert info.files[0].selected is True


async def test_unrestrict_link_posts_form_and_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/1.0/unrestrict/link"
        assert b"link=http" in request.content
        return httpx.Response(200, json={
            "filename": "01.mp3", "filesize": 3, "mimeType": "audio/mpeg",
            "download": "http://dl/01.mp3",
        })

    out = await _client(handler).unrestrict_link("http://rd/1")
    assert isinstance(out, RdUnrestrictedLink)
    assert out.download == "http://dl/01.mp3"
    assert out.mime_type == "audio/mpeg"


async def test_api_error_raises_realdebrid_error():
    client = _client(lambda req: httpx.Response(401, text="bad token"))
    with pytest.raises(RealDebridError) as exc:
        await client.user()
    assert exc.value.status_code == 401


async def test_add_magnet_returns_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/torrents/addMagnet")
        assert b"magnet" in request.content
        return httpx.Response(201, json={"id": "ABC123", "uri": "x"})

    client = _client(handler)
    assert await client.add_magnet("magnet:?xt=urn:btih:deadbeef") == "ABC123"


async def test_select_files_posts_ids():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(204)

    client = _client(handler)
    await client.select_files("ABC123", "1,3")
    assert captured["path"].endswith("/torrents/selectFiles/ABC123")
    assert "files=1%2C3" in captured["body"] or "files=1,3" in captured["body"]
