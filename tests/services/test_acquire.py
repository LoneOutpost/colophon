import asyncio
from pathlib import Path

from colophon.adapters.realdebrid import (
    RdTorrent,
    RdTorrentFile,
    RdTorrentInfo,
    RdUnrestrictedLink,
)
from colophon.services.acquire import (
    AcquireResult,
    _resolve_links,
    align_links_to_files,
    download_target_count,
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

    async def unrestrict_link(self, link, *, force: bool = False):
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


async def test_list_candidates_uploading_is_ready_and_pickable():
    # RD moves a finished torrent through "uploading" (copying to its own hosts) *after* the
    # file list and links are populated, so it is already retrievable. It must be pickable, not
    # hidden behind a "still preparing" state.
    torrents = [RdTorrent(id="u", filename="Mistborn", status="uploading", progress=100.0)]
    infos = {
        "u": RdTorrentInfo(id="u", filename="Mistborn", status="uploading", files=[
            RdTorrentFile(id=1, path="/Mistborn/01.mp3"),
            RdTorrentFile(id=2, path="/Mistborn/cover.jpg"),
        ]),
    }
    cands = await list_candidates(FakeRd(torrents=torrents, infos=infos))
    assert len(cands) == 1
    assert cands[0].is_ready is True
    assert cands[0].is_audiobook is True
    assert [f.path for f in cands[0].audio_files] == ["/Mistborn/01.mp3"]


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


async def test_download_mismatch_duplicate_basename_disambiguated_by_size(tmp_path, monkeypatch):
    # Two editions both contain '01_Night_Shift.mp3' in different subfolders. A count mismatch (a
    # selected file with no link) forces the fallback; basename alone can't tell them apart, but
    # (basename + filesize) can — each lands in its own subfolder, not flattened to the root.
    torrent = RdTorrentInfo(
        id="a", filename="SK", status="downloaded", links=["L1", "L2"],
        files=[
            RdTorrentFile(id=1, path="/SK/Night Shift (AFB)/01_Night_Shift.mp3", bytes=1000, selected=True),
            RdTorrentFile(id=2, path="/SK/Night Shift (NLS)/01_Night_Shift.mp3", bytes=2000, selected=True),
            RdTorrentFile(id=3, path="/SK/extra.mp3", bytes=50, selected=True),  # selected, no link
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="01_Night_Shift.mp3", filesize=1000, download="http://dl/afb"),
        "L2": RdUnrestrictedLink(filename="01_Night_Shift.mp3", filesize=2000, download="http://dl/nls"),
    }

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    c = result.folder
    assert (c / "Night Shift (AFB)" / "01_Night_Shift.mp3").exists()
    assert (c / "Night Shift (NLS)" / "01_Night_Shift.mp3").exists()
    assert not (c / "01_Night_Shift.mp3").exists()  # not flattened to the container root


async def test_download_mismatch_recovers_structure_by_order_when_sizes_disagree(tmp_path, monkeypatch):
    # The real-world failure: two editions share every track basename in different subfolders, and
    # RD's unrestrict `filesize` does NOT equal the torrent-info `bytes` (two independent RD
    # endpoints — sizes agree only intermittently). A count mismatch forces the fallback. Matching
    # on (basename, size) then misses for every file, so the old code flattened them all to the
    # container root and same-named tracks clobbered each other. RD returns links as a clean
    # in-order subsequence of the selected files, so each link must be mapped to its file by
    # POSITION — recovering the per-edition structure without trusting the sizes.
    torrent = RdTorrentInfo(
        id="a", filename="SK", status="downloaded", links=["L1", "L2", "L3", "L4"],
        files=[
            RdTorrentFile(id=1, path="/SK/Gunslinger (original)/1_ Gunslinger.mp3", bytes=100, selected=True),
            RdTorrentFile(id=2, path="/SK/Gunslinger (original)/2_ Waystation.mp3", bytes=200, selected=True),
            RdTorrentFile(id=3, path="/SK/Gunslinger (read by King)/1_ Gunslinger.mp3", bytes=300, selected=True),
            RdTorrentFile(id=4, path="/SK/Gunslinger (read by King)/2_ Waystation.mp3", bytes=400, selected=True),
            RdTorrentFile(id=5, path="/SK/extra.mp3", bytes=50, selected=True),  # selected, no link -> mismatch
        ],
    )
    # unrestrict filesizes are all off-by-one from bytes, so (basename, size) can never match.
    links = {
        "L1": RdUnrestrictedLink(filename="1_ Gunslinger.mp3", filesize=101, download="http://dl/o1"),
        "L2": RdUnrestrictedLink(filename="2_ Waystation.mp3", filesize=201, download="http://dl/o2"),
        "L3": RdUnrestrictedLink(filename="1_ Gunslinger.mp3", filesize=301, download="http://dl/k1"),
        "L4": RdUnrestrictedLink(filename="2_ Waystation.mp3", filesize=401, download="http://dl/k2"),
    }
    got = {}

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")
        got[url] = Path(dest)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    c = result.folder
    # Every track lands in its own edition's subfolder, mapped by link order — nothing flattened.
    assert got["http://dl/o1"] == c / "Gunslinger (original)" / "1_ Gunslinger.mp3"
    assert got["http://dl/o2"] == c / "Gunslinger (original)" / "2_ Waystation.mp3"
    assert got["http://dl/k1"] == c / "Gunslinger (read by King)" / "1_ Gunslinger.mp3"
    assert got["http://dl/k2"] == c / "Gunslinger (read by King)" / "2_ Waystation.mp3"
    assert not (c / "1_ Gunslinger.mp3").exists()  # not flattened / clobbered at the root
    assert not (c / "2_ Waystation.mp3").exists()
    assert sum(1 for f in result.files if f.ok) == 4


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


def test_structured_dests_top_folder_differing_from_torrent_is_kept(tmp_path):
    from colophon.services.acquire import structured_dests
    # The internal top folder differs from the torrent name, so it is real structure: keep it under
    # the torrent-named container rather than stripping it (which used to flatten pinned picks).
    container, dests = structured_dests(
        ["Mistborn/Disc 1/01.mp3", "Mistborn/Disc 2/01.mp3"], tmp_path, "torrent-name")
    assert container == tmp_path / "torrent-name"
    assert dests == [
        container / "Mistborn" / "Disc 1" / "01.mp3",
        container / "Mistborn" / "Disc 2" / "01.mp3",
    ]


def test_structured_dests_strips_top_folder_that_duplicates_torrent_name(tmp_path):
    from colophon.services.acquire import structured_dests
    # When the single top folder IS the torrent name, drop it so we don't nest name/name/....
    container, dests = structured_dests(
        ["Bundle/Book A/01.mp3", "Bundle/Book A/02.mp3"], tmp_path, "Bundle")
    assert container == tmp_path / "Bundle"
    assert dests == [container / "Book A" / "01.mp3", container / "Book A" / "02.mp3"]


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
    # The picked subfolder is preserved under the pinned container (not flattened away).
    assert dests == [pinned / "Mistborn" / "Disc 1" / "01.mp3"]


def test_structured_dests_pinned_single_folder_pick_keeps_subfolder(tmp_path):
    # Regression: picking one subfolder of a big torrent into its pinned (torrent-named) container
    # must keep that subfolder — not flatten depth-2 files into the container root.
    from colophon.services.acquire import structured_dests
    pinned = tmp_path / "TE_Audiobooks_S-2"
    container, dests = structured_dests(
        ["STEPHEN KING/1408.mp3", "STEPHEN KING/Blaze.mp3"],
        tmp_path, "TE_Audiobooks_S", pinned=pinned)
    assert container == pinned
    assert dests == [pinned / "STEPHEN KING" / "1408.mp3", pinned / "STEPHEN KING" / "Blaze.mp3"]


def test_structured_dests_sanitizes_components(tmp_path):
    from colophon.services.acquire import structured_dests
    # Top folder "X" differs from torrent name "n", so it is kept under the "n" container; every
    # path component is sanitized.
    container, dests = structured_dests(["/X/a:b.mp3", "/X/c?d.mp3"], tmp_path, "n")
    assert container == tmp_path / "n"
    assert dests == [container / "X" / "a_b.mp3", container / "X" / "c_d.mp3"]


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


async def test_add_mode_skips_existing_and_reuses_base(tmp_path, monkeypatch):
    from colophon.services.acquire import AcquireMode

    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded",
                        links=["L_mp3", "L_jpg"])
    links = {
        "L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3"),
        "L_jpg": RdUnrestrictedLink(filename="cover.jpg", download="http://dl/cover.jpg"),
    }
    base = tmp_path / "Mistborn"
    base.mkdir()
    (base / "01.mp3").write_bytes(b"STALE")

    downloaded = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"FRESH")
        downloaded.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, mode=AcquireMode.ADD)

    assert result.folder == base
    assert (base / "01.mp3").read_bytes() == b"STALE"
    assert "http://dl/01.mp3" not in downloaded
    assert (base / "cover.jpg").read_bytes() == b"FRESH"
    skipped = [f for f in result.files if f.skipped]
    assert [f.filename for f in skipped] == ["01.mp3"]


