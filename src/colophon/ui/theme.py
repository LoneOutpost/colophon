"""Shared visual theme: brand colors, dark-mode handling, and global CSS.

Call `apply_theme()` once at the top of every page render so the palette and
base styles are present, `setup_dark_mode()` to honor the stored/system
preference, and `dark_mode_button()` to add the header toggle.
"""

from __future__ import annotations

from nicegui import app, ui

# Warm "Clay & paper": a terracotta accent on warm stone neutrals. Flat (no
# gradients or glows). The accent lightens to a burnt orange on dark surfaces.
_PRIMARY = "#bf5a3c"

_CSS = """
:root {
  --col-radius: 12px;
  --colophon-accent: #bf5a3c;
  --colophon-sel: rgba(191, 90, 60, .12);
  --colophon-hover: rgba(191, 90, 60, .06);
  --colophon-ring: rgba(191, 90, 60, .45);
  --colophon-line: #e7ded2;
  --colophon-faint: #9a8f7e;
  --colophon-muted: #6c6256;
  --colophon-surface: #fcf9f4;
  --colophon-page: #f6f1ea;
}
.body--dark {
  /* Lighten the Quasar accent on dark surfaces (buttons, badges, .text-primary).
     !important beats the inline --q-primary that ui.colors sets on <body>. */
  --q-primary: #d6754f !important;
  --colophon-accent: #d6754f;
  --colophon-sel: rgba(214, 117, 79, .18);
  --colophon-hover: rgba(214, 117, 79, .08);
  --colophon-ring: rgba(214, 117, 79, .5);
  --colophon-line: #473f35;
  --colophon-faint: #8a8073;
  --colophon-muted: #b6ab9c;
  --colophon-surface: #262019;
  --colophon-page: #1c1916;
}
@font-face {
  font-family: 'Spectral'; font-style: normal; font-weight: 600;
  font-display: swap; src: url('/assets/fonts/spectral-600.woff2') format('woff2');
}
@font-face {
  font-family: 'Spectral'; font-style: normal; font-weight: 700;
  font-display: swap; src: url('/assets/fonts/spectral-700.woff2') format('woff2');
}
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif;
  background: var(--colophon-page);
  color: #2c271f;
}
.body--dark body { color: #ece4d8; }
/* Warm page surface in light mode (dark uses Quasar's dark_page). */
.q-page-container, .q-page { background: var(--colophon-page); }
.body--dark .q-page-container, .body--dark .q-page { background: #1c1916; }
/* Helper type classes consumed by the workspace. */
.colophon-book-title { font-family: 'Spectral', Georgia, 'Times New Roman', serif; }
.colophon-mono { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
/* Segmented toggle (Manage kind switcher): full-contrast unselected options, not the
   faint Quasar default. The active option keeps its accent fill + light text. */
.colophon-seg .q-btn:not(.q-btn--active) { color: #2c271f; }
.body--dark .colophon-seg .q-btn:not(.q-btn--active) { color: #ece4d8; }
/* One soft elevation for every card; 12px radius everywhere. */
.q-card {
  border-radius: var(--col-radius);
  background: var(--colophon-surface);
  box-shadow: 0 1px 2px rgba(24, 24, 27, .06), 0 1px 3px rgba(24, 24, 27, .05);
}
.body--dark .q-card { background: #262019; box-shadow: 0 1px 2px rgba(0, 0, 0, .45); }
/* Neutral, flat header/footer with a hairline rule (accent is reserved for
   actions, not the whole app bar). */
.q-header {
  box-shadow: none;
  background: var(--colophon-surface);
  color: #2c271f;
  border-bottom: 1px solid var(--colophon-line);
}
.body--dark .q-header {
  background: #262019;
  color: #ece4d8;
  border-bottom-color: var(--colophon-line);
}
.q-footer {
  background: var(--colophon-surface);
  color: #2c271f;
  border-top: 1px solid var(--colophon-line);
}
.body--dark .q-footer {
  background: #262019;
  color: #ece4d8;
  border-top-color: var(--colophon-line);
}
/* Rounded inputs and list rows to match the card radius scale. */
.q-field--outlined .q-field__control { border-radius: 8px; }
.q-item { border-radius: 8px; }
/* Row states: full warm tint for selection/hover, an inset ring for keyboard
   focus. No left-edge accent bar. */
.book-row-selected { background: var(--colophon-sel); }
.book-row:hover { background: var(--colophon-hover); }
.book-row-focused {
  background: var(--colophon-sel);
  box-shadow: inset 0 0 0 1px var(--colophon-ring);
}
/* Calmer scrollbars. */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(120, 120, 128, .4); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: rgba(120, 120, 128, .6); }
/* Detail-pane structure: section captions, tool groups, sticky action bar. */
.colophon-seccap { font-size: 10px; letter-spacing: .07em; text-transform: uppercase;
  color: var(--colophon-accent); font-weight: 700; margin: 10px 0 4px; }
.colophon-toolgroup { border: 1px solid var(--colophon-line); border-radius: 8px; padding: 6px 8px; }
.colophon-actionbar { position: sticky; bottom: 0; margin-top: 8px; padding: 8px 0;
  background: var(--colophon-surface); border-top: 1px solid var(--colophon-line); }
.body--dark .colophon-actionbar { background: #262019; }
"""


def apply_theme() -> None:
    """Set the brand palette and inject base CSS for the current page."""
    ui.colors(
        primary=_PRIMARY,
        secondary="#8a7f70",
        accent=_PRIMARY,
        positive="#16a34a",
        negative="#dc2626",
        info="#0ea5e9",
        warning="#d97706",
        dark="#262019",        # elevated dark surface (cards, header)
        dark_page="#1c1916",   # dark page background
    )
    ui.add_css(_CSS)


def setup_dark_mode() -> ui.dark_mode:
    """Apply the stored dark-mode preference, defaulting to the system setting.

    Returns the dark_mode control so a toggle can drive it."""
    pref = app.storage.general.get("dark_mode", "auto")
    dark = ui.dark_mode()
    if pref == "dark":
        dark.enable()
    elif pref == "light":
        dark.disable()
    else:
        # Follow the OS for the initial paint (Quasar 'auto'), then, once the
        # client is connected, pin the value to the matching explicit boolean.
        # This keeps the first render flash-free while ensuring later toggles are
        # explicit -> explicit (a clean repaint) rather than flipping out of
        # 'auto', which only restyles part of the page until the next navigation.
        dark.value = None

        async def _pin_to_system() -> None:
            try:
                is_dark = await ui.run_javascript(
                    "window.matchMedia('(prefers-color-scheme: dark)').matches"
                )
            except Exception:  # JS unavailable/timed out: stay on 'auto' (BLE001 intentional)
                return
            dark.value = bool(is_dark)

        ui.timer(0.1, _pin_to_system, once=True)
    return dark


def dark_mode_button(dark: ui.dark_mode) -> None:
    """A header button that toggles light/dark and persists the choice."""
    button = ui.button().props("flat round")

    def _sync() -> None:
        button.props(f"icon={'light_mode' if dark.value else 'dark_mode'}")
        button.tooltip("Switch to light mode" if dark.value else "Switch to dark mode")

    def _toggle() -> None:
        going_dark = not bool(dark.value)
        dark.value = going_dark
        app.storage.general["dark_mode"] = "dark" if going_dark else "light"
        _sync()

    _sync()
    button.on_click(_toggle)
