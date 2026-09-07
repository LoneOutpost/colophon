"""Domain-level exceptions for Colophon."""

from __future__ import annotations


def describe(exc: BaseException) -> str:
    """A log-safe description of `exc`, never empty.

    Some libraries raise wordless exceptions — `mutagen.id3.error()` on a corrupt MP3 carries no
    message at all — so interpolating the exception alone yields "…failed:" and nothing, which is
    what a real persist logged for two unreadable files. Always name the type; add the message only
    when there is one.
    """
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class ColophonError(Exception):
    """Base class for all Colophon domain errors."""


class IntegrityError(ColophonError):
    """A persistence invariant was violated."""


class TagWriteError(ColophonError):
    """Writing tags or cover art to an audio file failed, or the format is unsupported."""
