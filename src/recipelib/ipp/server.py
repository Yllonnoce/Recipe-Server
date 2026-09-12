"""HTTP/IPP server on its own port, run in a daemon thread next to the web app.

Uses the stdlib ThreadingHTTPServer: IPP is just POST with a binary body, and
the stdlib lets us handle `Expect: 100-continue` and chunked uploads (iOS
sends those) precisely. Bodies stream to a spooled temp file so a 60 MB
raster job never sits in memory twice.
"""
from __future__ import annotations

import io
import logging
import shutil
import tempfile
import threading
import time
import uuid as uuidlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import attrs as A
from . import codec as C
from .mdns import Advertiser, local_ipv4s, txt_record
from .ops import Document, Printer

log = logging.getLogger(__name__)
SPOOL_MEM = 4 * 1024 * 1024


def _icon_png(size: int) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=size, height=size)
    r = pymupdf.Rect(0, 0, size, size)
    shape = page.new_shape()
    shape.draw_rect(r)
    shape.finish(fill=(0.78, 0.33, 0.16), color=None)
    shape.draw_circle((size / 2, size * 0.56), size * 0.30)
    shape.finish(fill=(1, 1, 1), color=None)
    shape.draw_rect(pymupdf.Rect(size * 0.30, size * 0.22, size * 0.70, size * 0.34))
    shape.finish(fill=(1, 1, 1), color=None)
    shape.commit()
    png = page.get_pixmap(alpha=False).tobytes("png")
    doc.close()
    return png


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RecipeLibrary/0.1"
    printer: Printer = None      # type: ignore[assignment]
    icons: dict[int, bytes] = {}
    dump_dir: Path | None = None
    web_url: str = ""

    def log_message(self, fmt, *args):  # quiet; we log ourselves
        log.debug("ipp http: " + fmt, *args)

    # ---- GET: icons and a pointer to the web UI ----------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/icon.png", "/icon-128.png", "/icon-512.png"):
            size = 512 if "512" in path else 128
            data = self.icons[size]
            self._send(200, "image/png", data)
            return
        body = (f"<html><body style='font-family:sans-serif'><h2>{self.printer.info.name}</h2>"
                f"<p>This is the virtual printer's IPP endpoint. The library lives at "
                f"<a href='{self.web_url}'>{self.web_url}</a>.</p></body></html>").encode()
        self._send(200, "text/html; charset=utf-8", body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- POST: IPP ---------------------------------------------------------
    def do_POST(self):
        started = time.time()
        body = self._read_body()
        try:
            head = body.read(256 * 1024)
            body.seek(0)
            req, doc_offset = C.decode(head)
        except C.IPPError as e:
            log.warning("bad IPP request from %s: %s", self.client_address[0], e)
            self._send(400, "text/plain", f"bad IPP request: {e}".encode())
            return
        document = None
        if req.code in (0x0002, 0x0006):          # Print-Job, Send-Document carry a document
            body.seek(doc_offset)
            tmp = Path(tempfile.mkstemp(prefix="ippdoc-", dir=str(self.printer.spool))[1])
            with tmp.open("wb") as fh:
                shutil.copyfileobj(body, fh)
            document = tmp
        if self.dump_dir:
            self._dump(req, head, doc_offset)
        resp = self.printer.handle(req, document)
        if document is not None and document.exists():
            try:
                document.unlink()
            except OSError:
                pass
        data = C.encode(resp)
        self._send(200, "application/ipp", data)
        log.info("ipp %s %s from %s -> %#06x (%.0f ms)", _opname(req.code), req.value("job-name") or "",
                 self.client_address[0], resp.code, (time.time() - started) * 1000)

    def _read_body(self) -> io.IOBase:
        spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEM)
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            while True:
                line = self.rfile.readline(65537)
                if not line:
                    break
                size_s = line.split(b";")[0].strip()
                try:
                    size = int(size_s, 16)
                except ValueError:
                    break
                if size == 0:
                    # trailers until blank line
                    while True:
                        t = self.rfile.readline(65537)
                        if not t or t in (b"\r\n", b"\n"):
                            break
                    break
                remaining = size
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 16))
                    if not chunk:
                        break
                    spool.write(chunk)
                    remaining -= len(chunk)
                self.rfile.readline(3)   # CRLF after the chunk
        else:
            length = int(self.headers.get("Content-Length") or 0)
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 16))
                if not chunk:
                    break
                spool.write(chunk)
                remaining -= len(chunk)
        spool.seek(0)
        return spool

    def _send(self, status: int, ctype: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _dump(self, req: C.Message, head: bytes, doc_offset: int) -> None:
        try:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            base = self.dump_dir / f"{stamp}-{_opname(req.code)}-{self.client_address[0]}"
            base.with_suffix(".ipp").write_bytes(head[:doc_offset])
            base.with_suffix(".txt").write_text(C.describe(req) + "\n\nheaders:\n" + str(self.headers), encoding="utf-8")
        except Exception:  # noqa: BLE001
            log.exception("ipp dump failed")


def _opname(code: int) -> str:
    return {0x0002: "Print-Job", 0x0004: "Validate-Job", 0x0005: "Create-Job", 0x0006: "Send-Document",
            0x0008: "Cancel-Job", 0x0009: "Get-Job-Attributes", 0x000A: "Get-Jobs",
            0x000B: "Get-Printer-Attributes", 0x0015: "Get-Printer-Supported-Values", 0x0039: "Cancel-My-Jobs",
            0x003B: "Close-Job", 0x003C: "Identify-Printer"}.get(code, f"op-{code:#06x}")


class PrinterService:
    """Owns the HTTP server thread, the mDNS advertiser and the printer object."""

    def __init__(self, cfg, queue):
        self.cfg = cfg
        self.queue = queue
        self.port = cfg.ipp_port
        self.uuid = self._stable_uuid(cfg)
        ips = local_ipv4s() or ["127.0.0.1"]
        host = ips[0]
        self.uris = [f"ipp://{host}:{self.port}/ipp/print"]
        for ip in ips[1:]:
            self.uris.append(f"ipp://{ip}:{self.port}/ipp/print")
        self.web_url = f"http://{host}:{cfg.port}/"
        icon_urls = [f"http://{host}:{self.port}/icon-128.png", f"http://{host}:{self.port}/icon-512.png"]
        self.info = A.PrinterInfo(cfg.printer_name, cfg.printer_location, self.uuid, self.uris, self.web_url,
                                  icon_urls, paper=cfg.paper)
        self.printer = Printer(self.info, cfg.inbox_dir / "print", self._on_document,
                               first_job_id=self._first_job_id())
        self.httpd: ThreadingHTTPServer | None = None
        self.adv: Advertiser | None = None
        self._thread: threading.Thread | None = None
        self.last_job: dict | None = None

    @staticmethod
    def _stable_uuid(cfg) -> str:
        p = cfg.library_dir / "printer-uuid.txt"
        try:
            v = p.read_text().strip()
            uuidlib.UUID(v)
            return v
        except Exception:  # noqa: BLE001
            v = str(uuidlib.uuid4())
            try:
                p.write_text(v)
            except OSError:
                pass
            return v

    @staticmethod
    def _first_job_id() -> int:
        try:
            from sqlalchemy import func, select
            from ..db.engine import session_scope
            from ..db.models import CaptureJob
            with session_scope() as s:
                m = s.scalar(select(func.max(CaptureJob.ipp_job_id)))
                return int(m or 0) + 1
        except Exception:  # noqa: BLE001
            return 1

    def _on_document(self, doc: Document) -> None:
        from ..capture.sources.printer import ingest
        self.last_job = {"id": doc.job.id, "name": doc.job.name, "user": doc.job.user, "fmt": doc.fmt,
                         "time": time.time()}
        ingest(self.queue, doc)

    def start(self) -> None:
        handler = type("RecipeIPPHandler", (_Handler,), {})
        handler.printer = self.printer
        handler.icons = {128: _icon_png(128), 512: _icon_png(512)}
        handler.dump_dir = self.cfg.ipp_dump_dir
        handler.web_url = self.web_url
        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="ipp-server", daemon=True)
        self._thread.start()
        log.info("virtual printer '%s' listening on %s", self.info.name, ", ".join(self.uris))
        txt = txt_record(self.info.name, self.port, self.uuid, A.URF, A.FORMATS, self.web_url, self.info.location)
        self.adv = Advertiser(self.info.name, self.port, txt, web_port=self.cfg.port)
        self.adv.start()

    def stop(self) -> None:
        if self.adv:
            self.adv.stop()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
