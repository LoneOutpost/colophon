"""Opt-in timing spans for locating UI/render bottlenecks.

Enabled with ``COLOPHON_PERF=1`` (or any of 1/true/yes/on). When disabled,
``span()`` and ``timed()`` cost a single boolean check — no clock reads, no
allocation — so they are safe to leave in hot render paths. When enabled, each
span records its wall-clock duration and the top-level span emits the whole
nested tree at once to the ``colophon.perf`` logger, so a slow render reads
top-down and attributes to its slowest child::

    render / workspace: 412.3ms
      library_tree: 210.1ms
      dashboard_stats: 95.2ms
      nav render: 22.7ms
      list render: 40.1ms

Nesting is tracked with a ``ContextVar``, so concurrent page renders (each its
own asyncio task) build independent trees and never interleave.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger("colophon.perf")

_TRUTHY = {"1", "true", "yes", "on"}
_ENABLED = os.environ.get("COLOPHON_PERF", "").strip().lower() in _TRUTHY


def enabled() -> bool:
    """Whether timing is active (COLOPHON_PERF was truthy at startup)."""
    return _ENABLED


@dataclass
class _Span:
    label: str
    elapsed_ms: float = 0.0
    children: list[_Span] = field(default_factory=list)


_current: ContextVar[_Span | None] = ContextVar("colophon_perf_current", default=None)


@contextmanager
def span(label: str) -> Iterator[None]:
    """Time the enclosed block. A no-op when timing is disabled. Nested spans roll
    up under their parent; the outermost span logs the whole tree when it closes."""
    if not _ENABLED:
        yield
        return
    parent = _current.get()
    node = _Span(label)
    token = _current.set(node)
    start = time.perf_counter()
    try:
        yield
    finally:
        node.elapsed_ms = (time.perf_counter() - start) * 1000.0
        _current.reset(token)
        if parent is None:
            _emit(node)
        else:
            parent.children.append(node)


def _emit(root: _Span) -> None:
    lines: list[str] = []

    def walk(node: _Span, depth: int) -> None:
        lines.append(f"{'  ' * depth}{node.label}: {node.elapsed_ms:.1f}ms")
        for child in node.children:
            walk(child, depth + 1)

    walk(root, 0)
    logger.info("render timing —\n%s", "\n".join(lines))


def timed(label: str | None = None) -> Callable:
    """Decorate a function so each call is a :func:`span`. Handles sync and async
    callables. ``label`` defaults to the function's qualified name."""

    def decorate(fn: Callable) -> Callable:
        name = label or fn.__qualname__
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: object, **kwargs: object) -> object:
                with span(name):
                    return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            with span(name):
                return fn(*args, **kwargs)

        return wrapper

    return decorate
