from colophon.adapters.realdebrid import (
    RdTorrent,
    RdTorrentInfo,
    RdUnrestrictedLink,
)
from colophon.adapters.realdebrid_cache import CachingRealDebridSource
from colophon.adapters.repository import RdCacheRepo, connect, migrate


def _cache(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return RdCacheRepo(conn)


class FakeInner:
    def __init__(self):
        self.info_calls = 0
        self.unrestrict_calls = 0
        self.torrents = []

    async def list_torrents(self, limit=100):
        return self.torrents

    async def torrent_info(self, tid, *, force=False):
        self.info_calls += 1
        status = "downloaded" if tid != "prog" else "downloading"
        return RdTorrentInfo(id=tid, filename=tid, status=status, links=["L1"], files=[])

    async def unrestrict_link(self, link, *, force=False):
        self.unrestrict_calls += 1
        return RdUnrestrictedLink(filename="a.mp3", filesize=5, download=f"http://d/{link}")

    async def aclose(self):
        pass


async def test_torrent_info_caches_ready_and_serves_from_cache(tmp_path):
    inner = FakeInner()
    src = CachingRealDebridSource(inner, _cache(tmp_path))
    a = await src.torrent_info("t1")
    b = await src.torrent_info("t1")   # second call served from cache
    assert a.id == b.id == "t1"
    assert inner.info_calls == 1        # only one inner fetch


async def test_torrent_info_does_not_cache_in_progress(tmp_path):
    inner = FakeInner()
    src = CachingRealDebridSource(inner, _cache(tmp_path))
    await src.torrent_info("prog")
    await src.torrent_info("prog")
    assert inner.info_calls == 2        # in-progress always fetched live


async def test_force_bypasses_cache(tmp_path):
    inner = FakeInner()
    src = CachingRealDebridSource(inner, _cache(tmp_path))
    await src.torrent_info("t1")
    await src.torrent_info("t1", force=True)
    assert inner.info_calls == 2


async def test_unrestrict_caches(tmp_path):
    inner = FakeInner()
    src = CachingRealDebridSource(inner, _cache(tmp_path))
    await src.unrestrict_link("L1")
    await src.unrestrict_link("L1")
    assert inner.unrestrict_calls == 1


async def test_list_torrents_evicts_removed(tmp_path):
    inner = FakeInner()
    cache = _cache(tmp_path)
    src = CachingRealDebridSource(inner, cache)
    await src.torrent_info("t1")                       # cache t1
    inner.torrents = [RdTorrent(id="t2", filename="t2", status="downloaded")]  # t1 gone from RD
    await src.list_torrents()
    assert cache.get_torrent_info("t1") is None         # pruned


async def test_force_recaches_torrent_info(tmp_path):
    inner = FakeInner()
    cache = _cache(tmp_path)
    src = CachingRealDebridSource(inner, cache)
    await src.torrent_info("t1")
    cache.evict_torrent("t1")                       # cache emptied out-of-band
    await src.torrent_info("t1", force=True)         # force fetch must re-populate
    assert cache.get_torrent_info("t1") is not None


async def test_unrestrict_force_bypasses_and_recaches(tmp_path):
    inner = FakeInner()
    src = CachingRealDebridSource(inner, _cache(tmp_path))
    await src.unrestrict_link("L1")
    await src.unrestrict_link("L1", force=True)       # force must bypass the cache read
    assert inner.unrestrict_calls == 2
