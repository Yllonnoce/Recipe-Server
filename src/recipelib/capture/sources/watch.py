"""Inbox folder poller. Plain polling (not inotify) so it behaves the same on
SMB shares and on all three OSes. A file is picked up only once its size has
been stable across two polls, so half-copied files are never ingested."""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from ...config import get_settings
from .. import pdf as pdfops
from .upload import IMAGE_EXT

log = logging.getLogger(__name__)


class InboxWatcher:
    def __init__(self, queue, interval: float = 5.0):
        self.queue = queue
        self.interval = interval
        self._stop = threading.Event()
        self._seen: dict[Path, int] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="inbox-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def scan_once(self) -> int:
        cfg = get_settings()
        inbox = cfg.inbox_dir
        picked = 0
        try:
            entries = [p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")]
        except OSError:
            return 0
        current: dict[Path, int] = {}
        for p in entries:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            current[p] = size
            if self._seen.get(p) == size and size > 0:
                if self._ingest(p, cfg):
                    picked += 1
        self._seen = current
        return picked

    def _ingest(self, p: Path, cfg) -> bool:
        ext = p.suffix.lower()
        processed = cfg.inbox_dir / "processed"
        failed = cfg.inbox_dir / "failed"
        try:
            if ext == ".pdf" or pdfops.is_pdf(p):
                dest = processed / _unique(processed, p.name)
                shutil.move(str(p), dest)
                self.queue.enqueue("watch", pdf_path=str(dest), title_hint=_hint(p))
            elif ext in IMAGE_EXT:
                data = p.read_bytes()
                dest = processed / _unique(processed, p.stem + ".pdf")
                dest.write_bytes(pdfops.images_to_pdf([data]))
                (processed / _unique(processed, p.name)).write_bytes(data)
                p.unlink()
                self.queue.enqueue("watch", pdf_path=str(dest), title_hint=_hint(p))
            else:
                log.warning("inbox: skipping unsupported file %s", p.name)
                shutil.move(str(p), failed / _unique(failed, p.name))
                return False
            log.info("inbox: picked up %s", p.name)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("inbox: failed on %s: %s", p.name, e)
            try:
                shutil.move(str(p), failed / _unique(failed, p.name))
            except OSError:
                pass
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001
                log.exception("inbox scan failed")
            self._stop.wait(self.interval)


def _hint(p: Path) -> str:
    return p.stem.replace("_", " ").replace("-", " ").strip()[:120]


def _unique(folder: Path, name: str) -> str:
    cand = folder / name
    if not cand.exists():
        return name
    stem, suf = Path(name).stem, Path(name).suffix
    i = 2
    while (folder / f"{stem} ({i}){suf}").exists():
        i += 1
    return f"{stem} ({i}){suf}"
