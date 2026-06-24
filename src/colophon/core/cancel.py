"""A cooperative cancel flag, checked between units of work (graceful: in-flight
work finishes; not-yet-started work is skipped)."""

from __future__ import annotations


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True
