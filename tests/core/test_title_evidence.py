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


def test_separator_spanning_committed_title_is_outvoted_by_the_leaf_token():
    # The 245-title class: a committed directory title that is the whole "Author.-.Title" folder
    # string scores title_junk 1.0, so its dominant committed weight zeroes and the clean leaf token
    # from the same folder wins.
    b = _book(folder="Alastair Reynolds.-.House of Suns",
              stems=["Alastair Reynolds - House of Suns 01"], authors=["Alastair Reynolds"],
              title="Alastair Reynolds.-.House of Suns", title_prov=Provenance.DIRECTORY.value)
    resolve_title(b)
    assert b.title == "House of Suns"


def test_good_committed_directory_title_outweighs_noisy_folder_token():
    # no gate: the committed (normalized) directory title out-weighs a noisier folder token by weight.
    b = _book(folder="The Gunslinger (DT1 - original edition)",
              stems=["The Gunslinger 01"], authors=["Stephen King"],
              title="The Gunslinger", title_prov=Provenance.DIRECTORY.value)
    resolve_title(b)
    assert b.title == "The Gunslinger"


def test_resolve_title_cleans_a_folder_title_with_cruft():
    # a committed folder title carrying an encoding tail is cleaned by resolve_title (which runs in
    # the graph pass, after repair_fields) so folder-derived titles don't escape cleaning.
    b = _book(folder="Alastair Reynolds.-.Blue Remembered Earth",
              stems=["Blue Remembered Earth - 01"], authors=["Alastair Reynolds"],
              title="Blue Remembered Earth Ua 3@64.44m", title_prov=Provenance.DIRECTORY.value)
    resolve_title(b)
    assert b.title == "Blue Remembered Earth"
