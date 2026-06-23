"""Pure free-text parsers for metadata that arrives as prose rather than fields.

Kept in core (not in an adapter) so they are unit-testable without standing up
an HTTP client, and reusable if another source reports the same shapes.
"""

from __future__ import annotations

import re
from typing import Any

# "Read by Jane Doe", "Narrated by A and B", "Reader: X" -> capture the name run.
_NARRATOR_RE = re.compile(
    r"(?:read by|narrated by|reader[s]?\s*[:\-])\s*(?P<names>[^.;\n<]+)",
    re.IGNORECASE,
)
_NAME_SPLIT_RE = re.compile(r",|\band\b")


def parse_runtime_ms(value: Any) -> int | None:
    """'7:34:27' / '58:03' -> milliseconds; None for missing/unparseable input."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not parts or not all(p.strip().isdigit() for p in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds * 1000


def parse_narrators(description: Any) -> list[str]:
    """Best-effort narrator extraction from a free-text description. Returns [] when
    no 'read by'/'narrated by'/'reader:' cue is found (a miss beats a wrong name)."""
    if not isinstance(description, str):
        return []
    match = _NARRATOR_RE.search(description)
    if not match:
        return []
    out: list[str] = []
    for raw in _NAME_SPLIT_RE.split(match.group("names")):
        name = raw.strip(" \t-—·").strip()
        if name and name not in out:
            out.append(name)
    return out
