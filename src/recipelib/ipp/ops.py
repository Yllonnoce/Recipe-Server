"""IPP operation handlers and the in-memory job table."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import attrs as A
from . import codec as C
from .codec import Attr, Group, Message

log = logging.getLogger(__name__)

# operations
PRINT_JOB, VALIDATE_JOB, CREATE_JOB, SEND_DOCUMENT = 0x0002, 0x0004, 0x0005, 0x0006
CANCEL_JOB, GET_JOB_ATTRIBUTES, GET_JOBS, GET_PRINTER_ATTRIBUTES = 0x0008, 0x0009, 0x000A, 0x000B
GET_PRINTER_SUPPORTED_VALUES, CANCEL_MY_JOBS, CLOSE_JOB, IDENTIFY_PRINTER = 0x0015, 0x0039, 0x003B, 0x003C

# status codes
OK, OK_IGNORED = 0x0000, 0x0001
BAD_REQUEST, NOT_FOUND, NOT_POSSIBLE, DOC_FORMAT_ERROR = 0x0400, 0x0406, 0x0409, 0x040A
ATTRS_NOT_SUPPORTED, OP_NOT_SUPPORTED, INTERNAL = 0x040B, 0x0501, 0x0500

# job states
PENDING, PENDING_HELD, PROCESSING, PROCESSING_STOPPED, CANCELED, ABORTED, COMPLETED = 3, 4, 5, 6, 7, 8, 9

JOB_ATTRS_DEFAULT = ["job-id", "job-uri"]


@dataclass
class Job:
    id: int
    name: str
    user: str
    printer_uri: str
    state: int = PENDING
    reasons: list[str] = field(default_factory=lambda: ["none"])
    created: int = field(default_factory=lambda: int(time.time()))
    processing: int | None = None
    completed: int | None = None
    fmt: str = "application/pdf"
    impressions: int = 0
    documents: list[Path] = field(default_factory=list)
    created_dt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def uri(self) -> str:
        return f"{self.printer_uri}/{self.id}"

    def attributes(self, requested: set[str] | None) -> list[Attr]:
        all_attrs = [
            Attr("job-id", C.INTEGER, [self.id]),
            Attr("job-uri", C.URI, [self.uri]),
            Attr("job-name", C.NAME, [self.name]),
            Attr("job-originating-user-name", C.NAME, [self.user]),
            Attr("job-printer-uri", C.URI, [self.printer_uri]),
            Attr("job-state", C.ENUM, [self.state]),
            Attr("job-state-reasons", C.KEYWORD, list(self.reasons)),
            Attr("job-state-message", C.TEXT, [{PENDING: "Waiting", PROCESSING: "Saving", COMPLETED: "Saved to the recipe library",
                                                CANCELED: "Canceled", ABORTED: "Could not read the document"}.get(self.state, "")]),
            Attr("time-at-creation", C.INTEGER, [self.created]),
            Attr("time-at-processing", C.INTEGER if self.processing else C.NO_VALUE, [self.processing] if self.processing else []),
            Attr("time-at-completed", C.INTEGER if self.completed else C.NO_VALUE, [self.completed] if self.completed else []),
            Attr("date-time-at-creation", C.DATE_TIME, [self.created_dt]),
            Attr("job-impressions-completed", C.INTEGER, [self.impressions]),
            Attr("job-printer-up-time", C.INTEGER, [int(time.time())]),
            Attr("document-format", C.MIME, [self.fmt]),
        ]
        if not requested or "all" in requested or "job-description" in requested:
            return all_attrs
        return [a for a in all_attrs if a.name in requested]


@dataclass
class Document:
    """A received document handed to the ingest side."""
    job: Job
    path: Path
    fmt: str


class Printer:
    """Job table + operation dispatch. `on_document` is called (in the request
    thread) once a job's document is on disk; it should be quick."""

    def __init__(self, info: A.PrinterInfo, spool_dir: Path, on_document: Callable[[Document], None],
                 first_job_id: int = 1):
        self.info = info
        self.spool = spool_dir
        self.on_document = on_document
        self.jobs: dict[int, Job] = {}
        self._next = max(1, first_job_id)
        self._lock = threading.Lock()

    # ---- helpers -----------------------------------------------------------
    def _new_job(self, req: Message) -> Job:
        with self._lock:
            jid = self._next
            self._next += 1
            name = _text(req.value("job-name")) or "Printed recipe"
            user = _text(req.value("requesting-user-name")) or "anonymous"
            job = Job(id=jid, name=name[:200], user=user[:100], printer_uri=self.info.uris[0])
            self.jobs[jid] = job
            # keep the table small
            if len(self.jobs) > 200:
                for old in sorted(self.jobs)[:-100]:
                    if self.jobs[old].state >= CANCELED:
                        del self.jobs[old]
        return job

    def queued_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.state < CANCELED)

    def _response(self, req: Message, status: int) -> Message:
        resp = Message(version=req.version if req.version[0] in (1, 2) else (2, 0), code=status, request_id=req.request_id)
        g = Group(C.OPERATION_GROUP)
        g.add("attributes-charset", C.CHARSET, "utf-8")
        g.add("attributes-natural-language", C.LANGUAGE, "en")
        resp.groups.append(g)
        return resp

    def _error(self, req: Message, status: int, msg: str) -> Message:
        resp = self._response(req, status)
        resp.groups[0].add("status-message", C.TEXT, msg)
        return resp

    def _job_group(self, job: Job, requested: set[str] | None) -> Group:
        return Group(C.JOB_GROUP, job.attributes(requested))

    def _find_job(self, req: Message) -> Job | None:
        jid = req.value("job-id")
        if jid is None:
            uri = req.value("job-uri") or ""
            try:
                jid = int(str(uri).rstrip("/").rsplit("/", 1)[-1])
            except ValueError:
                return None
        try:
            return self.jobs.get(int(jid))
        except (TypeError, ValueError):
            return None

    # ---- dispatch ----------------------------------------------------------
    def handle(self, req: Message, document: bytes | Path | None) -> Message:
        op = req.code
        try:
            if op == GET_PRINTER_ATTRIBUTES or op == GET_PRINTER_SUPPORTED_VALUES:
                return self.op_get_printer_attributes(req)
            if op == VALIDATE_JOB:
                return self.op_validate_job(req)
            if op == PRINT_JOB:
                return self.op_print_job(req, document)
            if op == CREATE_JOB:
                return self.op_create_job(req)
            if op == SEND_DOCUMENT:
                return self.op_send_document(req, document)
            if op == GET_JOBS:
                return self.op_get_jobs(req)
            if op == GET_JOB_ATTRIBUTES:
                return self.op_get_job_attributes(req)
            if op == CANCEL_JOB:
                return self.op_cancel_job(req)
            if op == CLOSE_JOB:
                return self.op_close_job(req)
            if op == IDENTIFY_PRINTER:
                return self._response(req, OK)
            if op == CANCEL_MY_JOBS:
                for j in self.jobs.values():
                    if j.state < PROCESSING:
                        j.state, j.reasons, j.completed = CANCELED, ["job-canceled-by-user"], int(time.time())
                return self._response(req, OK)
            return self._error(req, OP_NOT_SUPPORTED, f"operation {op:#06x} not supported")
        except Exception as e:  # noqa: BLE001
            log.exception("IPP op %#06x failed", op)
            return self._error(req, INTERNAL, f"{type(e).__name__}: {e}")

    # ---- operations --------------------------------------------------------
    def op_get_printer_attributes(self, req: Message) -> Message:
        requested = [str(v) for v in req.values("requested-attributes")] or None
        resp = self._response(req, OK)
        resp.groups.append(Group(C.PRINTER_GROUP, A.printer_attributes(self.info, self.queued_count(), requested)))
        return resp

    def _check_format(self, req: Message) -> tuple[str, Message | None]:
        fmt = str(req.value("document-format") or "application/octet-stream")
        if fmt not in A.FORMATS:
            return fmt, self._error(req, DOC_FORMAT_ERROR, f"document format {fmt} not supported")
        return fmt, None

    def _unsupported(self, req: Message, resp: Message) -> None:
        """Echo job-template attributes we ignore in the unsupported group."""
        job_group = req.group(C.JOB_GROUP)
        if not job_group:
            return
        ignored = [a for a in job_group.attrs if a.name in ("copies", "number-up", "page-ranges", "job-sheets", "finishings")
                   and not (a.name == "copies" and a.value == 1)]
        if ignored:
            resp.code = OK_IGNORED
            resp.groups.append(Group(C.UNSUPPORTED_GROUP, [Attr(a.name, C.UNSUPPORTED, []) for a in ignored]))

    def op_validate_job(self, req: Message) -> Message:
        _fmt, err = self._check_format(req)
        if err:
            return err
        resp = self._response(req, OK)
        self._unsupported(req, resp)
        return resp

    def op_print_job(self, req: Message, document: bytes | Path | None) -> Message:
        fmt, err = self._check_format(req)
        if err:
            return err
        job = self._new_job(req)
        job.fmt = fmt
        self._receive(job, document, last=True)
        resp = self._response(req, OK)
        self._unsupported(req, resp)
        resp.groups.append(self._job_group(job, None))
        return resp

    def op_create_job(self, req: Message) -> Message:
        job = self._new_job(req)
        resp = self._response(req, OK)
        self._unsupported(req, resp)
        resp.groups.append(self._job_group(job, None))
        return resp

    def op_send_document(self, req: Message, document: bytes | Path | None) -> Message:
        job = self._find_job(req)
        if job is None:
            return self._error(req, NOT_FOUND, "job not found")
        if job.state >= CANCELED:
            return self._error(req, NOT_POSSIBLE, "job is no longer accepting documents")
        last = bool(req.value("last-document", True))
        fmt, err = self._check_format(req)
        if err:
            return err
        job.fmt = fmt
        if document is not None and _size(document) > 0:
            self._receive(job, document, last=last)
        elif last:
            self._finish(job)
        resp = self._response(req, OK)
        resp.groups.append(self._job_group(job, None))
        return resp

    def op_close_job(self, req: Message) -> Message:
        job = self._find_job(req)
        if job is None:
            return self._error(req, NOT_FOUND, "job not found")
        if job.state < CANCELED:
            self._finish(job)
        resp = self._response(req, OK)
        resp.groups.append(self._job_group(job, None))
        return resp

    def op_get_jobs(self, req: Message) -> Message:
        which = str(req.value("which-jobs") or "not-completed")
        requested = set(str(v) for v in req.values("requested-attributes")) or set(JOB_ATTRS_DEFAULT)
        limit = int(req.value("limit") or 0) or None
        resp = self._response(req, OK)
        jobs = sorted(self.jobs.values(), key=lambda j: j.id)
        if which == "completed":
            jobs = [j for j in jobs if j.state >= CANCELED][::-1]
        elif which == "not-completed":
            jobs = [j for j in jobs if j.state < CANCELED]
        elif which in ("aborted", "canceled", "pending", "processing"):
            st = {"aborted": ABORTED, "canceled": CANCELED, "pending": PENDING, "processing": PROCESSING}[which]
            jobs = [j for j in jobs if j.state == st]
        for j in jobs[:limit]:
            resp.groups.append(self._job_group(j, requested))
        return resp

    def op_get_job_attributes(self, req: Message) -> Message:
        job = self._find_job(req)
        if job is None:
            return self._error(req, NOT_FOUND, "job not found")
        requested = set(str(v) for v in req.values("requested-attributes")) or None
        resp = self._response(req, OK)
        resp.groups.append(self._job_group(job, requested))
        return resp

    def op_cancel_job(self, req: Message) -> Message:
        job = self._find_job(req)
        if job is None:
            return self._error(req, NOT_FOUND, "job not found")
        if job.state >= CANCELED:
            return self._error(req, NOT_POSSIBLE, "job already finished")
        job.state = CANCELED
        job.reasons = ["job-canceled-by-user"]
        job.completed = int(time.time())
        for p in job.documents:
            try:
                p.unlink()
            except OSError:
                pass
        return self._response(req, OK)

    # ---- documents ---------------------------------------------------------
    def _receive(self, job: Job, document: bytes | Path | None, last: bool) -> None:
        job.state = PROCESSING
        job.processing = int(time.time())
        self.spool.mkdir(parents=True, exist_ok=True)
        n = len(job.documents) + 1
        dest = self.spool / f"job{job.id:06d}-{n}.bin"
        if isinstance(document, Path):
            document.replace(dest)
        else:
            dest.write_bytes(document or b"")
        head = dest.open("rb").read(16)
        from .raster.to_pdf import sniff
        sniffed = sniff(head)
        if sniffed != "application/octet-stream":
            job.fmt = sniffed
        job.documents.append(dest)
        if last:
            self._finish(job)

    def _finish(self, job: Job) -> None:
        docs = [d for d in job.documents if d.exists() and d.stat().st_size > 0]
        if not docs:
            job.state, job.reasons = ABORTED, ["document-format-error"]
            job.completed = int(time.time())
            return
        job.state = COMPLETED
        job.reasons = ["job-completed-successfully"]
        job.completed = int(time.time())
        job.impressions = 1
        for d in docs:
            try:
                self.on_document(Document(job=job, path=d, fmt=job.fmt))
            except Exception:  # noqa: BLE001
                log.exception("on_document failed for job %s", job.id)


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, tuple):
        return str(v[1])
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def _size(document: bytes | Path) -> int:
    if isinstance(document, Path):
        return document.stat().st_size if document.exists() else 0
    return len(document)
