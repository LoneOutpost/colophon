from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo, RdUnrestrictedLink
from colophon.adapters.repository import RdCacheRepo, connect, migrate


def _repo(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return RdCacheRepo(conn)


def test_torrent_info_round_trip(tmp_path):
    repo = _repo(tmp_path)
    info = RdTorrentInfo(
        id="t1", filename="Bk", status="downloaded", links=["L1", "L2"],
        files=[RdTorrentFile(id=1, path="/Bk/01.mp3", bytes=10, selected=True)])
    assert repo.get_torrent_info("t1") is None
    repo.put_torrent_info(info)
    got = repo.get_torrent_info("t1")
    assert got is not None and got.filename == "Bk" and got.links == ["L1", "L2"]
    assert got.files[0].path == "/Bk/01.mp3"


def test_link_round_trip(tmp_path):
    repo = _repo(tmp_path)
    unr = RdUnrestrictedLink(filename="01.mp3", filesize=10, mime_type="audio/mpeg", download="http://d/1")
    assert repo.get_link("L1") is None
    repo.put_link("L1", unr)
    got = repo.get_link("L1")
    assert got is not None and got.download == "http://d/1" and got.filesize == 10


def test_evict_torrent_drops_info_and_its_links(tmp_path):
    repo = _repo(tmp_path)
    repo.put_torrent_info(RdTorrentInfo(id="t1", filename="Bk", status="downloaded", links=["L1", "L2"]))
    repo.put_link("L1", RdUnrestrictedLink(filename="a", download="http://d/a"))
    repo.put_link("L2", RdUnrestrictedLink(filename="b", download="http://d/b"))
    repo.put_link("Lx", RdUnrestrictedLink(filename="x", download="http://d/x"))  # unrelated
    repo.evict_torrent("t1")
    assert repo.get_torrent_info("t1") is None
    assert repo.get_link("L1") is None and repo.get_link("L2") is None
    assert repo.get_link("Lx") is not None  # untouched


def test_evict_torrents_prunes_to_keep_set(tmp_path):
    repo = _repo(tmp_path)
    repo.put_torrent_info(RdTorrentInfo(id="t1", filename="A", status="downloaded", links=["L1"]))
    repo.put_torrent_info(RdTorrentInfo(id="t2", filename="B", status="downloaded", links=["L2"]))
    repo.put_link("L1", RdUnrestrictedLink(filename="a", download="http://d/a"))
    repo.put_link("L2", RdUnrestrictedLink(filename="b", download="http://d/b"))
    repo.evict_torrents({"t2"})  # t1 gone from RD
    assert repo.get_torrent_info("t1") is None and repo.get_link("L1") is None
    assert repo.get_torrent_info("t2") is not None and repo.get_link("L2") is not None
