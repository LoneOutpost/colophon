from __future__ import annotations

from colophon.core.ballot import Tallied, tally


def test_sums_weight_per_key_and_reports_share():
    result = tally([("a", 1.0), ("a", 2.0), ("b", 1.0)])
    assert result.winner == "a"
    assert result.share == 0.75          # 3 / 4
    assert result.totals == {"a": 3.0, "b": 1.0}


def test_empty_votes_yield_no_winner():
    assert tally([]) == Tallied(None, 0.0, {})


def test_tiebreak_none_prefers_stronger_single_vote():
    # a and b both total 2.0; a has one 2.0 vote, b has two 1.0 votes -> a wins on strongest.
    result = tally([("a", 2.0), ("b", 1.0), ("b", 1.0)])
    assert result.winner == "a"
    assert result.share == 0.5           # 2 / 4


def test_tiebreak_order_prefers_earlier_in_order():
    # equal totals; "author" precedes "series" in order -> author wins.
    order = ("title", "author", "series", "franchise", "container")
    result = tally([("author", 1.0), ("series", 1.0)], order=order)
    assert result.winner == "author"
    assert result.share == 0.5


def test_share_is_rounded_to_two_decimals():
    result = tally([("a", 1.0), ("b", 1.0), ("c", 1.0)])
    assert result.share == 0.33          # 1/3 rounded


def test_all_zero_weight_votes_have_zero_share_without_dividing():
    result = tally([("a", 0.0)])
    assert result.winner == "a"
    assert result.share == 0.0
