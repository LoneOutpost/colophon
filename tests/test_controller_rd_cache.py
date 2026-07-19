from colophon.adapters.config import Config
from colophon.adapters.realdebrid_cache import CachingRealDebridSource
from colophon.app_context import AppContext
from colophon.controller import AppController


def test_rd_client_is_cache_backed(tmp_path):
    cfg = Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
                 real_debrid_token="tok")
    ctx = AppContext.create(cfg)
    ctrl = AppController(ctx)
    client = ctrl.rd_client()
    assert isinstance(client, CachingRealDebridSource)
    assert client.cache is ctx.rd_cache   # shares the persistent repo
    ctx.close()


async def test_rd_refresh_cache_forces_refetch(tmp_path, monkeypatch):
    cfg = Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
                 real_debrid_token="tok")
    ctx = AppContext.create(cfg)
    ctrl = AppController(ctx)

    from colophon.adapters.realdebrid import RdTorrent, RdTorrentInfo
    calls = {"info": 0}

    class FakeClient:
        async def list_torrents(self, limit=100):
            return [RdTorrent(id="t1", filename="Bk", status="downloaded")]
        async def torrent_info(self, tid, *, force=False):
            calls["info"] += 1
            assert force is True  # refresh must bypass cache
            return RdTorrentInfo(id=tid, filename="Bk", status="downloaded", links=[], files=[])
        async def aclose(self):
            pass

    from colophon.adapters.realdebrid_cache import CachingRealDebridSource
    monkeypatch.setattr(ctrl, "rd_client", lambda: CachingRealDebridSource(FakeClient(), ctx.rd_cache))

    await ctrl.rd_refresh_cache()
    assert calls["info"] == 1
    assert ctx.rd_cache.get_torrent_info("t1") is not None  # repopulated
    ctx.close()


async def test_second_download_of_same_torrent_makes_zero_new_api_calls(tmp_path, monkeypatch):
    # The whole point of the cache: pick a torrent's files once, and a later download of the
    # same torrent resolves entirely from cache (no repeat /unrestrict/link calls to RD).
    from pathlib import Path

    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo, RdUnrestrictedLink
    from colophon.adapters.realdebrid_cache import CachingRealDebridSource
    from colophon.adapters.repository import RdCacheRepo, connect, migrate
    from colophon.services.acquire import download_torrent

    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    cache = RdCacheRepo(conn)
    torrent = RdTorrentInfo(
        id="t1", filename="Bk", status="downloaded", links=["L1", "L2"],
        files=[RdTorrentFile(id=1, path="/Bk/01.mp3", bytes=10, selected=True),
               RdTorrentFile(id=2, path="/Bk/02.mp3", bytes=20, selected=True)])

    class Inner:
        def __init__(self):
            self.unrestrict_calls = 0

        async def unrestrict_link(self, link, *, force=False):
            self.unrestrict_calls += 1
            i = link[-1]
            return RdUnrestrictedLink(filename=f"0{i}.mp3", filesize=int(i) * 10, download=f"http://d/{link}")

        async def aclose(self):
            pass

    inner = Inner()
    client = CachingRealDebridSource(inner, cache)

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)

    r1 = await download_torrent(client, torrent, tmp_path / "a")
    assert sum(1 for f in r1.files if f.ok) == 2
    assert inner.unrestrict_calls == 2          # first run resolves both links live

    r2 = await download_torrent(client, torrent, tmp_path / "b")
    assert sum(1 for f in r2.files if f.ok) == 2
    assert inner.unrestrict_calls == 2          # second run: ZERO new API calls (all cache hits)
