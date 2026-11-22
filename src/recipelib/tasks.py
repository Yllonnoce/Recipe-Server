"""Long jobs started from Settings (backup, restore, shrink, reindex, categorize) and the
backup-server sync. They run one at a time in the background and report what they are
doing, so a page can show progress instead of hanging until they finish.

A restore holds the library for its whole run: while one runs, the capture queue does
not start jobs and the web app refuses changes (see `restoring`).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

log = logging.getLogger(__name__)

Report = Callable[..., None]   # report(step, done=None, total=None)


class Busy(RuntimeError):
    """Another job is running; its label is the message."""


_lock = threading.Lock()
_current: dict | None = None
_last: dict[str, dict] = {}


def _report_into(t: dict) -> Report:
    def report(step: str | None = None, done: int | None = None, total: int | None = None) -> None:
        if step is not None and step != t["step"]:
            t.update(step=step, done=None, total=None)
        if total is not None:
            t["total"] = total
        if done is not None:
            t["done"] = done
    return report


class _Job:
    """Holds the one-at-a-time lock from `begin` to `end`, which may run on different threads."""

    def __init__(self, kind: str, label: str, blocks_writes: bool):
        self.t = {"kind": kind, "label": label, "step": None, "done": None, "total": None, "running": True,
                  "ok": None, "message": None, "blocks_writes": blocks_writes, "t0": time.monotonic(),
                  "finished_at": None, "seconds": None}

    def begin(self) -> Report:
        global _current
        if not _lock.acquire(blocking=False):
            cur = _current
            raise Busy(f"{cur['label']} is still running" if cur else "another job is still running")
        _current = self.t
        return _report_into(self.t)

    def end(self, ok: bool, message: str | None) -> None:
        global _current
        self.t.update(running=False, ok=ok, message=message, finished_at=datetime.now(),
                      seconds=int(time.monotonic() - self.t["t0"]))
        _last[self.t["kind"]] = self.t
        _current = None
        _lock.release()


class exclusive:
    """`with exclusive(kind, label) as report:` runs the body as the one job, on this thread.
    Raises Busy straight away if another job is running."""

    def __init__(self, kind: str, label: str, blocks_writes: bool = False):
        self.job = _Job(kind, label, blocks_writes)

    def __enter__(self) -> Report:
        return self.job.begin()

    def __exit__(self, et, e, tb) -> None:
        self.job.end(e is None, None if e is None else _explain(e))


def start(kind: str, label: str, fn: Callable[[Report], str | None], blocks_writes: bool = False) -> None:
    """Run `fn(report)` in the background as the one job; what it returns is the result line.
    Raises Busy (before starting anything) if another job is running."""
    job = _Job(kind, label, blocks_writes)
    report = job.begin()

    def run():
        try:
            msg = fn(report)
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed", label)
            job.end(False, _explain(e))
        else:
            job.end(True, msg)
    threading.Thread(target=run, name=f"task-{kind}", daemon=True).start()


def _explain(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


def current() -> dict | None:
    return _current


def restoring() -> bool:
    cur = _current
    return bool(cur and cur["blocks_writes"])


def _fmt(sec: int) -> str:
    return f"{sec}s" if sec < 60 else f"{sec // 60}m {sec % 60:02d}s"


def status(kind: str) -> dict | None:
    """What a page shows for this kind of job: the running one, else the last one that finished."""
    cur = _current
    t = cur if cur and cur["kind"] == kind else _last.get(kind)
    if t is None:
        return None
    pct = int(100 * t["done"] / t["total"]) if t["total"] and t["done"] is not None else None
    secs = int(time.monotonic() - t["t0"]) if t["running"] else t["seconds"]
    return {**t, "pct": pct, "elapsed": _fmt(secs or 0),
            "finished": t["finished_at"].strftime("%H:%M") if t["finished_at"] else None}


def clear() -> None:
    """Forget finished jobs (tests)."""
    _last.clear()
