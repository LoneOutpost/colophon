"""Infer author/series/title from a book folder's position in a directory tree.

Driven by a positional scheme like "Author/Series/Title". To avoid mis-assigning
the wrong field on irregular layouts (e.g. a standalone "Author/Title" book under
an Author/Series/Title scheme), inference only fires when the folder's depth under
the scan root exactly matches the scheme length. Weak evidence: the caller slots
the result below embedded tags and the sidecar in reconcile's precedence.
"""

from __future__ import annotations

from pathlib import Path

_KNOWN_FIELDS = {"author", "series", "title"}


def parse_scheme(spec: str) -> list[str]:
    """Parse 'Author/Series/Title' into ['author', 'series', 'title']. Unknown
    tokens are kept as positional placeholders (skipped during inference)."""
    return [token.strip().lower() for token in spec.split("/") if token.strip()]


def infer_from_path(folder: Path, root: Path, scheme: list[str]) -> dict[str, str]:
    """Map `folder`'s path components (relative to `root`) to fields per `scheme`,
    only when the depth matches the scheme length. Returns author/series/title
    values found; empty dict if the scheme is empty, the depth mismatches, or the
    folder is not under root."""
    if not scheme:
        return {}
    try:
        parts = folder.relative_to(root).parts
    except ValueError:
        return {}
    if len(parts) != len(scheme):
        return {}
    return {field: parts[i] for i, field in enumerate(scheme) if field in _KNOWN_FIELDS and parts[i]}
