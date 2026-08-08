"""Step logging for long operations: a start line, a periodic heartbeat while it runs, and a
done/failed line with elapsed time. Always-on at INFO on the `colophon.progress` logger — distinct
from the COLOPHON_PERF timing tree (core/perf.py). Usable as a sync OR an async context manager; the
heartbeat is a daemon thread, so it ticks around blocking work and around `await` alike."""

from __future__ import annotations

import logging
import os
import threading
from time import perf_counter

logger = logging.getLogger("colophon.progress")


def _default_interval() -> float:
    try:
        v = float(os.environ.get("COLOPHON_HEARTBEAT_SECS", "5"))
    except ValueError:
        return 5.0
    return v if v > 0 else 5.0


class step:
    """Bracket a long operation with start / heartbeat / done logging. `interval` seconds between
    heartbeats (default from COLOPHON_HEARTBEAT_SECS, else 5). A step faster than one interval emits
    no heartbeat. Never swallows: an exception is logged as `failed` and re-raised."""

    def __init__(self, label: str, *, interval: float | None = None) -> None:
        self.label = label
        self.interval = interval if interval is not None else _default_interval()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def _start(self) -> None:
        self._t0 = perf_counter()
        logger.info(f"{self.label}: starting")
        self._done.clear()
        self._thread = threading.Thread(target=self._beat, daemon=True,
                                        name=f"progress:{self.label}")
        self._thread.start()

    def _beat(self) -> None:
        while not self._done.wait(self.interval):
            logger.info(f"{self.label}: still running ({perf_counter() - self._t0:.0f}s)")

    def _stop(self, exc: BaseException | None) -> None:
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        elapsed = perf_counter() - self._t0
        if exc is None:
            logger.info(f"{self.label}: done in {elapsed:.1f}s")
        else:
            logger.warning(f"{self.label}: failed after {elapsed:.1f}s: {exc}")

    def __enter__(self) -> step:
        self._start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stop(exc)
        return False

    async def __aenter__(self) -> step:
        self._start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._stop(exc)
        return False
