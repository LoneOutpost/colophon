from __future__ import annotations

from pathlib import Path

from colophon.core import evidence_weights as W
from colophon.core.models import BookUnit, DetectedWork, Provenance, SeriesRef, SourceFile
from colophon.core.title_evidence import resolve_title


def test_title_weights_exist_and_bonus_is_below_every_source():
    for name in ("W_T_TAG", "W_T_DATAFILE", "W_T_FOLDER", "W_T_FILENAME", "W_T_CONSTANCY_BONUS"):
        assert getattr(W, name) > 0
    assert W.W_T_CONSTANCY_BONUS < min(W.W_T_TAG, W.W_T_DATAFILE, W.W_T_FOLDER, W.W_T_FILENAME)


def _book(folder, stems, authors=(), series=(), title=None, title_prov=Provenance.TAG.value,
          label=None, label_prov=Provenance.TAG.value) -> BookUnit:
    b = BookUnit.new(source_folder=Path(f"/lib/{folder}"))
    b.source_files = [SourceFile(path=Path(f"/lib/{s}.opus"), size=1, duration_seconds=0.0, ext="opus")
                      for s in stems]
    b.authors = list(authors)
    b.series = [SeriesRef(name=n) for n in series]
    if label:
        b.detected_works = [DetectedWork(label=label, label_prov=label_prov)]
    if title is not None:
        b.title, b.provenance["title"] = title, title_prov
    return b


def test_committed_author_title_is_penalized_and_folder_token_wins():
    b = _book(folder="Alan Dean Foster.-.Flinx Bk03.-.Orphan Star",
              stems=["Alan Dean Foster - Orphan Star 01"], authors=["Alan Dean Foster"],
              title="Alan Dean Foster", title_prov=Provenance.TAG.value)
    resolve_title(b)
    assert b.title == "Orphan Star"


def test_a_real_tag_title_is_kept():
    b = _book(folder="Alan Dean Foster.-.Flinx Bk03.-.Orphan Star",
              stems=["Alan Dean Foster - Orphan Star 01"], authors=["Alan Dean Foster"],
              title="Orphan Star", title_prov=Provenance.TAG.value)
    resolve_title(b)
    assert b.title == "Orphan Star"


def test_manual_title_settles():
    b = _book(folder="Whatever", stems=["x"], authors=["A"], title="Manual", title_prov=Provenance.MANUAL.value)
    resolve_title(b)
    assert b.title == "Manual"
