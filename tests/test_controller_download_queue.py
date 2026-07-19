import asyncio

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.services.acquire import AcquireResult


def _ctrl(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))
    return AppController(ctx), ctx


async def test_third_download_is_queued_until_a_slot_frees(tmp_path, monkeypatch):
    ctrl, ctx = _ctrl(tmp_path)
    gate = asyncio.Event()

    class FakeClient:
        async def torrent_info(self, tid):
            from colophon.adapters.realdebrid import RdTorrentInfo
            return RdTorrentInfo(id=tid, filename=tid, status="downloaded", links=[], files=[])
        async def aclose(self):
            pass

    async def fake_download_torrent(client, torrent, dest_root, **kwargs):
        await gate.wait()  # hold the slot until released
        return AcquireResult(folder=dest_root)

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download_torrent)
    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())

    t1 = asyncio.create_task(ctrl.rd_download("t1", name="t1", dest_dir=tmp_path))
    t2 = asyncio.create_task(ctrl.rd_download("t2", name="t2", dest_dir=tmp_path))
    t3 = asyncio.create_task(ctrl.rd_download("t3", name="t3", dest_dir=tmp_path))
    await asyncio.sleep(0.05)  # let the two slots fill and the third queue
    statuses = sorted(e.status for e in ctrl.active_downloads())
    assert statuses == ["active", "active", "queued"]
    gate.set()  # release all
    await asyncio.gather(t1, t2, t3)
    ctx.close()
