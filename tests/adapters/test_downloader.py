import httpx

from colophon.adapters.downloader import stream_download


async def test_stream_download_writes_file_and_reports_progress(tmp_path):
    payload = b"audiobookbytes" * 10

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://dl/file.mp3"
        return httpx.Response(200, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    seen = []
    dest = tmp_path / "file.mp3"
    await stream_download("http://dl/file.mp3", dest, progress=lambda d, t: seen.append((d, t)), client=client)

    assert dest.read_bytes() == payload
    assert not dest.with_suffix(".mp3.part").exists()  # temp renamed away
    assert seen and seen[-1][0] == len(payload)


async def test_stream_download_raises_on_http_error_and_leaves_no_final(tmp_path):
    import pytest

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope")))
    dest = tmp_path / "x.mp3"

    with pytest.raises(httpx.HTTPStatusError):
        await stream_download("http://dl/x.mp3", dest, client=client)
    assert not dest.exists()
