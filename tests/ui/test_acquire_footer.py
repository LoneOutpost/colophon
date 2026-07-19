from colophon.controller import DownloadEntry
from colophon.ui.acquire import download_row_text, downloads_title_text


def test_row_text_resolving():
    e = DownloadEntry(key="k", name="Book", status="active", phase="resolving",
                      links_done=120, links_total=1638, files_total=4)
    assert download_row_text(e) == "Resolving 120/1638 links"


def test_row_text_downloading():
    e = DownloadEntry(key="k", name="Book", status="active", phase="downloading",
                      files_done=1, files_total=4)
    assert download_row_text(e) == "Downloading 1/4"


def test_row_text_queued():
    e = DownloadEntry(key="k", name="Book", status="queued", files_total=4)
    assert download_row_text(e) == "Queued"


def test_row_text_partial():
    e = DownloadEntry(key="k", name="Book", status="partial", files_done=3, files_total=4)
    assert download_row_text(e) == "Partial: 3 of 4 (1 failed)"


def test_title_counts_active_downloading_files_remaining():
    entries = [
        DownloadEntry(key="a", name="A", status="active", phase="downloading",
                      files_done=1, files_total=4),
        DownloadEntry(key="b", name="B", status="queued", files_total=2),  # queued not counted
    ]
    assert downloads_title_text(entries) == "Downloads: 3 file(s) downloading"


def test_title_idle_when_no_active_downloading():
    assert downloads_title_text([]) == "Downloads"
