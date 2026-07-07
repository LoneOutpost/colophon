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


async def test_list_candidates_ready_classifies_audio_inprogress_shown():
    torrents = [
        RdTorrent(id="a", filename="Mistborn", status="downloaded"),
        RdTorrent(id="b", filename="pending", status="downloading", progress=42.0),
    ]
    infos = {
        "a": RdTorrentInfo(id="a", filename="Mistborn", status="downloaded", files=[
            RdTorrentFile(id=1, path="/Mistborn/01.mp3"),
            RdTorrentFile(id=2, path="/Mistborn/cover.jpg"),
        ]),
    }
    cands = await list_candidates(FakeRd(torrents=torrents, infos=infos))
    by_id = {c.torrent.id: c for c in cands}
    # ready one: full file info + audio classification
    assert by_id["a"].is_ready is True
    assert [f.path for f in by_id["a"].audio_files] == ["/Mistborn/01.mp3"]
    assert by_id["a"].total_files == 2 and by_id["a"].is_audiobook is True
    # in-progress one: shown, no files, carries status/progress
    assert by_id["b"].is_ready is False
    assert by_id["b"].total_files == 0
    assert by_id["b"].torrent.progress == 42.0


async def test_list_candidates_isolates_info_failure():
    class Boom(FakeRd):
        async def torrent_info(self, torrent_id):
            raise RuntimeError("rd down")

    torrents = [RdTorrent(id="a", filename="X", status="downloaded")]
    cands = await list_candidates(Boom(torrents=torrents))
    # Failure isolated (not raised): the torrent is surfaced as not-ready (no file list),
    # not silently dropped.
    assert len(cands) == 1
    assert cands[0].is_ready is False and cands[0].total_files == 0


async def test_download_torrent_keeps_audio_and_cover_skips_other(tmp_path, monkeypatch):
    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded",
                        links=["L_mp3", "L_jpg", "L_nfo"])
    links = {
        "L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3"),
        "L_jpg": RdUnrestrictedLink(filename="cover.jpg", download="http://dl/cover.jpg"),
        "L_nfo": RdUnrestrictedLink(filename="info.nfo", download="http://dl/info.nfo"),
    }
    downloaded = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"data")
        downloaded.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)

    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    assert isinstance(result, AcquireResult)
    names = sorted(f.name for f in result.folder.iterdir())
    assert names == ["01.mp3", "cover.jpg"]  # .nfo skipped, never downloaded
    assert "http://dl/info.nfo" not in downloaded
    assert result.any_ok is True


async def test_download_torrent_preserves_structure_no_collision(tmp_path, monkeypatch):
    torrent = RdTorrentInfo(
        id="a", filename="Expanse", status="downloaded", links=["L1", "L2"],
        files=[
            RdTorrentFile(id=1, path="/Expanse/Disc 1/01.mp3", selected=True),
            RdTorrentFile(id=2, path="/Expanse/Disc 2/01.mp3", selected=True),
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/d1"),
        "L2": RdUnrestrictedLink(filename="01.mp3", download="http://dl/d2"),
    }

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    assert (result.folder / "Disc 1" / "01.mp3").exists()
    assert (result.folder / "Disc 2" / "01.mp3").exists()  # no clobber despite same basename
    assert sum(1 for f in result.files if f.ok) == 2


async def test_download_preserves_structure_when_links_fewer_than_selected(tmp_path, monkeypatch):
    # RD's real quirk: it returns FEWER links than selected files (e.g. 1638 vs 4440), so the
    # index map can't be trusted. Structure must still be recovered by matching each link's
    # unrestricted filename to a selected file's path — not flattened.
    torrent = RdTorrentInfo(
        id="a", filename="Collection", status="downloaded", links=["L1", "L2"],
        files=[
            RdTorrentFile(id=1, path="/Collection/Blake Crouch/Lightless.mp3", selected=True),
            RdTorrentFile(id=2, path="/Collection/Blake Crouch/Supernova.mp3", selected=True),
            RdTorrentFile(id=3, path="/Collection/Other/Third.mp3", selected=True),  # no link
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="Lightless.mp3", download="http://dl/1"),
        "L2": RdUnrestrictedLink(filename="Supernova.mp3", download="http://dl/2"),
    }

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    assert (result.folder / "Blake Crouch" / "Lightless.mp3").exists()   # subfolder preserved
    assert (result.folder / "Blake Crouch" / "Supernova.mp3").exists()
    assert not (result.folder / "Lightless.mp3").exists()               # not flattened
    assert sum(1 for f in result.files if f.ok) == 2


async def test_download_mismatch_subset_preserves_structure(tmp_path, monkeypatch):
    # Same quirk, but the user picked a subset (file_ids): only the picked files download, and
    # they keep their structure.
    torrent = RdTorrentInfo(
        id="a", filename="Coll", status="downloaded", links=["L1", "L2", "L3"],
        files=[
            RdTorrentFile(id=1, path="/Coll/A/one.mp3", selected=True),
            RdTorrentFile(id=2, path="/Coll/B/two.mp3", selected=True),
            RdTorrentFile(id=3, path="/Coll/C/three.mp3", selected=True),
            RdTorrentFile(id=4, path="/Coll/D/four.mp3", selected=True),  # selected but no link
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="one.mp3", download="http://dl/1"),
        "L2": RdUnrestrictedLink(filename="two.mp3", download="http://dl/2"),
        "L3": RdUnrestrictedLink(filename="three.mp3", download="http://dl/3"),
    }
    got = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")
        got.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, file_ids={2})
    assert got == ["http://dl/2"]                                  # only the picked file
    assert (result.folder / "B" / "two.mp3").exists()             # structure preserved
    assert not (result.folder / "A" / "one.mp3").exists()


