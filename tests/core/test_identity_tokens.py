from __future__ import annotations

from colophon.core.identity_tokens import title_candidates


def test_drops_author_and_series_marker():
    name = "Alan Dean Foster.-.Flinx Bk03.-.Orphan Star"
    assert title_candidates(name, authors=["Alan Dean Foster"], series=[]) == ["Orphan Star"]


def test_author_match_folds_initials_via_normalize_key():
    name = "HP Lovecraft - The Fungi From Yuggoth"
    assert title_candidates(name, authors=["H. P. Lovecraft"], series=[]) == ["The Fungi From Yuggoth"]


def test_drops_bracketed_and_structural_tokens():
    assert title_candidates("Cujo (read by Lorna Raver)", authors=[], series=[]) == ["Cujo"]
    assert title_candidates("Title - 01 of 12", authors=[], series=[]) == ["Title"]


def test_bare_number_title_survives():
    assert title_candidates("1984", authors=[], series=[]) == ["1984"]


def test_author_only_folder_yields_no_title():
    assert title_candidates("Alexei Panshin", authors=["Alexei Panshin"], series=[]) == []
