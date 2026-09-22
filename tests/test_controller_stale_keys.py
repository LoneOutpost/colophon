"""Detect (never rewrite) stored entity-alias/franchise keys the current tokenizer can no
longer produce. See AppController.unreachable_alias_keys / unreachable_franchise_keys.

Note on the example used here: the branch's headline case is that an apostrophe used to become a
word boundary ("O'Brien" -> old key "o brien") and now glues instead ("O'Brien" -> "obrien"). But
"o brien" itself is *not* a usable example for this detector: normalize_key("o brien") == "o brien"
(a real two-word name like "O Brien" still keys there today), so it stays a fixed point forever and
this detector correctly leaves it alone — it cannot know whether the row came from "O'Brien" or a
genuine "O Brien", and reporting it would be a false positive. What the detector catches is a key
that has stopped being *any* valid output of the current tokenizer at all, e.g. one still carrying a
raw apostrophe from a normalize_key era that predates punctuation-stripping entirely.
"""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _ctx(tmp_path):
    return AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[])
    )


def test_reports_an_alias_key_the_tokenizer_can_no_longer_produce(tmp_path):
    # A key still carrying a raw apostrophe cannot come from today's normalize_key (it strips
    # apostrophes: normalize_key("o'brien") == "obrien"), so it is dead weight no current rename
    # will ever hit. It cannot be repaired automatically -- the original alias text was never
    # stored -- so it must be surfaced instead.
    ctx = _ctx(tmp_path)
    ctx.aliases.set("author", "o'brien", "Patrick O'Brian")
    stale = AppController(ctx).unreachable_alias_keys()
    assert ("author", "o'brien") in stale
    ctx.close()


def test_a_reachable_alias_key_is_not_reported(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.aliases.set("author", "anne mccaffery", "Anne McCaffrey")
    assert AppController(ctx).unreachable_alias_keys() == []
    ctx.close()


def test_reports_a_franchise_key_the_tokenizer_can_no_longer_produce(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.conn.execute(
        "INSERT INTO known_entities (kind, name_key, display) VALUES ('franchise', ?, ?)",
        ("o'brien", "O'Brien Chronicles"),
    )
    ctx.conn.commit()
    stale = AppController(ctx).unreachable_franchise_keys()
    assert "o'brien" in stale
    ctx.close()


def test_a_reachable_franchise_key_is_not_reported(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.franchises.add("Star Wars")
    assert AppController(ctx).unreachable_franchise_keys() == []
    ctx.close()
