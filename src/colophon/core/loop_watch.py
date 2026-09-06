"""Detect a starved asyncio event loop, and name what starved it.

Blocking the loop is Colophon's recurring failure: NiceGUI derives its socket heartbeat from
``reconnect_timeout`` (4s ping interval + 2s timeout by default), so roughly six seconds of
synchronous work in a handler makes the browser drop the connection. The client is then pruned and
the reconnect reloads the page — silently, taking any open dialog and the pending action with it.
Nothing in the app reports that; it has only ever been found by a person noticing afterwards.

The watch runs on a daemon *thread*, not a task, for two reasons. A task cannot run while the loop
is blocked, so it could only ever measure a stall after the fact; and only another thread can read
the main thread's frames *during* the block, which is what turns "something stalled" into a stack
naming the call. Cost while healthy is one wakeup per interval and one loop callback.

Always on by default (set ``COLOPHON_LOOPWATCH=0`` to disable), logging to ``colophon.loopwatch``.
Distinct from the opt-in COLOPHON_PERF timing tree (core/perf.py): this reports a fault, not a
measurement.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter

logger = logging.getLogger("colophon.loopwatch")

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_INTERVAL = 0.5   # how often to ping the loop
_DEFAULT_THRESHOLD = 1.0  # unanswered for this long -> the loop is blocked
_STACK_DEPTH = 8          # innermost frames kept in the report


def _env_float(name: str, fallback: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return fallback
    return value if value > 0 else fallback


def enabled() -> bool:
    """Whether the watch should run (COLOPHON_LOOPWATCH, default on)."""
    return os.environ.get("COLOPHON_LOOPWATCH", "1").strip().lower() in _TRUTHY


@dataclass
class LoopStall:
    """One episode of the event loop failing to service a callback."""

    seconds: float
    stack: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        where = f" — in {self.stack[-1]}" if self.stack else ""
        return f"event loop blocked for {self.seconds:.1f}s{where}"


def _frames_of(thread_id: int) -> list[str]:
    """The innermost frames of `thread_id` right now, Colophon's own preferred.

    Read while the loop is still blocked, so these are the frames doing the blocking. Falls back to
    the raw innermost frames when nothing in the stack belongs to Colophon (e.g. blocked inside a
    library call reached from a callback we don't own).
    """
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return []
    entries = traceback.extract_stack(frame)
    ours = [e for e in entries if "colophon" in e.filename] or entries
    return [f"{os.path.basename(e.filename)}:{e.lineno} in {e.name}" for e in ours[-_STACK_DEPTH:]]


def _report(stall: LoopStall) -> None:
    detail = "\n".join(f"    at {frame}" for frame in reversed(stall.stack))
    logger.warning(
        "%s; the browser drops the page at ~6s\n%s", stall, detail or "    (no stack captured)"
    )


def start_loop_watch(
    *,
    interval: float | None = None,
    threshold: float | None = None,
    on_stall: Callable[[LoopStall], None] = _report,
) -> Callable[[], None]:
    """Watch the running loop until the returned stop callable is invoked.

    Every `interval` seconds the watcher asks the loop to set an event. If that takes longer than
    `threshold`, the loop is blocked: it captures the main thread's stack immediately, waits for the
    loop to come back, and hands `on_stall` the total duration plus the stack. One report per
    episode, so a single long block does not produce a stream of warnings.
    """
    interval = interval if interval is not None else _env_float("COLOPHON_LOOPWATCH_INTERVAL",
                                                                _DEFAULT_INTERVAL)
    threshold = threshold if threshold is not None else _env_float("COLOPHON_LOOPWATCH_THRESHOLD",
                                                                   _DEFAULT_THRESHOLD)
    loop = asyncio.get_running_loop()
    main_id = threading.main_thread().ident or 0
    stopping = threading.Event()

    def run() -> None:
        while not stopping.wait(interval):
            answered = threading.Event()
            sent = perf_counter()
            try:
                loop.call_soon_threadsafe(answered.set)
            except RuntimeError:
                return  # loop closed underneath us; nothing left to watch
            if answered.wait(threshold):
                continue
            stack = _frames_of(main_id)  # captured while it is still stuck
            while not answered.wait(interval):
                if stopping.is_set():
                    return
            stall = LoopStall(seconds=perf_counter() - sent, stack=stack)
            try:
                on_stall(stall)
            except Exception:  # a reporting failure must never kill the watch
                logger.exception("loop-stall handler raised")

    thread = threading.Thread(target=run, name="colophon-loopwatch", daemon=True)
    thread.start()
    return stopping.set
