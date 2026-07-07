"""The controller holds a session-sticky acquire mode and threads it to download_torrent."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.services.acquire import AcquireMode


def _ctrl(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))
    return AppController(ctx), ctx


def test_acquire_mode_defaults_to_indexed(tmp_path):
    ctrl, ctx = _ctrl(tmp_path)
    assert ctrl.acquire_mode is AcquireMode.INDEXED
    ctx.close()


async def test_rd_download_uses_sticky_mode_when_unspecified(tmp_path, monkeypatch):
    ctrl, ctx = _ctrl(tmp_path)
    seen = {}

    async def fake_download_torrent(client, torrent, dest_root, **kwargs):
        from colophon.services.acquire import AcquireResult
        seen["mode"] = kwargs.get("mode")
        return AcquireResult(folder=dest_root)

    class FakeClient:
        async def torrent_info(self, tid):
            from colophon.adapters.realdebrid import RdTorrentInfo
            return RdTorrentInfo(id=tid, filename="X", status="downloaded", files=[])

        async def aclose(self):
            pass

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download_torrent)
    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())

    ctrl.acquire_mode = AcquireMode.OVERWRITE  # sticky selection
    await ctrl.rd_download("t1", name="X", dest_dir=tmp_path)  # no explicit mode
    assert seen["mode"] is AcquireMode.OVERWRITE
    ctx.close()
