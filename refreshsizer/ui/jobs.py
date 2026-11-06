"""Background jobs (ingest, sizing + Monte Carlo, reports, benchmark update) with progress for HTMX polling.

Each job runs in its own daemon thread, so one slow or stuck job never blocks the others; jobs of the same
project are serialized by a per-project lock, and chart rendering is serialized in report/charts.py."""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("refreshsizer.ui")


@dataclass
class Job:
    id: str
    kind: str
    project: str | None
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    redirect: str | None = None
    error: str | None = None
    result: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    log: list[str] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None
    last_update: float | None = None

    @property
    def stalled_s(self) -> int:
        """Seconds since the last progress message while running (0 otherwise)."""
        import time

        if self.status != "running" or self.last_update is None:
            return 0
        return int(time.monotonic() - self.last_update)

    @property
    def elapsed(self) -> str:
        import time

        if self.started_at is None:
            return "waiting for the previous job of this project"
        s = int((self.finished_at or time.monotonic()) - self.started_at)
        return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"

    @property
    def percent(self) -> int:
        return int(round(100 * max(0.0, min(1.0, self.progress))))


def friendly_error(e: Exception) -> str:
    """pydantic validation errors -> their message only; others -> 'Type: message'."""
    errs = getattr(e, "errors", None)
    if callable(errs):
        try:
            return "; ".join(str(x.get("msg", x)).removeprefix("Value error, ") for x in errs())
        except Exception:
            pass
    return f"{type(e).__name__}: {e}"


JobFn = Callable[[Callable[[float, str], None], "Job"], str | None]


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, project: str | None) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(project or "<global>", threading.Lock())

    def submit(self, kind: str, project: str | None, fn: JobFn, redirect: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, project=project, redirect=redirect)
        self.jobs[job.id] = job

        def progress(frac: float, msg: str) -> None:
            import time

            job.last_update = time.monotonic()
            job.progress = max(job.progress, min(0.99, frac))
            job.message = msg
            job.log.append(msg)

        def run() -> None:
            import time

            lock = self._lock_for(project)
            lock.acquire()
            job.status = "running"
            job.started_at = job.last_update = time.monotonic()
            log.info("job %s (%s, %s) started", job.id, kind, project)
            try:
                r = fn(progress, job)
                if r:
                    job.redirect = r
                job.progress = 1.0
                job.status = "done"
                job.message = job.message if job.result else "done"
                log.info("job %s done in %s", job.id, job.elapsed)
            except Exception as e:  # report in the UI, never kill the server
                job.status = "error"
                job.error = friendly_error(e)
                log.error("job %s failed:\n%s", job.id, traceback.format_exc())
            finally:
                job.finished_at = time.monotonic()
                lock.release()

        threading.Thread(target=run, name=f"refreshsizer-job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def wait(self, job_id: str, timeout: float = 120.0) -> Job:
        """Block until the job finishes (tests)."""
        import time

        end = time.monotonic() + timeout
        job = self.jobs[job_id]
        while job.status in ("queued", "running") and time.monotonic() < end:
            time.sleep(0.05)
        return job
