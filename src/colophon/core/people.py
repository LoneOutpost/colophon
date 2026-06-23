"""Split a delimited author/narrator string into individual names.

Sources sometimes deliver several people as one delimited string. This is the
single, shared splitter. In auto mode it uses a conservative full-name
heuristic; when a caller knows the provider's exact delimiter(s) it passes
`separators` to remove the ambiguity entirely.
"""

from __future__ import annotations

import re

# Unambiguous multi-person separators always split in auto mode: '&', a
# whitespace-bounded 'and', and ';'. (Commas are handled separately.)
_AUTO_SEPARATORS = re.compile(r"\s*&\s*|\s+and\s+|\s*;\s*")


def _looks_like_full_name(part: str) -> bool:
    """A 'First Last' name has internal whitespace; 'Frank' or 'Jr.' does not."""
    return " " in part.strip()


def split_people(value: str | None, *, separators: list[str] | None = None) -> list[str]:
    """Split `value` into individual people. `None`/blank -> []. Parts are
    stripped and empties dropped.

    Hinted mode (`separators` given): split on exactly those delimiters; any
    other character (commas included) is treated as name-internal.

    Auto mode (`separators is None`): split on '&'/' and '/';', then split a
    chunk on commas only when it has >=2 comma-parts that all look like full
    names (contain internal whitespace), so 'Last, First' and suffixes are kept.
    """
    if not value or not value.strip():
        return []

    if separators is not None:
        pattern = "|".join(re.escape(sep) for sep in separators)
        parts = re.split(pattern, value) if pattern else [value]
        return [p.strip() for p in parts if p.strip()]

    out: list[str] = []
    for chunk in _AUTO_SEPARATORS.split(value):
        chunk = chunk.strip()
        if not chunk:
            continue
        comma_parts = [p.strip() for p in chunk.split(",") if p.strip()]
        if len(comma_parts) >= 2 and all(_looks_like_full_name(p) for p in comma_parts):
            out.extend(comma_parts)
        else:
            out.append(chunk)
    return out
