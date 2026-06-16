"""Domain-level exceptions for Colophon."""

from __future__ import annotations


class ColophonError(Exception):
    """Base class for all Colophon domain errors."""


class IntegrityError(ColophonError):
    """A persistence invariant was violated."""
