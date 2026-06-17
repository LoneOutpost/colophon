"""Domain-level exceptions for Colophon."""

from __future__ import annotations


class ColophonError(Exception):
    """Base class for all Colophon domain errors."""


class IntegrityError(ColophonError):
    """A persistence invariant was violated."""


class TagWriteError(ColophonError):
    """Writing tags or cover art to an audio file failed, or the format is unsupported."""
