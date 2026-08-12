from __future__ import annotations

from colophon.core import evidence_weights as W


def test_grouping_weights_exist():
    for name in (
        "W_G_PRIOR", "W_G_ENUMERATION", "W_G_INDEX_TITLES", "W_G_CONSTANCY_PER_TOKEN",
        "W_G_UNIFORM_KEY", "W_G_UNIFORM_AUTHOR", "W_G_SERIES", "W_G_SERIES_MIN_SECONDS",
    ):
        assert getattr(W, name) > 0
    assert W.W_G_CONSTANCY_CAP >= 1
    # reliable signals outweigh the prior alone; corroborators alone do not.
    assert W.W_G_ENUMERATION > W.W_G_PRIOR
    assert W.W_G_UNIFORM_KEY + W.W_G_UNIFORM_AUTHOR + W.W_G_CONSTANCY_PER_TOKEN * W.W_G_CONSTANCY_CAP < W.W_G_PRIOR
