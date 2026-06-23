"""Compile a %placeholder% template into a regex and parse filenames into fields.

Ported from id3editor's scraper.py; field vocabulary adapted to Colophon's model.
"""

from __future__ import annotations

import re
from re import Pattern

VALID_FILENAME_FIELDS = {
    "author", "narrator", "title", "subtitle", "series", "sequence", "year",
}

_PLACEHOLDER = re.compile(r"%(\w+)%")


def compile_template(template: str) -> Pattern[str]:
    """Turn a template like '%author% - %title%' into an anchored regex.

    Placeholders become non-greedy named capture groups; literal text is matched
    verbatim. `%skip%` matches and discards a segment.
    """
    parts: list[str] = []
    seen: set[str] = set()
    last = 0
    for match in _PLACEHOLDER.finditer(template):
        literal = template[last : match.start()]
        if literal:
            parts.append(re.escape(literal))
        name = match.group(1)
        if name == "skip":
            parts.append(r"(?:.+?)")
        elif name in VALID_FILENAME_FIELDS:
            if name in seen:
                raise ValueError(f"Placeholder %{name}% used more than once")
            seen.add(name)
            parts.append(rf"(?P<{name}>.+?)")
        else:
            raise ValueError(f"Unknown placeholder %{name}%")
        last = match.end()
    trailing = template[last:]
    if trailing:
        parts.append(re.escape(trailing))
    return re.compile("^" + "".join(parts) + "$")


def strip_ext(filename: str) -> str:
    """The filename without its final extension ('a.b.mp3' -> 'a.b')."""
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def parse_filename(pattern: Pattern[str], filename: str) -> dict[str, str] | None:
    """Parse a filename (extension stripped) into field values, or None if no match."""
    match = pattern.match(strip_ext(filename))
    if match is None:
        return None
    return {key: value.strip() for key, value in match.groupdict().items()}