async def test_download_torrent_isolates_per_file_failure(tmp_path, monkeypatch):
    torrent = RdTorrent(id="a", filename="Bk", status="downloaded", links=["L1", "L2"])
    links = {
        "L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3"),
        "L2": RdUnrestrictedLink(filename="02.mp3", download="http://dl/02.mp3"),
    }

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
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


async def test_download_torrent_removes_empty_folder_on_total_failure(tmp_path, monkeypatch):
    torrent = RdTorrent(id="a", filename="Bk", status="downloaded", links=["L1"])
    links = {"L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3")}

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    assert result.any_ok is False
    assert not result.folder.exists()  # empty staging dir cleaned up


async def test_download_torrent_reuses_pinned_folder_for_resume(tmp_path, monkeypatch):
    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded", links=["L1"])
    links = {"L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3")}

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)

    pinned = tmp_path / "existing-folder"
    pinned.mkdir()
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, folder=pinned)
    assert result.folder == pinned  # no new deduped dir; the .part of an interrupted run resumes here
    assert (pinned / "01.mp3").exists()


def test_structured_dests_names_container_after_torrent_top(tmp_path):
    from colophon.services.acquire import structured_dests
    container, dests = structured_dests(
        ["Mistborn/Disc 1/01.mp3", "Mistborn/Disc 2/01.mp3"], tmp_path, "torrent-name")
    assert container == tmp_path / "Mistborn"  # the torrent's own top folder, not torrent-name
    assert dests == [container / "Disc 1" / "01.mp3", container / "Disc 2" / "01.mp3"]


def test_structured_dests_wraps_bare_files_in_torrent_name(tmp_path):
    from colophon.services.acquire import structured_dests
    container, dests = structured_dests(["01.mp3", "02.mp3"], tmp_path, "Bundle")
    assert container == tmp_path / "Bundle"
    assert dests == [container / "01.mp3", container / "02.mp3"]


def test_structured_dests_distinct_top_dirs_wrap_and_keep_tree(tmp_path):
    from colophon.services.acquire import structured_dests
    # No shared top -> wrap in torrent name, keep each file's full path (no collision).
    container, dests = structured_dests(["A/01.mp3", "B/01.mp3"], tmp_path, "Bundle")
    assert container == tmp_path / "Bundle"
    assert dests == [container / "A" / "01.mp3", container / "B" / "01.mp3"]


def test_structured_dests_subset_keeps_full_tree(tmp_path):
    from colophon.services.acquire import structured_dests
    container, dests = structured_dests(
        ["Bundle/Book A/01.mp3", "Bundle/Book A/02.mp3"], tmp_path, "Bundle")
    assert container == tmp_path / "Bundle"
    assert dests == [container / "Book A" / "01.mp3", container / "Book A" / "02.mp3"]


def test_structured_dests_honors_pinned_container(tmp_path):
    from colophon.services.acquire import structured_dests
    pinned = tmp_path / "already"
    container, dests = structured_dests(["Mistborn/Disc 1/01.mp3"], tmp_path, "n", pinned=pinned)
    assert container == pinned
    assert dests == [pinned / "Disc 1" / "01.mp3"]