async def test_overwrite_mode_replaces_existing_and_stale_part(tmp_path, monkeypatch):
    from colophon.services.acquire import AcquireMode

    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded", links=["L_mp3"])
    links = {"L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3")}
    base = tmp_path / "Mistborn"
    base.mkdir()
    (base / "01.mp3").write_bytes(b"STALE")
    (base / "01.mp3.part").write_bytes(b"leftover")

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        assert not Path(str(dest) + ".part").exists()
        Path(dest).write_bytes(b"FRESH")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, mode=AcquireMode.OVERWRITE)

    assert result.folder == base
    assert (base / "01.mp3").read_bytes() == b"FRESH"
    assert not (base / "01.mp3.part").exists()


async def test_indexed_mode_unchanged_allocates_new_folder(tmp_path, monkeypatch):
    from colophon.services.acquire import AcquireMode

    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded", links=["L_mp3"])
    links = {"L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3")}
    (tmp_path / "Mistborn").mkdir()

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"data")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, mode=AcquireMode.INDEXED)

    assert result.folder == tmp_path / "Mistborn-2"


async def test_add_mode_resumes_a_partial_not_yet_complete(tmp_path, monkeypatch):
    from colophon.services.acquire import AcquireMode

    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded", links=["L_mp3"])
    links = {"L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3")}
    base = tmp_path / "Mistborn"
    base.mkdir()
    # An interrupted prior attempt: only a .part exists (no final file). ADD must NOT skip;
    # it re-invokes stream_download (which, in real life, resumes the .part via Range).
    (base / "01.mp3.part").write_bytes(b"half")

    fetched = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        assert Path(str(dest) + ".part").exists()  # the partial is still there to resume
        Path(dest).write_bytes(b"FRESH")
        fetched.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, mode=AcquireMode.ADD)

    assert fetched == ["http://dl/01.mp3"]           # resumed, not skipped
    assert [f for f in result.files if f.skipped] == []
    assert (base / "01.mp3").read_bytes() == b"FRESH"


