"""Guard: shipped theme tokens must meet WCAG 2.2 AA. Imports the same constants
ui/theme.py uses, so a future token edit that regresses a ratio fails CI."""

from colophon.ui.theme import (
    ACCENT_DARK,
    ACCENT_LIGHT,
    BORDER_DARK,
    BORDER_LIGHT,
    MUTED_DARK,
    MUTED_LIGHT,
    NEGATIVE,
    PAGE_DARK,
    POSITIVE,
    PRIMARY,
    SURFACE_DARK,
    SURFACE_LIGHT,
    WARNING,
)

WHITE = "#ffffff"


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hex_: str) -> float:
    h = hex_.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _ratio(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_status_badge_fills_pass_white_text():
    for fill in (POSITIVE, WARNING, NEGATIVE):
        assert _ratio(WHITE, fill) >= 4.5, f"{fill} fails white-text AA"


def test_light_accent_passes_as_fill_and_as_text():
    assert _ratio(WHITE, PRIMARY) >= 4.5          # filled button label
    assert _ratio(PRIMARY, SURFACE_LIGHT) >= 4.5  # .text-primary / link
    assert _ratio(ACCENT_LIGHT, SURFACE_LIGHT) >= 4.5  # section caption


def test_dark_accent_passes_as_text_and_with_dark_button_label():
    assert _ratio(ACCENT_DARK, SURFACE_DARK) >= 4.5   # dark caption / .text-primary
    assert _ratio(PAGE_DARK, ACCENT_DARK) >= 4.5       # dark filled button uses ink label


def test_muted_text_passes_both_modes():
    assert _ratio(MUTED_LIGHT, SURFACE_LIGHT) >= 4.5
    assert _ratio(MUTED_DARK, SURFACE_DARK) >= 4.5


def test_control_border_passes_both_modes():
    assert _ratio(BORDER_LIGHT, SURFACE_LIGHT) >= 3.0
    assert _ratio(BORDER_DARK, SURFACE_DARK) >= 3.0
