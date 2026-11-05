"""Background jobs (ingest, sizing + Monte Carlo, reports, benchmark update) with progress for HTMX polling.

One worker thread: jobs run one after another, which also keeps matplotlib (not thread-safe) safe."""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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

    @property
    def percent(self) -> int:
        return int(round(100 * max(0.0, min(1.0, self.progress))))


JobFn = Callable[[Callable[[float, str], None], "Job"], str | None]


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refreshsizer-job")

    def submit(self, kind: str, project: str | None, fn: JobFn, redirect: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, project=project, redirect=redirect)
        self.jobs[job.id] = job

        def progress(frac: float, msg: str) -> None:
            job.progress = max(job.progress, min(0.99, frac))
            job.message = msg
            job.log.append(msg)

        def run() -> None:
            job.status = "running"
            try:
                r = fn(progress, job)
                if r:
                    job.redirect = r
                job.progress = 1.0
                job.status = "done"
                job.message = job.message if job.result else "done"
            except Exception as e:  # report in the UI, never kill the server
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                log.error("job %s failed:\n%s", job.id, traceback.format_exc())

        self.pool.submit(run)
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
