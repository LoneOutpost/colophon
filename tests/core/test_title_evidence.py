from __future__ import annotations

from colophon.core import evidence_weights as W


def test_title_weights_exist_and_bonus_is_below_every_source():
    for name in ("W_T_TAG", "W_T_DATAFILE", "W_T_FOLDER", "W_T_FILENAME", "W_T_CONSTANCY_BONUS"):
        assert getattr(W, name) > 0
    assert W.W_T_CONSTANCY_BONUS < min(W.W_T_TAG, W.W_T_DATAFILE, W.W_T_FOLDER, W.W_T_FILENAME)
