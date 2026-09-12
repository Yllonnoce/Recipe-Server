"""FastAPI application factory. The lifespan starts the capture workers, the
inbox watcher and (when enabled) the virtual printer, and stops them on exit."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib import resources
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
    app.state.queue = queue
    app.state.printer = printer
    log.info("Recipe Library %s ready on http://%s:%s", __version__, cfg.host, cfg.port)
    try:
        yield
    finally:
        checker.stop()
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
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    from .web.routes import assets, capture, cook, library, mealplan, reader, recipe, settings, shopping
    for mod in (library, recipe, reader, cook, capture, assets, settings, shopping, mealplan):
        app.include_router(mod.router)
    return app


app = create_app()
