from pathlib import Path

from colophon.adapters.audio import AUDIO_EXTENSIONS, is_audio_file, probe_audio_file


def test_audio_extensions_cover_common_formats():
    assert {".mp3", ".m4a", ".m4b"} <= AUDIO_EXTENSIONS


def test_is_audio_file_is_case_insensitive():
    assert is_audio_file(Path("/x/Chapter.MP3"))
    assert not is_audio_file(Path("/x/cover.jpg"))


def test_probe_returns_source_file_with_metadata(make_audio):
    path = make_audio("01.mp3", seconds=1)
    sf = probe_audio_file(path)
    assert sf.path == path
    assert sf.ext == "mp3"
    assert sf.size > 0
    assert sf.duration_seconds > 0.5  # ~1s of silence


def test_read_audio_metadata_returns_source_file_and_tags(make_audio):
    from mutagen.id3 import ID3, TIT2, TPE1

    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata
    from colophon.adapters.tags import read_embedded_tags

    clear_audio_metadata_cache()
    path = make_audio("01.mp3", seconds=1)
    id3 = ID3(path)
    id3.add(TIT2(encoding=3, text=["Hello"]))
    id3.add(TPE1(encoding=3, text=["Narrator A"]))
    id3.save(path)

    sf, tags = read_audio_metadata(path)
    # SourceFile parity with the old probe_audio_file contract
    assert sf.path == path
    assert sf.ext == "mp3"
    assert sf.size > 0
    assert sf.duration_seconds > 0.5
    # Tags parity with the direct-open reader
    assert tags == read_embedded_tags(path)
    assert tags.title == "Hello"


def test_read_audio_metadata_caches_until_file_changes(make_audio, monkeypatch):
    import os

    from colophon.adapters import audio as audio_mod
    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata

    clear_audio_metadata_cache()
    path = make_audio("01.mp3", seconds=1)

    calls = {"n": 0}
    real = audio_mod.MutagenFile

    def counting(p, *a, **k):
        calls["n"] += 1
        return real(p, *a, **k)

    monkeypatch.setattr(audio_mod, "MutagenFile", counting)

    read_audio_metadata(path)
    read_audio_metadata(path)
    assert calls["n"] == 1  # second read served from cache (same path, mtime, size)

    # Bump mtime -> key changes -> a fresh load.
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    read_audio_metadata(path)
    assert calls["n"] == 2


def test_probe_audio_file_delegates_to_reader(make_audio):
    path = make_audio("01.mp3", seconds=1)
    sf = probe_audio_file(path)
    assert sf.path == path and sf.ext == "mp3" and sf.size > 0 and sf.duration_seconds > 0.5


def test_ffprobe_fallback_recovers_duration_when_mutagen_reads_zero(make_audio, monkeypatch):
    # A header-less-but-real file: mutagen can't sync to a frame (returns None), so duration falls
    # back to ffprobe, which decodes the stream directly.
    from colophon.adapters import audio as audio_mod
    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata

    path = make_audio("real.mp3", seconds=1)
    clear_audio_metadata_cache()
    monkeypatch.setattr(audio_mod, "MutagenFile", lambda *a, **k: None)  # simulate mutagen failure

    sf, _ = read_audio_metadata(path)
    assert sf.size > 0
    assert sf.duration_seconds > 0.5  # recovered via the ffprobe fallback


def test_empty_file_reads_zero_duration_without_crashing(tmp_path):
    # A nonempty file with no audio (zero-filled placeholder): mutagen and ffprobe both fail; the
    # reader returns a real size with 0 duration rather than raising.
    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata

    p = tmp_path / "placeholder.mp3"
    p.write_bytes(b"\x00" * 8192)
    clear_audio_metadata_cache()

    sf, _ = read_audio_metadata(p)
    assert sf.size == 8192
    assert sf.duration_seconds == 0.0


def test_ffprobe_skipped_for_zero_filled_file(tmp_path, monkeypatch):
    # A zero-filled placeholder (mutagen reads 0) must NOT trigger an ffprobe probe — there's no
    # audio to recover, and probing reads several wasted MB. The gate short-circuits to 0.
    from colophon.adapters import audio as audio_mod
    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata

    p = tmp_path / "placeholder.mp3"
    p.write_bytes(b"\x00" * (256 * 1024))
    clear_audio_metadata_cache()
    called = {"n": 0}
    def _boom(_path):
        called["n"] += 1
        raise AssertionError("ffprobe should not run on a zero-filled file")
    monkeypatch.setattr(audio_mod, "probe_duration_seconds", _boom)

    sf, _ = read_audio_metadata(p)
    assert sf.duration_seconds == 0.0
    assert called["n"] == 0  # ffprobe was gated out


def test_read_audio_metadata_captures_quality(make_audio):
    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata

    clear_audio_metadata_cache()
    path = make_audio("q.mp3", seconds=1)  # ffmpeg: 22050 Hz mono mp3
    sf, _ = read_audio_metadata(path)
    assert sf.sample_rate == 22050
    assert sf.channels == 1
    assert sf.codec == "MP3"
    assert sf.bitrate > 0


def test_ffprobe_still_runs_for_a_file_with_data(tmp_path, monkeypatch):
    # A file with real bytes that mutagen can't sync still gets the ffprobe fallback.
    from colophon.adapters import audio as audio_mod
    from colophon.adapters.audio import clear_audio_metadata_cache, read_audio_metadata

    p = tmp_path / "headerless.mp3"
    p.write_bytes(b"\x00" * 1024 + b"\xff\xfb" + b"data" * 1000)  # has non-zero data in the header
    clear_audio_metadata_cache()
    monkeypatch.setattr(audio_mod, "MutagenFile", lambda *a, **k: None)  # force mutagen failure
    monkeypatch.setattr(audio_mod, "probe_duration_seconds", lambda _p: 123.0)

    sf, _ = read_audio_metadata(p)
    assert sf.duration_seconds == 123.0  # ffprobe ran and recovered it
