"""DB-backed job queue with a small thread pool.

Jobs live in `capture_jobs`; workers claim the oldest queued job whose
next_attempt_at has passed, run the pipeline, and record the outcome. The
queue survives restarts because the DB is the queue. `wake()` nudges the
workers so a fresh job starts within milliseconds rather than at the next poll.
"""
from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..db.engine import session_scope
from ..db.models import CaptureJob, utcnow

log = logging.getLogger(__name__)

_llm_lock = threading.Semaphore(1)


class JobQueue:
    def __init__(self, workers: int = 2, poll_seconds: float = 3.0):
        self.workers = max(1, workers)
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Condition()
        self._threads: list[threading.Thread] = []
        self._claim_lock = threading.Lock()
        self.llm_lock = _llm_lock

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._recover()
        for i in range(self.workers):
            t = threading.Thread(target=self._run, name=f"capture-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("capture queue started with %d worker(s)", self.workers)

    def stop(self) -> None:
        self._stop.set()
        self.wake()
        for t in self._threads:
            t.join(timeout=5)

    def wake(self) -> None:
        with self._wake:
            self._wake.notify_all()

    def _recover(self) -> None:
        """Jobs left 'running' by a crash go back to queued."""
        with session_scope() as s:
            for j in s.scalars(select(CaptureJob).where(CaptureJob.state == "running")):
                j.state = "queued"
                j.log("recover", "server restarted mid-job; re-queued")

    # ---- enqueue -----------------------------------------------------------
    def enqueue(self, source: str, *, pdf_path: str | None = None, url: str | None = None,
                title_hint: str | None = None, ipp_job_id: int | None = None,
                force: bool = False, priority: int = 0) -> int:
        with session_scope() as s:
            j = CaptureJob(source=source, input_path=pdf_path, input_url=url, title_hint=title_hint,
                           ipp_job_id=ipp_job_id, force=1 if force else 0, priority=priority,
                           state="queued", created_at=utcnow())
            j.log("queued")
            s.add(j)
            s.flush()
            jid = j.id
        self.wake()
        return jid

    def retry(self, job_id: int) -> bool:
        with session_scope() as s:
            j = s.get(CaptureJob, job_id)
            if j is None or j.state == "running":
                return False
            j.state = "queued"
            j.next_attempt_at = None
            j.last_error = None
            j.log("retry", "manual retry")
        self.wake()
        return True

    def cancel(self, job_id: int) -> bool:
        with session_scope() as s:
            j = s.get(CaptureJob, job_id)
            if j is None or j.state in ("done", "running"):
                return False
            j.state = "canceled"
            j.finished_at = utcnow()
            j.log("canceled")
        return True

    # ---- worker loop -------------------------------------------------------
    def _claim(self) -> int | None:
        now = utcnow()
        with self._claim_lock, session_scope() as s:
            j = s.scalars(
                select(CaptureJob)
                .where(CaptureJob.state.in_(["queued", "waiting_llm"]))
                .where((CaptureJob.next_attempt_at.is_(None)) | (CaptureJob.next_attempt_at <= now))
                .order_by(CaptureJob.priority.desc(), CaptureJob.id)
                .limit(1)
            ).first()
            if j is None:
                return None
            j.state = "running"
            j.attempts += 1
            j.started_at = j.started_at or now
            return j.id

    def _run(self) -> None:
        from .pipeline import InputGone, LLMUnavailable, run_job
        while not self._stop.is_set():
            jid = None
            try:
                jid = self._claim()
            except Exception:
                log.exception("claim failed")
            if jid is None:
                with self._wake:
                    self._wake.wait(self.poll_seconds)
                continue
            try:
                run_job(jid, self)
            except LLMUnavailable as e:
                self._defer(jid, str(e))
            except InputGone as e:
                log.warning("job %s: %s", jid, e)
                self._fail(jid, str(e))
            except Exception as e:  # noqa: BLE001
                log.error("job %s failed: %s\n%s", jid, e, traceback.format_exc())
                self._fail(jid, f"{type(e).__name__}: {e}")

    def _defer(self, jid: int, why: str) -> None:
        with session_scope() as s:
            j = s.get(CaptureJob, jid)
            if j is None:
                return
            backoff = [60, 300, 900, 3600, 3600 * 3, 3600 * 6]
            delay = backoff[min(j.attempts - 1, len(backoff) - 1)]
            j.state = "waiting_llm"
            j.next_attempt_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()
            j.last_error = why
            j.log("waiting_llm", f"{why}; retry in {delay}s")

    def _fail(self, jid: int, err: str) -> None:
        with session_scope() as s:
            j = s.get(CaptureJob, jid)
            if j is None:
                return
            j.state = "failed"
            j.last_error = err[:2000]
            j.finished_at = utcnow()
            j.log("failed", err[:300])
            if j.recipe_id:
                from ..db.models import Recipe
                r = s.get(Recipe, j.recipe_id)
                if r is not None and r.status == "processing":
                    r.status = "needs_review"


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    if _queue is None:
        raise RuntimeError("queue not started")
    return _queue


def set_queue(q: JobQueue | None) -> None:
    global _queue
    _queue = q