def test_structured_dests_sanitizes_components(tmp_path):
    from colophon.services.acquire import structured_dests
    container, dests = structured_dests(["/X/a:b.mp3", "/X/c?d.mp3"], tmp_path, "n")
    assert container == tmp_path / "X"
    assert dests == [container / "a_b.mp3", container / "c_d.mp3"]


def test_plan_pairs_maps_links_to_selected_paths():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import plan_pairs
    t = RdTorrentInfo(id="a", links=["L1", "L2"], files=[
        RdTorrentFile(id=1, path="/A/01.mp3", selected=True),
        RdTorrentFile(id=2, path="/A/02.mp3", selected=True),
    ])
    pairs, keep = plan_pairs(t, None)
    assert pairs == [("/A/01.mp3", "L1"), ("/A/02.mp3", "L2")]
    assert keep is None


def test_plan_pairs_subset_keeps_only_chosen():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import plan_pairs
    t = RdTorrentInfo(id="a", links=["L1", "L2", "L3"], files=[
        RdTorrentFile(id=1, path="/A/01.mp3", selected=True),
        RdTorrentFile(id=2, path="/B/01.mp3", selected=True),
        RdTorrentFile(id=3, path="/C/01.mp3", selected=True),
    ])
    pairs, keep = plan_pairs(t, {2})
    assert pairs == [("/B/01.mp3", "L2")]
    assert keep is None


def test_plan_pairs_falls_back_on_count_mismatch():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import plan_pairs
    t = RdTorrentInfo(id="a", links=["L1"], files=[
        RdTorrentFile(id=1, path="/A/keep.mp3", selected=True),
        RdTorrentFile(id=2, path="/A/skip.mp3", selected=True),
    ])
    pairs, keep = plan_pairs(t, {1})
    assert pairs is None
    assert keep == {"keep.mp3"}


def test_plan_pairs_no_files_is_flat_fallback():
    from colophon.adapters.realdebrid import RdTorrent
    from colophon.services.acquire import plan_pairs
    t = RdTorrent(id="a", links=["L1", "L2"])  # no files list
    pairs, keep = plan_pairs(t, None)
    assert pairs is None and keep is None


def test_sanitize_name_strips_separators():
    assert sanitize_name("a/b:c?.mp3") == "a_b_c_.mp3"
    assert sanitize_name("   ...   ") == "download"


def test_sanitize_name_clamps_length_and_keeps_extension():
    out = sanitize_name("x" * 500 + ".mp3")
    assert len(out) <= 200
    assert out.endswith(".mp3")


async def test_add_torrent_selects_all_by_default():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import add_torrent

    selected = {}

    class FakeRd:
        async def add_magnet(self, magnet):
            return "TID"
        async def torrent_info(self, tid):
            return RdTorrentInfo(id="TID", files=[
                RdTorrentFile(id=1, path="book.mp3"), RdTorrentFile(id=2, path="cover.jpg"),
            ])
        async def select_files(self, tid, file_ids):
            selected["ids"] = file_ids

    assert await add_torrent(FakeRd(), "magnet:?x") == "TID"
    assert selected["ids"] == "all"  # prepare everything so the picker sees all files


async def test_add_torrent_audio_only_selects_audio_ids():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import add_torrent

    selected = {}

    class FakeRd:
        async def add_magnet(self, magnet):
            return "TID"
        async def torrent_info(self, tid):
            return RdTorrentInfo(id="TID", files=[
                RdTorrentFile(id=1, path="book.mp3"), RdTorrentFile(id=2, path="cover.jpg"),
                RdTorrentFile(id=3, path="part2.m4b"),
            ])
        async def select_files(self, tid, file_ids):
            selected["ids"] = file_ids

    await add_torrent(FakeRd(), "magnet:?x", audio_only=True)
    assert selected["ids"] == "1,3"  # audio files only


async def test_add_torrent_file_uploads_and_selects_all_by_default():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import add_torrent_file

    calls = {}

    class FakeRd:
        async def add_torrent_file(self, content):
            calls["content"] = content
            return "TID"
        async def torrent_info(self, tid):
            return RdTorrentInfo(id="TID", files=[RdTorrentFile(id=1, path="book.mp3")])
        async def select_files(self, tid, file_ids):
            calls["ids"] = file_ids

    assert await add_torrent_file(FakeRd(), b"d8:announce...") == "TID"
    assert calls["content"] == b"d8:announce..."  # raw bytes forwarded
    assert calls["ids"] == "all"


