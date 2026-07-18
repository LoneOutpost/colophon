from pathlib import Path

from colophon.core.duplicate_check import duplicate_targets


def test_groups_books_that_share_a_target_path():
    pairs = [
        ("b1", Path("/lib/Zahn/Thrawn.m4b")),
        ("b2", Path("/lib/Zahn/Thrawn.m4b")),   # collides with b1
        ("b3", Path("/lib/Zahn/Alliances.m4b")),  # unique
        ("b4", Path("/lib/Zahn/Thrawn.m4b")),   # collides with b1, b2
    ]
    groups = duplicate_targets(pairs)
    assert groups == [(Path("/lib/Zahn/Thrawn.m4b"), ["b1", "b2", "b4"])]


def test_no_groups_when_all_targets_unique():
    pairs = [("b1", Path("/lib/a.m4b")), ("b2", Path("/lib/b.m4b"))]
    assert duplicate_targets(pairs) == []


def test_multiple_collision_groups_sorted_by_path():
    pairs = [
        ("b1", Path("/lib/B.m4b")),
        ("b2", Path("/lib/A.m4b")),
        ("b3", Path("/lib/B.m4b")),
        ("b4", Path("/lib/A.m4b")),
    ]
    groups = duplicate_targets(pairs)
    assert groups == [
        (Path("/lib/A.m4b"), ["b2", "b4"]),
        (Path("/lib/B.m4b"), ["b1", "b3"]),
    ]