async def test_overwrite_mode_leaves_unrelated_files_alone(tmp_path, monkeypatch):
    from colophon.services.acquire import AcquireMode

    torrent = RdTorrent(id="a", filename="Mistborn", status="downloaded", links=["L_mp3"])
    links = {"L_mp3": RdUnrestrictedLink(filename="01.mp3", download="http://dl/01.mp3")}
    base = tmp_path / "Mistborn"
    base.mkdir()
    (base / "01.mp3").write_bytes(b"STALE")
    (base / "notes.txt").write_bytes(b"keep me")  # unrelated file this download never touches

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"FRESH")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    await download_torrent(FakeRd(links=links), torrent, tmp_path, mode=AcquireMode.OVERWRITE)

    assert (base / "01.mp3").read_bytes() == b"FRESH"
    assert (base / "notes.txt").read_bytes() == b"keep me"  # untouched


async def test_download_single_archive_link_reports_clear_reason(tmp_path, monkeypatch):
    # RD packed a many-file torrent into ONE archive link (e.g. a 500GB .rar): the single link
    # is the archive, not any of the selected files, so a per-file pick matches nothing. We
    # fail with a clear note instead of a bare failure, and never fetch the giant archive.
    torrent = RdTorrentInfo(
        id="a", filename="TE_Audiobooks_A", status="downloaded", links=["L_rar"],
        files=[
            RdTorrentFile(id=1, path="/TE/A Cleeves/Raven Black/01.mp3", selected=True),
            RdTorrentFile(id=2, path="/TE/A Cleeves/Raven Black/02.mp3", selected=True),
            RdTorrentFile(id=3, path="/TE/A Milne/Pooh/01.mp3", selected=True),
        ],
    )
    # the lone link resolves to the archive, whose name matches none of the picked files
    links = {"L_rar": RdUnrestrictedLink(filename="TE_Audiobooks_A.rar", download="http://dl/rar")}
    called = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        called.append(url)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path, file_ids={1})
    assert result.any_ok is False
    assert called == []                       # never streamed the 500GB archive
    assert result.note and "archive" in result.note.lower()


