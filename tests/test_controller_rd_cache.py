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
