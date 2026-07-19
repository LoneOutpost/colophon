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
