from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ... import __version__
from ...config import config_path, get_settings
from ...db import fts
from ...db.engine import get_db
from ..templating import templates

router = APIRouter()


@router.get("/settings", name="settings")
def settings(request: Request, s: Session = Depends(get_db)):
    from ...doctor import run_checks
    return templates.TemplateResponse(request, "pages/settings.html", {
        "cfg": get_settings(), "config_path": config_path(), "version": __version__,
        "checks": run_checks(quick=True), "message": request.query_params.get("m"),
        "printer": getattr(request.app.state, "printer", None), "host": request.url.hostname,
    })


@router.post("/settings/reindex", name="settings_reindex")
def reindex(request: Request, s: Session = Depends(get_db)):
    n = fts.rebuild_all(s)
    s.commit()
    return RedirectResponse(str(request.url_for("settings")) + f"?m=Search index rebuilt for {n} recipes", status_code=303)


@router.post("/settings/categorize", name="settings_categorize")
def categorize(request: Request, s: Session = Depends(get_db)):
    """Auto-assign categories to every recipe (keeps ones picked by hand)."""
    from ...domain import recipes as R
    n = R.categorize_all(s)
    s.commit()
    return RedirectResponse(str(request.url_for("settings")) + f"?m=Categories assigned to {n} recipes", status_code=303)
