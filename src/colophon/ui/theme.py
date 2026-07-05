"""Shared visual theme: brand colors, dark-mode handling, and global CSS.

Call `apply_theme()` once at the top of every page render so the palette and
base styles are present, `setup_dark_mode()` to honor the stored/system
preference, and `dark_mode_button()` to add the header toggle.
"""

from __future__ import annotations

from nicegui import app, ui

# Warm "Clay & paper": a terracotta accent on warm stone neutrals. Flat (no
# gradients or glows). All contrast-relevant colors are module constants so
# ui.colors(), the CSS, and tests/ui/test_contrast.py share one source (WCAG AA).
PRIMARY = "#b04e30"        # light accent: white-on-fill 5.28, on surface 5.03
POSITIVE = "#15803d"       # badge fill: white text 5.02
WARNING = "#b45309"        # badge fill: white text 5.02
NEGATIVE = "#dc2626"       # badge fill: white text 4.83
ACCENT_LIGHT = "#b04e30"
ACCENT_DARK = "#d6754f"    # as text on dark surface 5.00
MUTED_LIGHT = "#6c6256"    # 5.68
MUTED_DARK = "#b6ab9c"     # 7.13
BORDER_LIGHT = "#94876f"   # 3.36 vs light surface
BORDER_DARK = "#736a5a"    # 3.02 vs dark surface
SURFACE_LIGHT = "#fcf9f4"
SURFACE_DARK = "#262019"
PAGE_DARK = "#1c1916"
PAGE_LIGHT = "#f6f1ea"

_LIGHT_VARS = {
    "col-radius": "12px",
    "colophon-accent": ACCENT_LIGHT,
    "colophon-sel": "rgba(176, 78, 48, .12)",
    "colophon-hover": "rgba(176, 78, 48, .06)",
    "colophon-ring": "rgba(176, 78, 48, .45)",
    "colophon-line": "#e7ded2",
    "colophon-border": BORDER_LIGHT,
    "colophon-muted": MUTED_LIGHT,
    "colophon-surface": SURFACE_LIGHT,
    "colophon-page": PAGE_LIGHT,
}
_DARK_VARS = {
    # !important beats the inline --q-primary that ui.colors sets on <body>.
    "q-primary": f"{ACCENT_DARK} !important",
    "colophon-accent": ACCENT_DARK,
    "colophon-sel": "rgba(214, 117, 79, .18)",
    "colophon-hover": "rgba(214, 117, 79, .08)",
    "colophon-ring": "rgba(214, 117, 79, .5)",
    "colophon-line": "#473f35",
    "colophon-border": BORDER_DARK,
    "colophon-muted": MUTED_DARK,
    "colophon-surface": SURFACE_DARK,
    "colophon-page": PAGE_DARK,
}


def _vars_block(selector: str, kv: dict[str, str]) -> str:
    lines = "\n".join(f"  --{name}: {value};" for name, value in kv.items())
    return f"{selector} {{\n{lines}\n}}\n"