async def test_download_single_file_torrent_still_downloads(tmp_path, monkeypatch):
    # Guard: one link for one selected file is legitimate (counts match) and must not be
    # mistaken for an archive.
    torrent = RdTorrentInfo(
        id="a", filename="Solo", status="downloaded", links=["L1"],
        files=[RdTorrentFile(id=1, path="/Solo/01.mp3", selected=True)],
    )
    links = {"L1": RdUnrestrictedLink(filename="01.mp3", download="http://dl/1")}

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(FakeRd(links=links), torrent, tmp_path)
    assert result.any_ok is True
    assert result.note is None


def test_visible_candidates_hides_errored_by_default_shows_under_show_all():
    from colophon.services.acquire import AcquireCandidate, visible_candidates
    book = AcquireCandidate(
        torrent=RdTorrentInfo(id="a", filename="Bk", status="downloaded",
                              files=[RdTorrentFile(id=1, path="/Bk/01.mp3")]),
        audio_files=[RdTorrentFile(id=1, path="/Bk/01.mp3")], total_files=1, is_ready=True)
    errored = AcquireCandidate(
        torrent=RdTorrent(id="b", filename="Vid", status="error"),
        audio_files=[], total_files=0, is_ready=False)
    inprogress = AcquireCandidate(
        torrent=RdTorrent(id="c", filename="Pending", status="downloading"),
        audio_files=[], total_files=0, is_ready=False)

    assert errored.is_errored is True and book.is_errored is False
    default = {c.torrent.id for c in visible_candidates([book, errored, inprogress], show_all=False)}
    assert default == {"a", "c"}   # errored hidden; audiobook + in-progress shown
    everything = {c.torrent.id for c in visible_candidates([book, errored, inprogress], show_all=True)}
    assert everything == {"a", "b", "c"}   # Show all reveals errored too


def test_align_closest_size_when_earlier_link_missing():
    # Two same-name files; the FIRST has no link (a gap). The single link's size is
    # closest to the SECOND, so closest-size must pick index 1, not position-0.
    files = [("01.mp3", 100), ("01.mp3", 900)]
    links = [("01.mp3", 905)]  # off-by-5 from the second file
    assert align_links_to_files(files, links) == [1]


def test_align_position_when_size_unusable():
    # filesize 0 (unknown) => ignore size, fall back to first-by-position.
    files = [("01.mp3", 100), ("01.mp3", 900)]
    links = [("01.mp3", 0)]
    assert align_links_to_files(files, links) == [0]


async def test_download_self_heals_stale_link(tmp_path, monkeypatch):
    # The cached URL's stream fails; the file self-heals by re-unrestricting (force=True)
    # and streaming the fresh URL.
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1"],
        files=[RdTorrentFile(id=1, path="/T/01.mp3", bytes=10, selected=True)])

    class Rd:
        def __init__(self):
            self.forced = []

        async def unrestrict_link(self, link, *, force=False):
            if force:
                self.forced.append(link)
                return RdUnrestrictedLink(filename="01.mp3", filesize=10, download="http://fresh/1")
            return RdUnrestrictedLink(filename="01.mp3", filesize=10, download="http://stale/1")

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        if "stale" in url:
            raise RuntimeError("410 gone")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    rd = Rd()
    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(rd, torrent, tmp_path)
    assert rd.forced == ["L1"]                                  # re-unrestricted fresh
    assert sum(1 for f in result.files if f.ok) == 1            # landed after self-heal
    assert (result.folder / "01.mp3").exists()


async def test_download_records_failure_when_self_heal_also_fails(tmp_path, monkeypatch):
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1"],
        files=[RdTorrentFile(id=1, path="/T/01.mp3", bytes=10, selected=True)])

    class Rd:
        async def unrestrict_link(self, link, *, force=False):
            return RdUnrestrictedLink(filename="01.mp3", filesize=10, download="http://x/1")

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        raise RuntimeError("boom")  # both attempts fail

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(Rd(), torrent, tmp_path)
    assert result.any_ok is False
    bad = [f for f in result.files if not f.ok]
    assert len(bad) == 1 and "boom" in (bad[0].error or "")


