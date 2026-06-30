"""A scan parses each audio file exactly once: SEARCH loads + caches; CATEGORIZE and
IDENTIFY are served from the cache. Regression guard for the read-once dedup.

Rather than patch mutagen's constructors (which breaks its internal loading), this asserts
on the cached reader's own hit/miss counters: after a forced all-phase scan of N single-file
books, the reader misses once per file (the SEARCH load) and the CATEGORIZE + IDENTIFY reads
are cache hits. Before the dedup wiring those two phases bypass the cache, so hits stay at 0.
"""

from __future__ import annotations

from mutagen.id3 import ID3, TIT2, TPE1

from colophon.adapters.audio import _read_audio_metadata, clear_audio_metadata_cache
from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.services.ingest import ScanOptions, ScanScope, plan_scan


def _tagged_mp3(make_audio, name: str, title: str) -> None:
    path = make_audio(name, seconds=1)
    id3 = ID3(path)
    id3.add(TIT2(encoding=3, text=[title]))
    id3.add(TPE1(encoding=3, text=["Author"]))
    id3.save(path)


def test_scan_reads_each_file_once_via_cache(make_audio, tmp_path):
    # Two books, each one audio file, under one scan root.
    _tagged_mp3(make_audio, "BookA/01.mp3", "Book A")
    _tagged_mp3(make_audio, "BookB/01.mp3", "Book B")
    # make_audio writes under the test's tmp_path (shared fixture), so both book folders
    # live directly under it; scan that directory as the root.
    scan_root = tmp_path

    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    repo = BookUnitRepo(conn)

    clear_audio_metadata_cache()
    opts = ScanOptions(scope=ScanScope.REFRESH)  # force all phases
    plan = plan_scan(repo, scan_root, template="$Author - $Title", options=opts)
    conn.close()

    # All three phases ran and the cached tags reached IDENTIFY (titles came from TIT2).
    assert {b.title for b in plan.units} == {"Book A", "Book B"}

    info = _read_audio_metadata.cache_info()
    # One real parse per file (the SEARCH load); nothing re-parsed.
    assert info.misses == 2, info
    # CATEGORIZE + IDENTIFY for each book are served from the cache (2 books x 2 reads).
    assert info.hits == 4, info
