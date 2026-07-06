"""A shared, in-memory registry of active background jobs.

Long operations (scan, re-probe, encode/organize, downloads) run in a worker thread with a
per-client progress callback, so only the tab that launched one could see it. The registry is
server-side and shared, so ANY session can render the live set of running jobs. In-memory only —
a restart clears it, which is correct: a restart also kills the jobs.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Job:
    """One tracked background operation. `total` is None until the work knows its size."""

    id: int
    label: str
    started_at: datetime
    done: int = 0
    total: int | None = None
    detail: str = ""

    @property
    def fraction(self) -> float | None:
        """Completion in [0, 1], or None when the total isn't known yet."""
        if not self.total:
            return None
        return max(0.0, min(1.0, self.done / self.total))


class JobRegistry:
    """Thread-safe registry of active jobs. `track()` is the normal entry point: it registers a
    job, hands back a `JobHandle` whose `.progress(done, total, detail)` matches the existing
    progress-callback signature, and clears the job when the block exits (even on error)."""

    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def active(self) -> list[Job]:
        """A snapshot of running jobs, oldest first — the registry only holds running jobs (a job is
        removed when its block exits). Copies each so callers can read without the lock."""
        with self._lock:
            return sorted((Job(**vars(j)) for j in self._jobs.values()), key=lambda j: j.started_at)

    def _start(self, label: str) -> Job:
        with self._lock:
            job = Job(id=next(self._ids), label=label, started_at=datetime.now(UTC))
            self._jobs[job.id] = job
            return job

    def _update(self, job_id: int, done: int, total: int | None, detail: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.done, job.total, job.detail = done, total, detail

    def _finish(self, job_id: int) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def track(self, label: str) -> JobHandle:
        return JobHandle(self, self._start(label))


@dataclass
class JobHandle:
    """Context manager + progress sink for one tracked job. `progress(done, total, detail)` is the
    same shape the operations already emit, so wiring is a one-line pass-through."""

    registry: JobRegistry
    job: Job = field(repr=False)

    def progress(self, done: int, total: int | None = None, detail: str = "") -> None:
        self.registry._update(self.job.id, done, total, detail)

    def __enter__(self) -> JobHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.registry._finish(self.job.id)