def test_align_exact_still_preferred_over_closer_position():
    files = [("01.mp3", 100), ("01.mp3", 900)]
    links = [("01.mp3", 900)]  # exact match to index 1
    assert align_links_to_files(files, links) == [1]


def test_target_count_subset_is_pick_size():
    info = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1", "L2", "L3"],
        files=[
            RdTorrentFile(id=1, path="/T/a.mp3", selected=True),
            RdTorrentFile(id=2, path="/T/b.mp3", selected=True),
            RdTorrentFile(id=3, path="/T/notes.txt", selected=True),
        ],
    )
    assert download_target_count(info, {1, 2}) == 2


def test_target_count_all_is_audio_plus_cover_keepset():
    info = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1", "L2", "L3", "L4"],
        files=[
            RdTorrentFile(id=1, path="/T/a.mp3", selected=True),
            RdTorrentFile(id=2, path="/T/cover.jpg", selected=True),
            RdTorrentFile(id=3, path="/T/notes.txt", selected=True),  # dropped by _keep_file
            RdTorrentFile(id=4, path="/T/b.mp3", selected=False),      # not selected
        ],
    )
    assert download_target_count(info, None) == 2  # a.mp3 + cover.jpg


def test_target_count_no_file_list_falls_back_to_links():
    info = RdTorrentInfo(id="a", filename="T", status="downloaded", links=["L1", "L2"], files=[])
    assert download_target_count(info, None) == 2


async def test_resolve_links_preserves_order_and_reports_progress():
    links = ["L1", "L2", "L3"]
    resolved = {
        "L1": RdUnrestrictedLink(filename="1.mp3", filesize=1, download="http://d/1"),
        "L2": RdUnrestrictedLink(filename="2.mp3", filesize=2, download="http://d/2"),
        "L3": RdUnrestrictedLink(filename="3.mp3", filesize=3, download="http://d/3"),
    }

    class Rd:
        async def unrestrict_link(self, link):
            if link == "L2":
                raise RuntimeError("boom")  # isolated failure -> None slot
            return resolved[link]

    seen: list[tuple[int, int]] = []
    out = await _resolve_links(
        Rd(), links, sem=asyncio.Semaphore(2), cancel=None,
        on_progress=lambda done, total: seen.append((done, total)),
    )
    assert [u.filename if u else None for u in out] == ["1.mp3", None, "3.mp3"]
    assert seen[-1] == (3, 3)  # final progress reports all links accounted for


async def test_download_reports_resolve_then_download_phases(tmp_path, monkeypatch):
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1", "L2"],
        files=[
            RdTorrentFile(id=1, path="/T/Disc 1/01.mp3", bytes=10, selected=True),
            RdTorrentFile(id=2, path="/T/Disc 2/01.mp3", bytes=20, selected=True),
            RdTorrentFile(id=3, path="/T/extra.mp3", bytes=5, selected=True),  # no link -> mismatch
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="01.mp3", filesize=10, download="http://dl/1"),
        "L2": RdUnrestrictedLink(filename="01.mp3", filesize=20, download="http://dl/2"),
    }

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    phases: list[tuple[str, int, int]] = []
    result = await download_torrent(
        FakeRd(links=links), torrent, tmp_path,
        progress=lambda phase, done, total, name: phases.append((phase, done, total)),
    )
    c = result.folder
    assert (c / "Disc 1" / "01.mp3").exists()
    assert (c / "Disc 2" / "01.mp3").exists()          # structured, not flattened
    kinds = [p for p, _, _ in phases]
    assert "resolving" in kinds and "downloading" in kinds
    assert kinds.index("resolving") < kinds.index("downloading")  # resolve reported first
    assert phases[-1][1] == phases[-1][2] == 2          # downloading ended 2/2 (Y = 2 picked)


async def test_subset_pick_early_stops_resolving(tmp_path, monkeypatch):
    # 100 selected files but only 99 links (mismatch forces the fallback). The user picks an
    # EARLY file (id=3). Early-stop must resolve only a small prefix — nowhere near all 99 —
    # while still placing the picked file. Concurrency is bounded, so a few extra links beyond
    # the pick may resolve, but it must be far below the total.
    files = [RdTorrentFile(id=i, path=f"/T/{i:03d}.mp3", bytes=i * 1000, selected=True)
             for i in range(1, 101)]
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded",
        links=[f"L{i}" for i in range(1, 100)], files=files)  # 99 links, 100 selected
    unrestricted: list[str] = []

    class Rd:
        async def unrestrict_link(self, link):
            unrestricted.append(link)
            i = int(link[1:])
            return RdUnrestrictedLink(filename=f"{i:03d}.mp3", filesize=i * 1000, download=f"http://d/{i}")

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(Rd(), torrent, tmp_path, file_ids={3})
    assert (result.folder / "003.mp3").exists()          # the picked file landed
    assert len(unrestricted) < 30                          # stopped early (<< 99)


