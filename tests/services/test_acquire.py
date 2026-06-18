from pathlib import Path

from colophon.adapters.realdebrid import (
    RdTorrent,
    RdTorrentFile,
    RdTorrentInfo,
    RdUnrestrictedLink,
)
from colophon.services.acquire import (
    AcquireResult,
    download_torrent,
    list_candidates,
    sanitize_name,
)


class FakeRd:
    def __init__(self, torrents=None, infos=None, links=None):
        self._torrents = torrents or []
        self._infos = infos or {}
        self._links = links or {}

    async def list_torrents(self, limit=100):
        return self._torrents

    async def torrent_info(self, torrent_id):
        return self._infos[torrent_id]

    async def unrestrict_link(self, link):
        return self._links[link]


async def test_list_candidates_only_ready_and_classifies_audio():
    torrents = [
        RdTorrent(id="a", filename="Mistborn", status="downloaded"),
        RdTorrent(id="b", filename="pending", status="downloading"),
    ]
    infos = {
        "a": RdTorrentInfo(id="a", filename="Mistborn", status="downloaded", files=[
            RdTorrentFile(id=1, path="/Mistborn/01.mp3"),
            RdTorrentFile(id=2, path="/Mistborn/cover.jpg"),
        ]),
    }
    cands = await list_candidates(FakeRd(torrents=torrents, infos=infos))
    assert len(cands) == 1
    c = cands[0]
    assert c.torrent.id == "a"
    assert [f.path for f in c.audio_files] == ["/Mistborn/01.mp3"]
    assert c.total_files == 2
    assert c.is_audiobook is True


async def test_list_candidates_isolates_info_failure():
    class Boom(FakeRd):
        async def torrent_info(self, torrent_id):
            raise RuntimeError("rd down")

    torrents = [RdTorrent(id="a", filename="X", status="downloaded")]
    cands = await list_candidates(Boom(torrents=torrents))
    assert cands == []  # failure isolated, not raised


async def test_download_torrent_keeps_audio_and_cover_skips_other(tmp_path, monkeypatch):
    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded",
                        links=["L_mp3", "L_jpg", "L_nfo"])
    links = {
        "L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3"),
        "L_jpg": RdUnrestrictedLink(filename="cover.jpg", download="http://dl/cover.jpg"),
        "L_nfo": RdUnrestrictedLink(filename="info.nfo", download="http://dl/info.nfo"),
    }
    downloaded = []

    async def fake_stream(url, dest, *, progress=None, client=None):
        Path(dest).write_bytes(b"data")
        downloaded.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)

    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    assert isinstance(result, AcquireResult)
    names = sorted(f.name for f in result.folder.iterdir())
    assert names == ["01.mp3", "cover.jpg"]  # .nfo skipped, never downloaded
    assert "http://dl/info.nfo" not in downloaded
    assert result.any_ok is True


async def test_download_torrent_isolates_per_file_failure(tmp_path, monkeypatch):
    torrent = RdTorrent(id="a", filename="Bk", status="downloaded", links=["L1", "L2"])
    links = {
        "L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3"),
        "L2": RdUnrestrictedLink(filename="02.mp3", download="http://dl/02.mp3"),
    }

    async def fake_stream(url, dest, *, progress=None, client=None):
        if url.endswith("02.mp3"):
            raise RuntimeError("boom")
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    oks = [f for f in result.files if f.ok]
    bad = [f for f in result.files if not f.ok]
    assert len(oks) == 1 and oks[0].filename == "01.mp3"
    assert len(bad) == 1 and bad[0].filename == "02.mp3"
    assert result.any_ok is True


def test_sanitize_name_strips_separators():
    assert sanitize_name("a/b:c?.mp3") == "a_b_c_.mp3"
    assert sanitize_name("   ...   ") == "download"


def test_sanitize_name_clamps_length_and_keeps_extension():
    out = sanitize_name("x" * 500 + ".mp3")
    assert len(out) <= 200
    assert out.endswith(".mp3")