_STATIC_CSS = """
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
body.body--dark { color: #ece4d8; }
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
/* Rounded inputs and list rows to match the card radius scale. Outlined input
   boundaries use the AA control border (3:1), not the faint decorative hairline. */
.q-field--outlined .q-field__control { border-radius: 8px; }
.q-field--outlined .q-field__control:before { border-color: var(--colophon-border); }
.q-item { border-radius: 8px; }
/* Active-jobs indicator: the popover menu gets a comfortable minimum so progress bars
   and detail lines aren't cramped. */
.colophon-jobs-menu { min-width: 16rem; }
/* Pipeline stepper stages in the header: even padding, count badge sits inline with the label. */
.colophon-stage { padding-inline: 10px; }
.colophon-stage .q-badge { font-weight: 600; }
/* Row states: full warm tint for selection/hover, an inset ring for keyboard
   focus. No left-edge accent bar. */
.book-row-selected { background: var(--colophon-sel); }
.book-row:hover { background: var(--colophon-hover); }
.book-row-focused {
  background: var(--colophon-sel);
  box-shadow: inset 0 0 0 1px var(--colophon-ring);
}
/* Let the title ellipsize instead of forcing the row wide: a flex child won't shrink
   below its content unless min-width is 0, and without this the right-pinned
   confidence/state badges get pushed off the (often narrow) list pane. */
.book-row .q-item__section--main,
.book-row .colophon-book-title { min-width: 0; }
/* Pin the Books list's scroll content to its container width. Quasar's scroll content is absolutely
   positioned and sizes to its widest child, so without this the rows expand to their natural width
   and push the right-pinned confidence/state badges out past the (scrollable) pane edge. */
.colophon-book-scroll .q-scrollarea__content { width: 100%; }
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
/* Page sub-header band: a recessive surface zone with a hairline rule, separating a
   page's controls + state-of-play from its body (the page -> surface -> line tonal
   rule). Shared across pages via chrome.page_toolbar. */
.colophon-toolbar { background: var(--colophon-surface); border-bottom: 1px solid var(--colophon-line);
  padding: 10px 16px; }
.body--dark .colophon-toolbar { background: #262019; }
/* Reading-column cap for form/prose pages: left-anchored (not centered), so the page
   frame stays identical to full-bleed pages while fields keep a scannable width. */
.colophon-measure-read { max-width: 768px; }
/* AA helpers (#105): warm muted text, muted outline chips, dark filled-button ink
   text (the dark accent fails white-on-fill), and a viewport cap on every dialog. */
.colophon-muted { color: var(--colophon-muted); }
.colophon-chip { color: var(--colophon-muted); border-color: var(--colophon-border); }
.body--dark .q-btn.bg-primary, .body--dark .q-btn.bg-primary .q-btn__content {
  color: #1c1916 !important;  /* dark accent fails white-on-fill; beats Quasar .text-white */
}
/* Checkbox/radio/toggle text labels inherit the page's dark ink, so in dark mode
   they render dark-on-dark. Route them through the AA muted token. */
.body--dark .q-checkbox__label,
.body--dark .q-radio__label,
.body--dark .q-toggle__label { color: var(--colophon-muted); }
.q-dialog .q-card { max-width: calc(100vw - 2rem); }
"""

_CSS = _vars_block(":root", _LIGHT_VARS) + _vars_block(".body--dark", _DARK_VARS) + _STATIC_CSS


def apply_theme() -> None:
    """Set the brand palette and inject base CSS for the current page."""
    ui.colors(
        primary=PRIMARY,
        secondary="#8a7f70",
        accent=PRIMARY,
        positive=POSITIVE,
        negative=NEGATIVE,
        info="#0ea5e9",
        warning=WARNING,
        dark=SURFACE_DARK,        # elevated dark surface (cards, header)
        dark_page=PAGE_DARK,      # dark page background
    )
    ui.add_css(_CSS)


def preload_background_css(pref: str) -> str:
    """The `html` background rule that paints the page in the right theme color in
    the initial HTML, before the dark-mode class is applied on connect. Without it,
    every full-page navigation flashes the light background for an explicit-dark user
    (the dark class only arrives after the websocket connects). Styling `html`
    (Quasar styles `body`) means it shows only during the pre-hydration blank and
    never fights the hydrated theme. 'auto' uses the OS media query, like Quasar."""
    if pref == "dark":
        return f"html{{background:{PAGE_DARK}}}"
    if pref == "light":
        return f"html{{background:{PAGE_LIGHT}}}"
    return (
        f"html{{background:{PAGE_LIGHT}}}"
        f"@media(prefers-color-scheme:dark){{html{{background:{PAGE_DARK}}}}}"
    )


def preload_theme_background() -> None:
    """Inject the early page-background `<style>` into the document head. Call this in
    every page handler's *synchronous* prefix (before any `await`), so it lands in
    the initial HTML and the navigation paints in the right theme instead of flashing
    light until the dark-mode class arrives on connect."""
    pref = app.storage.general.get("dark_mode", "auto")
    ui.add_head_html(f"<style>{preload_background_css(pref)}</style>")


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
        label = "Switch to light mode" if dark.value else "Switch to dark mode"
        button.props(f"icon={'light_mode' if dark.value else 'dark_mode'} aria-label=\"{label}\"")
        button.tooltip(label)

    def _toggle() -> None:
        going_dark = not bool(dark.value)
        dark.value = going_dark
        app.storage.general["dark_mode"] = "dark" if going_dark else "light"
        _sync()

    _sync()
    button.on_click(_toggle)