async def test_add_torrent_file_audio_only_selects_audio_ids():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import add_torrent_file

    calls = {}

    class FakeRd:
        async def add_torrent_file(self, content):
            return "TID"
        async def torrent_info(self, tid):
            return RdTorrentInfo(id="TID", files=[
                RdTorrentFile(id=1, path="book.mp3"), RdTorrentFile(id=2, path="cover.jpg"),
                RdTorrentFile(id=3, path="part2.m4b"),
            ])
        async def select_files(self, tid, file_ids):
            calls["ids"] = file_ids

    await add_torrent_file(FakeRd(), b"data", audio_only=True)
    assert calls["ids"] == "1,3"


async def test_add_torrent_audio_only_falls_back_to_all_when_no_audio():
    from colophon.adapters.realdebrid import RdTorrentFile, RdTorrentInfo
    from colophon.services.acquire import add_torrent

    selected = {}

    class FakeRd:
        async def add_magnet(self, magnet):
            return "TID"
        async def torrent_info(self, tid):
            return RdTorrentInfo(id="TID", files=[RdTorrentFile(id=1, path="readme.txt")])
        async def select_files(self, tid, file_ids):
            selected["ids"] = file_ids

    await add_torrent(FakeRd(), "magnet:?x", audio_only=True)
    assert selected["ids"] == "all"


async def test_file_ids_downloads_only_chosen_by_index(tmp_path, monkeypatch):
    torrent = RdTorrentInfo(
        id="a", filename="Bundle", status="downloaded", links=["L1", "L2", "L3"],
        files=[
            RdTorrentFile(id=1, path="/A/01.mp3", selected=True),
            RdTorrentFile(id=2, path="/B/01.mp3", selected=True),
            RdTorrentFile(id=3, path="/C/01.mp3", selected=True),
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/A"),
        "L2": RdUnrestrictedLink(filename="01.mp3", download="http://dl/B"),
        "L3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/C"),
    }
    got = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"ok")
        got.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, file_ids={2})
    assert got == ["http://dl/B"]  # id 2 -> selected[1] -> L2
    assert result.any_ok is True


async def test_file_ids_includes_non_audio(tmp_path, monkeypatch):
    torrent = RdTorrentInfo(
        id="a", filename="X", status="downloaded", links=["L1"],
        files=[RdTorrentFile(id=1, path="/A/notes.pdf", selected=True)],
    )
    links = {"L1": RdUnrestrictedLink(filename="notes.pdf", download="http://dl/pdf")}
    got = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"ok")
        got.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    await download_torrent(FakeRd(links=links), torrent, tmp_path, file_ids={1})
    assert got == ["http://dl/pdf"]  # explicit pick bypasses the audio-only filter


async def test_file_ids_count_mismatch_falls_back_to_filename(tmp_path, monkeypatch):
    # 1 link but 2 selected files -> index map can't be trusted; fall back to name match
    torrent = RdTorrentInfo(
        id="a", filename="X", status="downloaded", links=["L1"],
        files=[
            RdTorrentFile(id=1, path="/A/keep.mp3", selected=True),
            RdTorrentFile(id=2, path="/A/skip.mp3", selected=True),
        ],
    )
    links = {"L1": RdUnrestrictedLink(filename="keep.mp3", download="http://dl/keep")}
    got = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"ok")
        got.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    await download_torrent(FakeRd(links=links), torrent, tmp_path, file_ids={1})
    assert got == ["http://dl/keep"]  # fetched all links, kept only keep.mp3 by name


def test_container_for_indexed_dedups_but_base_reuses(tmp_path):
    from colophon.services.acquire import AcquireMode, _container_for

    (tmp_path / "Book").mkdir()  # base already exists
    assert _container_for(tmp_path, "Book", AcquireMode.INDEXED) == tmp_path / "Book-2"
    assert _container_for(tmp_path, "Book", AcquireMode.ADD) == tmp_path / "Book"
    assert _container_for(tmp_path, "Book", AcquireMode.OVERWRITE) == tmp_path / "Book"


def test_acquired_file_has_skipped_default_false(tmp_path):
    from colophon.services.acquire import AcquiredFile

    f = AcquiredFile(filename="x", path=tmp_path / "x", ok=True)
    assert f.skipped is False
