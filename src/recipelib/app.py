"""FastAPI application factory. The lifespan starts the capture workers, the
inbox watcher and (when enabled) the virtual printer, and stops them on exit."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import resources
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response


class _Static(StaticFiles):
    """Own scripts/styles change with every update: make browsers revalidate
    them (cheap 304s). Vendored libraries under reader/ and vendor/ are immutable."""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        path = str(args[0]) if args else ""
        if "/reader/" in path or "/vendor/" in path or "/icons/" in path:
            resp.headers["Cache-Control"] = "public, max-age=604800"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

from . import __version__
from .capture.queue import JobQueue, set_queue
from .capture.sources.watch import InboxWatcher
from .config import get_settings
from .db.engine import init_engine
from .db.migrate import migrate

log = logging.getLogger(__name__)


def setup_logging(cfg) -> None:
    cfg.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(cfg.logs_dir / "recipelib.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    import sys
    has_console = sys.stderr is not None      # pythonw.exe / a hidden service has no console
    if has_console and not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    setup_logging(cfg)
    migrate(cfg.db_path)
    init_engine(cfg.db_path)
    _startup_housekeeping()
    queue = JobQueue(workers=cfg.workers)
    set_queue(queue)
    queue.start()
    watcher = InboxWatcher(queue, interval=cfg.watch_interval)
    watcher.start()
    printer = None
    if cfg.printer_enabled:
        try:
            from .ipp.server import PrinterService
            printer = PrinterService(cfg, queue)
            printer.start()
        except Exception:  # noqa: BLE001
            log.exception("virtual printer failed to start; continuing without it")
            printer = None
    from .updater import Checker
    checker = Checker()
    checker.start()
    from .backup import AutoBackup
    auto_backup = AutoBackup(cfg.auto_backup_days, cfg.backup_keep)
    auto_backup.start()
    from .mirror import Mirror, Pusher
    mirror = Mirror(cfg.mirror_of, cfg.mirror_interval_hours)
    mirror.start()
    pusher = Pusher(cfg.push_to, cfg.push_interval_hours)
    pusher.start()
    if cfg.llm_enabled and cfg.llm_keep_loaded:
        import threading

        from .extract.llm import warm_up

        def _warm():
            import time
            for _ in range(20):                       # Ollama may still be starting at boot
                if warm_up(keep=True):
                    return
                time.sleep(30)
        threading.Thread(target=_warm, name="llm-warmup", daemon=True).start()
    app.state.queue = queue
    app.state.printer = printer
    log.info("Recipe Library %s ready on http://%s:%s", __version__, cfg.host, cfg.port)
    try:
        yield
    finally:
        checker.stop()
        auto_backup.stop()
        mirror.stop()
        pusher.stop()
        if printer is not None:
            printer.stop()
        watcher.stop()
        queue.stop()


def _startup_housekeeping() -> None:
    """Idempotent fix-ups run on every start: seed the fixed categories and
    categorise recipes that predate them."""
    from .db.engine import session_scope
    from .domain import recipes as R
    try:
        with session_scope() as s:
            made = R.seed_categories(s)
            n = R.categorize_missing(s)
        if made or n:
            log.info("startup: %d categories seeded, %d recipes categorised", made, n)
    except Exception:  # noqa: BLE001
        log.exception("startup housekeeping failed (continuing)")


def create_app() -> FastAPI:
    app = FastAPI(title="Recipe Library", version=__version__, lifespan=lifespan, docs_url="/api/docs")
    static_dir = str(resources.files("recipelib.web") / "static")
    app.mount("/static", _Static(directory=static_dir), name="static")

    from .web.routes import assets, capture, cook, library, mealplan, reader, recipe, settings, shopping
    for mod in (library, recipe, reader, cook, capture, assets, settings, shopping, mealplan):
        app.include_router(mod.router)

    @app.middleware("http")
    async def hold_changes_during_restore(request, call_next):
        """A restore replaces or merges into the library in one long transaction; a change
        made meanwhile would fail or be wiped. Reading keeps working."""
        from . import tasks
        if request.method not in ("GET", "HEAD", "OPTIONS") and tasks.restoring():
            import html

            from fastapi.responses import HTMLResponse
            cur = tasks.current() or {}
            label = html.escape((cur.get("label") or "restore").lower())   # holds file names and addresses
            msg = (f"The library is being restored ({label}). "
                   "Nothing was changed; try again when it has finished, usually within a minute.")
            if request.headers.get("HX-Request") == "true":
                return HTMLResponse(f'<p class="flash bad">{msg}</p>', status_code=503, headers={"HX-Reswap": "none"})
            return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                                f"<title>Restoring</title><p style='font:16px system-ui;max-width:40em;margin:3em auto;padding:0 16px'>"
                                f"{msg}<br><br><a href='javascript:history.back()'>Go back</a></p>",
                                status_code=503, headers={"Retry-After": "30"})
        return await call_next(request)
    return app


app = create_app()
