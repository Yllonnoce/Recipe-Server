"""Jinja environment shared by all routers."""
from __future__ import annotations

from importlib import resources

from fastapi.templating import Jinja2Templates

from ..extract.normalize import format_quantity

_dir = str(resources.files("recipelib.web") / "templates")
templates = Jinja2Templates(directory=_dir)
templates.env.filters["qty"] = format_quantity


def minutes_human(m: int | None) -> str:
    if not m:
        return ""
    h, r = divmod(int(m), 60)
    if h and r:
        return f"{h} h {r} min"
    if h:
        return f"{h} h"
    return f"{r} min"


templates.env.filters["mins"] = minutes_human
templates.env.globals["status_label"] = {
    "processing": "Processing", "needs_review": "Needs review", "ready": "Ready", "failed": "Failed",
}

from .. import updater  # noqa: E402
templates.env.globals["update_state"] = updater.STATE


def _static_version() -> str:
    from .. import __version__
    try:
        from .. import updater
        c = updater.current().commit
    except Exception:  # noqa: BLE001
        c = None
    return f"{__version__}-{c}" if c else __version__


templates.env.globals["static_v"] = _static_version()
