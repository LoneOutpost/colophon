from pathlib import Path

from colophon.adapters.lazylibrarian import AudiobookPatterns, read_audiobook_patterns


def _write_ini(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.ini"
    p.write_text(body)
    return p


def test_reads_postprocess_patterns(tmp_path: Path):
    ini = _write_ini(
        tmp_path,
        "[POSTPROCESS]\n"
        "audiobook_dest_folder = $Author/$Series/$Title\n"
        "audiobook_dest_file = $Author - $Title Part $Part of $Total\n"
        "audiobook_single_file = $Title\n",
    )
    pats = read_audiobook_patterns(ini)
    assert pats.folder == "$Author/$Series/$Title"
    assert pats.single_file == "$Title"


def test_missing_keys_use_defaults(tmp_path: Path):
    ini = _write_ini(tmp_path, "[POSTPROCESS]\n")
    pats = read_audiobook_patterns(ini)
    assert pats.folder == "$Author/$Title"
    assert pats.single_file == ""


def test_missing_file_returns_defaults(tmp_path: Path):
    pats = read_audiobook_patterns(tmp_path / "absent.ini")
    assert pats == AudiobookPatterns()
    assert pats.folder == "$Author/$Title"
