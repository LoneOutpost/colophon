"""ISBN normalization and ISBN-10/13 equivalence for matching (pure)."""

from __future__ import annotations


def normalize_isbn(value: str | None) -> str | None:
    """Strip hyphens/spaces and uppercase a trailing 'x'. None/empty -> None.
    Does not validate the check digit."""
    if not value:
        return None
    cleaned = value.replace("-", "").replace(" ", "").strip().upper()
    return cleaned or None


def _isbn13_check_digit(twelve: str) -> str:
    total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(twelve))
    return str((10 - total % 10) % 10)


def to_isbn13(value: str | None) -> str | None:
    """Canonical ISBN-13 for comparison. A 13-digit ISBN passes through; a 10-digit
    ISBN converts to 13 (978 prefix + recomputed check digit). None for anything that
    is not exactly 10 or 13 ISBN characters."""
    n = normalize_isbn(value)
    if n is None:
        return None
    if len(n) == 13 and n.isdigit():
        return n
    if len(n) == 10 and n[:9].isdigit() and (n[9].isdigit() or n[9] == "X"):
        body = "978" + n[:9]
        return body + _isbn13_check_digit(body)
    return None


def isbn_equal(a: str | None, b: str | None) -> bool:
    """True when a and b canonicalize to the same ISBN-13 (so an ISBN-10 matches its
    ISBN-13). False when either is missing or not a valid ISBN."""
    ca, cb = to_isbn13(a), to_isbn13(b)
    return ca is not None and ca == cb
