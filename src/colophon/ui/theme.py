"""Shared visual theme: brand colors, dark-mode handling, and global CSS.

Call `apply_theme()` once at the top of every page render so the palette and
base styles are present, `setup_dark_mode()` to honor the stored/system
preference, and `dark_mode_button()` to add the header toggle.
"""

from __future__ import annotations

from nicegui import app, ui

# A single calm indigo accent on a zinc-neutral scale. Flat (no gradients or
# glows); the accent lightens slightly for dark surfaces via Quasar's dark plugin.
_PRIMARY = "#4f46e5"

_CSS = """
:root { --col-radius: 12px; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, sans-serif;
}
/* One soft elevation for every card; 12px radius everywhere. */
.q-card {
  border-radius: var(--col-radius);
  box-shadow: 0 1px 2px rgba(24, 24, 27, .06), 0 1px 3px rgba(24, 24, 27, .05);
}
.body--dark .q-card { box-shadow: 0 1px 2px rgba(0, 0, 0, .45); }
/* Neutral, flat header/footer with a hairline rule (accent is reserved for
   actions, not the whole app bar). */
.q-header {
  box-shadow: none;
  background: #ffffff;
  color: #18181b;
  border-bottom: 1px solid rgba(24, 24, 27, .10);
}
.body--dark .q-header {
  background: #1e1e24;
  color: #fafafa;
  border-bottom-color: rgba(255, 255, 255, .08);
}
.q-footer {
  background: #ffffff;
  color: #18181b;
  border-top: 1px solid rgba(24, 24, 27, .10);
}
.body--dark .q-footer {
  background: #1e1e24;
  color: #fafafa;
  border-top-color: rgba(255, 255, 255, .08);
}
/* Rounded inputs and list rows to match the card radius scale. */
.q-field--outlined .q-field__control { border-radius: 8px; }
.q-item { border-radius: 8px; }
/* Keyboard-focused book row: a left accent rule + a faint accent tint. */
.book-row-focused {
  box-shadow: inset 3px 0 0 var(--q-primary);
  background: rgba(79, 70, 229, .07);
}
.body--dark .book-row-focused { background: rgba(99, 102, 241, .16); }
/* Calmer scrollbars. */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(120, 120, 128, .4); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: rgba(120, 120, 128, .6); }
"""


def apply_theme() -> None:
    """Set the brand palette and inject base CSS for the current page."""
    ui.colors(
        primary=_PRIMARY,
        secondary="#64748b",
        accent=_PRIMARY,
        positive="#16a34a",
        negative="#dc2626",
        info="#0ea5e9",
        warning="#d97706",
        dark="#1e1e24",        # elevated dark surface (cards, header)
        dark_page="#141418",   # dark page background
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

    def _icon() -> str:
        return "light_mode" if dark.value else "dark_mode"

    def _tooltip() -> str:
        return "Switch to light mode" if dark.value else "Switch to dark mode"

    def _toggle() -> None:
        going_dark = not bool(dark.value)
        dark.value = going_dark
        app.storage.general["dark_mode"] = "dark" if going_dark else "light"
        button.props(f"icon={_icon()}")
        button.tooltip(_tooltip())

    button.props(f"icon={_icon()}")
    button.tooltip(_tooltip())
    button.on_click(_toggle)
