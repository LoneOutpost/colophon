from pathlib import Path

import pytest

from colophon.services.foster import foster_work


def test_foster_work_moves_files_into_named_subdir(tmp_path):
    (tmp_path / "01.mp3").write_bytes(b"")
    (tmp_path / "02.mp3").write_bytes(b"")
    dests = foster_work([tmp_path / "01.mp3", tmp_path / "02.mp3"], tmp_path, "The Way of Kings")
    assert (tmp_path / "The Way of Kings" / "01.mp3").is_file()
    assert (tmp_path / "The Way of Kings" / "02.mp3").is_file()
    assert dests == [tmp_path / "The Way of Kings" / "01.mp3",
                     tmp_path / "The Way of Kings" / "02.mp3"]


def test_foster_work_refuses_existing_dir(tmp_path):
    (tmp_path / "Legion").mkdir()
    (tmp_path / "a.mp3").write_bytes(b"")
    with pytest.raises(FileExistsError):
        foster_work([tmp_path / "a.mp3"], tmp_path, "Legion")
