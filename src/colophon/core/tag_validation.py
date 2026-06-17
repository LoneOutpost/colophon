"""Validate projected tags against what the container model can represent (FR-4.4).

Returns human-readable warnings; it never raises. The caller (preview) surfaces
these so nothing unrepresentable or obviously wrong is written silently. The
checks are deliberately conservative: tag containers accept almost any string,
so we only flag a missing title, an implausible year, or a negative sequence.
"""

from __future__ import annotations

from colophon.core.models import EmbeddedTags

_YEAR_MIN = 1000
_YEAR_MAX = 2100


def validate_tags(tags: EmbeddedTags) -> list[str]:
    warnings: list[str] = []
    if not tags.title:
        warnings.append("No title: the file will be written without a title tag.")
    if tags.year is not None and not (_YEAR_MIN <= tags.year <= _YEAR_MAX):
        warnings.append(f"Implausible year {tags.year} (expected {_YEAR_MIN}-{_YEAR_MAX}).")
    if tags.sequence is not None and tags.sequence < 0:
        warnings.append(f"Negative series sequence {tags.sequence}.")
    return warnings
