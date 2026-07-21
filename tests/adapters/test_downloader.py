import httpx
import pytest

from colophon.adapters.downloader import _STREAM_TIMEOUT, DownloadCancelled, stream_download
from colophon.core.cancel import CancelToken


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_default_stream_client_has_a_bounded_read_timeout():
    # A stalled Real-Debrid CDN with no timeout wedges the download slot forever; the default
    # stream client must cap connect + read so a stall raises instead of hanging.
    assert _STREAM_TIMEOUT.read is not None and _STREAM_TIMEOUT.read > 0
    assert _STREAM_TIMEOUT.connect is not None and _STREAM_TIMEOUT.connect > 0


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
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope")))
    dest = tmp_path / "x.mp3"

    with pytest.raises(httpx.HTTPStatusError):
        await stream_download("http://dl/x.mp3", dest, client=client)
    assert not dest.exists()


async def test_resume_sends_range_and_appends(tmp_path):
    dest = tmp_path / "f.bin"
    (tmp_path / "f.bin.part").write_bytes(b"AAA")

    def handler(request):
        assert request.headers.get("range") == "bytes=3-"
        return httpx.Response(206, content=b"BBB", headers={"content-length": "3"})

    await stream_download("http://x/f", dest, client=_client(handler))
    assert dest.read_bytes() == b"AAABBB"


async def test_range_ignored_restarts_from_zero(tmp_path):
    dest = tmp_path / "f.bin"
    (tmp_path / "f.bin.part").write_bytes(b"AAA")

    def handler(request):
        return httpx.Response(200, content=b"XYZW", headers={"content-length": "4"})

    await stream_download("http://x/f", dest, client=_client(handler))
    assert dest.read_bytes() == b"XYZW"


async def test_cancel_mid_stream_keeps_part(tmp_path):
    dest = tmp_path / "f.bin"
    token = CancelToken()
    seen = []

    def _progress(done, total):
        seen.append(done)
        token.cancel()  # cancel after the first chunk

    def handler(request):
        return httpx.Response(200, content=b"Z" * 200000, headers={"content-length": "200000"})

    with pytest.raises(DownloadCancelled):
        await stream_download("http://x/f", dest, client=_client(handler),
                              progress=_progress, cancel=token)
    assert not dest.exists()
    assert (tmp_path / "f.bin.part").exists()
