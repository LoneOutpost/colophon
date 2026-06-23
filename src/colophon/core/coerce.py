"""Shared string-to-number coercion helpers for tag/filename evidence.

All helpers return ``None`` rather than raising on missing or unparseable
input, so callers can use them directly when populating optional fields.
"""

from __future__ import annotations


def to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def to_float(value: str | int | float | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def year_or_none(value: str | None) -> int | None:
    """Extract a 4-digit year from a date-ish string (e.g. ``"2021-05"`` -> 2021)."""
    if value is None:
        return None
    try:
        return int(str(value)[:4])
    except ValueError:
        return None
