from pathlib import Path

from colophon.core.models import EmbeddedTags, SourceFile
from colophon.services.ingest import WarmCache


def _sf(path, tags, mtime_ns, size):
    return SourceFile(path=path, size=size, mtime_ns=mtime_ns, duration_seconds=60.0, ext="opus", tags=tags)


def test_take_reuses_cached_when_unchanged(tmp_path):
    f = tmp_path / "01.opus"
    f.write_bytes(b"")
    st = f.stat()
    cached = _sf(f, EmbeddedTags(title="Cached"), st.st_mtime_ns, st.st_size)
    wc = WarmCache({str(f): cached})
    got = wc.take(f)
    assert got is cached and got.tags.title == "Cached"
    assert wc.warm == 1 and wc.cold == 0


def test_take_cold_when_mtime_differs(tmp_path):
    f = tmp_path / "01.opus"
    f.write_bytes(b"")
    st = f.stat()
    cached = _sf(f, EmbeddedTags(title="Cached"), st.st_mtime_ns + 1, st.st_size)
    wc = WarmCache({str(f): cached})
    assert wc.take(f) is None
    assert wc.warm == 0 and wc.cold == 1


def test_take_cold_when_absent(tmp_path):
    f = tmp_path / "01.opus"
    f.write_bytes(b"")
    wc = WarmCache({})
    assert wc.take(f) is None and wc.cold == 1


def test_take_cold_when_file_deleted(tmp_path):
    # a cached entry whose file was deleted between scans -> stat() raises OSError -> cold miss
    f = tmp_path / "gone.opus"  # never created on disk
    cached = _sf(f, EmbeddedTags(title="Cached"), 12345, 1)
    wc = WarmCache({str(f): cached})
    assert wc.take(f) is None
    assert wc.warm == 0 and wc.cold == 1


def test_take_cold_when_no_tags(tmp_path):
    f = tmp_path / "01.opus"
    f.write_bytes(b"")
    st = f.stat()
    cached = _sf(f, None, st.st_mtime_ns, st.st_size)
    wc = WarmCache({str(f): cached})
    assert wc.take(f) is None and wc.cold == 1


def test_build_collects_only_tagged_source_files():
    d = Path("/lib/x")
    from colophon.core.models import BookUnit
    b1 = BookUnit.new(source_folder=d)
    b1.source_files = [_sf(d / "a.opus", EmbeddedTags(title="A"), 111, 1),
                       _sf(d / "b.opus", None, 222, 2)]

    class _Repo:
        def list_all(self):
            return [b1]

    wc = WarmCache.build(_Repo())
    assert str(d / "a.opus") in wc.by_path
    assert str(d / "b.opus") not in wc.by_path