async def test_download_all_still_resolves_every_link(tmp_path, monkeypatch):
    # No file_ids => download-all => NO early stop; every link is resolved.
    files = [RdTorrentFile(id=i, path=f"/T/{i:02d}.mp3", bytes=i * 1000, selected=True)
             for i in range(1, 6)]
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded",
        links=[f"L{i}" for i in range(1, 5)], files=files)  # 4 links, 5 selected
    unrestricted: list[str] = []

    class Rd:
        async def unrestrict_link(self, link):
            unrestricted.append(link)
            i = int(link[1:])
            return RdUnrestrictedLink(filename=f"{i:02d}.mp3", filesize=i * 1000, download=f"http://d/{i}")

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    await download_torrent(Rd(), torrent, tmp_path, file_ids=None)
    assert len(unrestricted) == 4  # all links resolved


async def test_download_all_pairs_path_skips_non_audio_and_counts_match(tmp_path, monkeypatch):
    # Contract-held pairs path (links == selected). download-all (file_ids=None) must keep only
    # audio+cover, matching download_target_count, so the "Downloading a/Y" denominator is
    # consistent (no "Downloading 3/2"). An explicit pick still downloads exactly what was chosen.
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1", "L2", "L3"],
        files=[
            RdTorrentFile(id=1, path="/T/01.mp3", bytes=10, selected=True),
            RdTorrentFile(id=2, path="/T/cover.jpg", bytes=5, selected=True),
            RdTorrentFile(id=3, path="/T/info.nfo", bytes=1, selected=True),  # non-audio -> dropped
        ],
    )
    links = {
        "L1": RdUnrestrictedLink(filename="01.mp3", filesize=10, download="http://dl/1"),
        "L2": RdUnrestrictedLink(filename="cover.jpg", filesize=5, download="http://dl/2"),
        "L3": RdUnrestrictedLink(filename="info.nfo", filesize=1, download="http://dl/3"),
    }
    got = []

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")
        got.append(Path(dest).name)

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    phases: list[tuple[str, int, int]] = []
    await download_torrent(FakeRd(links=links), torrent, tmp_path,
                           progress=lambda ph, d, t, n: phases.append((ph, d, t)))
    assert set(got) == {"01.mp3", "cover.jpg"}          # non-audio .nfo dropped on download-all
    dl_totals = [t for ph, _, t in phases if ph == "downloading"]
    assert dl_totals and all(t == 2 for t in dl_totals)  # denominator == keep-count
    assert download_target_count(torrent, None) == 2      # and matches the metadata count


async def test_download_records_unresolvable_link_as_retryable_failure(tmp_path, monkeypatch):
    # A link that fails to unrestrict (e.g. throttle-exhausted after retries) must be RECORDED as a
    # failed file, not silently dropped — so it's visible and a resume can try it again.
    torrent = RdTorrentInfo(
        id="a", filename="T", status="downloaded", links=["L1", "L2"],
        files=[RdTorrentFile(id=1, path="/T/01.mp3", bytes=10, selected=True),
               RdTorrentFile(id=2, path="/T/02.mp3", bytes=20, selected=True)])

    class Rd:
        async def unrestrict_link(self, link, *, force=False):
            if link == "L2":
                raise RuntimeError("429 exhausted")
            return RdUnrestrictedLink(filename="01.mp3", filesize=10, download="http://d/1")

    async def fake_stream(url, dest, *, progress=None, cancel=None, client=None):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"ok")

    monkeypatch.setattr("colophon.services.acquire.stream_download", fake_stream)
    result = await download_torrent(Rd(), torrent, tmp_path)
    ok = [f for f in result.files if f.ok]
    bad = [f for f in result.files if not f.ok]
    assert len(ok) == 1                      # 01.mp3 landed
    assert len(bad) == 1                     # 02.mp3's failed link is recorded, not dropped
    assert bad[0].filename == "02.mp3"
    assert "retry" in (bad[0].error or "").lower()
