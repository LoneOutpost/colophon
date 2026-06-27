from pathlib import Path

import pytest

from colophon.core.filename_cluster import cluster
from tests.fixtures.filename_clusters import CORPUS


@pytest.mark.parametrize("label,names,kind,count", CORPUS, ids=[c[0] for c in CORPUS])
def test_corpus_folder(label, names, kind, count):
    files = [Path("/lib/Author") / n for n in names]
    result = cluster(files)
    assert result.content_kind is kind, f"{label}: expected {kind}, got {result.content_kind}"
    assert len(result.detected_works) == count, f"{label}: expected {count} works"


def test_corpus_decimal_sequence_preserved():
    files = [Path("/lib/Author/Duchess of Love (Duchess of Love Trilogy 0.5).mp3")]
    work = cluster(files).detected_works[0]
    assert work.series == "Duchess of Love Trilogy" and work.sequence == 0.5
