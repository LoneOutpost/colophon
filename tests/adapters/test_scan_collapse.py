from __future__ import annotations

from pathlib import Path

from colophon.adapters.scan import BookUnitFiles, collapse_child_folders


def _unit(folder: str, *names: str) -> BookUnitFiles:
    base = Path(folder)
    return BookUnitFiles(folder=base, files=[base / n for n in names])


def _folders(units: list[BookUnitFiles]) -> set[Path]:
    """Compare by folder, not list order: the collapse returns its result sorted by name."""
    return {u.folder for u in units}


def test_disc_folders_fold_into_their_parent():
    units = [
        _unit("/lib/Innocent in Death/CD1", "a01.mp3", "a02.mp3"),
        _unit("/lib/Innocent in Death/CD2", "b01.mp3"),
    ]
    (collapsed,) = collapse_child_folders(units)
    assert collapsed.folder == Path("/lib/Innocent in Death")
    assert [p.name for p in collapsed.files] == ["a01.mp3", "a02.mp3", "b01.mp3"]


def test_files_are_ordered_by_disc_then_track():
    # CD2 must precede CD10 — a plain name sort would not.
    units = [
        _unit("/lib/Book/CD10", "x.mp3"),
        _unit("/lib/Book/CD2", "y.mp3"),
        _unit("/lib/Book/CD1", "z.mp3"),
    ]
    (collapsed,) = collapse_child_folders(units)
    assert [p.parent.name for p in collapsed.files] == ["CD1", "CD2", "CD10"]


def test_mixed_zero_padding_still_collapses():
    # The real JD Robb book has CD2 and CD03 side by side.
    units = [_unit("/lib/Book/CD03", "a.mp3"), _unit("/lib/Book/CD2", "b.mp3")]
    (collapsed,) = collapse_child_folders(units)
    assert collapsed.folder == Path("/lib/Book")
    assert [p.parent.name for p in collapsed.files] == ["CD2", "CD03"]


def test_a_missing_disc_still_collapses():
    # Flagged-and-whole beats silently-split: missing-parts detection handles the gap later.
    units = [_unit("/lib/Book/CD1", "a.mp3"), _unit("/lib/Book/CD3", "b.mp3")]
    assert len(collapse_child_folders(units)) == 1


def test_a_lone_disc_folder_is_left_alone():
    units = [_unit("/lib/Book/CD1", "a.mp3")]
    assert _folders(collapse_child_folders(units)) == _folders(units)


def test_a_parent_with_its_own_audio_is_left_alone():
    # Ambiguous shape: loose audio beside disc folders is not a clean disc split.
    units = [
        _unit("/lib/Book", "intro.mp3"),
        _unit("/lib/Book/CD1", "a.mp3"),
        _unit("/lib/Book/CD2", "b.mp3"),
    ]
    assert _folders(collapse_child_folders(units)) == _folders(units)


def test_a_non_disc_sibling_blocks_the_collapse():
    units = [
        _unit("/lib/Book/CD1", "a.mp3"),
        _unit("/lib/Book/CD2", "b.mp3"),
        _unit("/lib/Book/Bonus Interview", "c.mp3"),
    ]
    assert _folders(collapse_child_folders(units)) == _folders(units)


def test_ordinary_sibling_books_are_untouched():
    units = [
        _unit("/lib/Author/Dune", "a.mp3"),
        _unit("/lib/Author/Messiah", "b.mp3"),
    ]
    assert _folders(collapse_child_folders(units)) == _folders(units)


def test_an_explicit_member_set_collapses_non_disc_folders():
    units = [
        _unit("/lib/Author/Part One", "a.mp3"),
        _unit("/lib/Author/Part Two", "b.mp3"),
    ]
    combined = {"/lib/Author": frozenset({"/lib/Author/Part One", "/lib/Author/Part Two"})}
    (collapsed,) = collapse_child_folders(units, combined=combined)
    assert collapsed.folder == Path("/lib/Author")
    assert [p.name for p in collapsed.files] == ["a.mp3", "b.mp3"]


def test_a_non_member_sibling_keeps_its_own_book():
    # The bug this guards: combining two books under an author folder must not swallow the rest.
    units = [
        _unit("/lib/Author/Part One", "a.mp3"),
        _unit("/lib/Author/Part Two", "b.mp3"),
        _unit("/lib/Author/Another Book", "c.mp3"),
    ]
    combined = {"/lib/Author": frozenset({"/lib/Author/Part One", "/lib/Author/Part Two"})}
    result = collapse_child_folders(units, combined=combined)
    folders = {u.folder for u in result}
    assert Path("/lib/Author") in folders
    assert Path("/lib/Author/Another Book") in folders
    assert len(result) == 2
