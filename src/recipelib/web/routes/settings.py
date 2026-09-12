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
    from ... import updater
    return templates.TemplateResponse(request, "pages/settings.html", {
        "ver": updater.current(), "upd": updater.STATE,
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


@router.post("/settings/update/check", name="settings_update_check")
def update_check(request: Request):
    from ... import updater
    r = updater.check()
    if r["error"]:
        msg = "Update check: " + r["error"]
    elif r["behind"]:
        msg = f"A newer version is available ({r['behind']} change{'s' if r['behind'] != 1 else ''} behind)"
    else:
        msg = "You are on the latest version"
    return RedirectResponse(str(request.url_for("settings")) + "?m=" + msg, status_code=303)


@router.post("/settings/update/run", name="settings_update_run")
def update_run(request: Request):
    import threading

    from ... import updater
    if updater.repo_root() is None:
        return RedirectResponse(str(request.url_for("settings")) + "?m=Not installed from git; re-run the installer with a fresh download", status_code=303)
    if not updater.STATE.get("updating"):
        threading.Thread(target=updater.update, kwargs={"restart": True}, name="updater", daemon=True).start()
    return RedirectResponse(str(request.url_for("settings")) + "?m=Updating… the server restarts itself when done; reload this page in a minute", status_code=303)
