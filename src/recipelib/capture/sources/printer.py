"""Glue between a finished IPP job and the ingest pipeline."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ...config import get_settings
from ...ipp.ops import Document

log = logging.getLogger(__name__)
_EXT = {"application/pdf": ".pdf", "image/pwg-raster": ".pwg", "image/urf": ".urf",
        "image/jpeg": ".jpg", "image/png": ".png"}


def clean_job_name(name: str) -> str:
    """Browser print jobs are usually titled with the page title; tidy it."""
    n = (name or "").strip()
    n = re.sub(r"\.(pdf|html?|txt)$", "", n, flags=re.I)
    n = re.sub(r"\s*[-|–—]\s*(Print(able)?|Recipe|Recipes)\s*$", "", n, flags=re.I)
    n = re.sub(r"^Print(able)?\s*[-:]\s*", "", n, flags=re.I)
    return n[:150] or "Printed recipe"


def ingest(queue, doc: Document) -> int:
    cfg = get_settings()
    spool = cfg.inbox_dir / "print"
    spool.mkdir(parents=True, exist_ok=True)
    ext = _EXT.get(doc.fmt, ".bin")
    dest = spool / f"job{doc.job.id:06d}{ext}"
    Path(doc.path).replace(dest)
    if getattr(doc.job, "reverse", False):
        # 'reverse-order' means the device wanted the printer to *output* pages
        # last-first; the document itself is in reading order, so leave it alone.
        log.info("printer: job %s asked for reverse-order output (ignored; document kept as sent)", doc.job.id)
    log.info("printer: job %s '%s' (%s, %d bytes) queued", doc.job.id, doc.job.name, doc.fmt, dest.stat().st_size)
    return queue.enqueue("printer", pdf_path=str(dest), title_hint=clean_job_name(doc.job.name),
                         ipp_job_id=doc.job.id, priority=3)
