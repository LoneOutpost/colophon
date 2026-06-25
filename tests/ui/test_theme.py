from colophon.ui.theme import PAGE_DARK, PAGE_LIGHT, preload_background_css


def test_preload_dark_paints_dark_html():
    css = preload_background_css("dark")
    assert css == f"html{{background:{PAGE_DARK}}}"
    assert "@media" not in css  # explicit pref must not depend on the OS setting


def test_preload_light_paints_light_html():
    assert preload_background_css("light") == f"html{{background:{PAGE_LIGHT}}}"


def test_preload_auto_uses_os_media_query():
    css = preload_background_css("auto")
    assert css.startswith(f"html{{background:{PAGE_LIGHT}}}")
    assert f"@media(prefers-color-scheme:dark){{html{{background:{PAGE_DARK}}}}}" in css
