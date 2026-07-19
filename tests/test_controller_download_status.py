from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.services.acquire import AcquiredFile, AcquireResult


def _ctrl(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))
    return AppController(ctx), ctx


async def test_run_download_partial_when_a_picked_file_has_no_link(tmp_path, monkeypatch):
    ctrl, ctx = _ctrl(tmp_path)

    class FakeClient:
        async def torrent_info(self, tid):
            from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
            return RdTorrentInfo(
                id=tid, filename="Bk", status="downloaded", links=["L1"],
                files=[
                    RdTorrentFile(id=1, path="/Bk/a.mp3", selected=True),
                    RdTorrentFile(id=2, path="/Bk/b.mp3", selected=True),
                ],
            )
        async def aclose(self):
            pass

    async def fake_download_torrent(client, torrent, dest_root, **kwargs):
        # 2 picked, only 1 lands
        (dest_root).mkdir(parents=True, exist_ok=True)
        return AcquireResult(folder=dest_root, files=[
            AcquiredFile(filename="a.mp3", path=None, ok=True),
            AcquiredFile(filename="b.mp3", path=None, ok=False, error="boom"),
        ])

    monkeypatch.setattr("colophon.controller.download_torrent", fake_download_torrent)
    monkeypatch.setattr(ctrl, "rd_client", lambda: FakeClient())

    await ctrl.rd_download("t1", name="Bk", file_ids=[1, 2], dest_dir=tmp_path / "dl")
    entry = ctrl.active_downloads()[0]
    assert entry.files_total == 2   # Y from metadata, fixed
    assert entry.files_done == 1
    assert entry.status == "partial"
    ctx.close()
