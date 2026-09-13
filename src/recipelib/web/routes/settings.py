from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
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
    from ... import backup as B
    return templates.TemplateResponse(request, "pages/settings.html", {
        **_update_ctx(), "backups": B.list_backups(), "bk": B.STATE, "shrink": SHRINK,
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


def _update_ctx() -> dict:
    from ... import updater
    st = updater.STATE
    done = updater.finished_update()
    steps = []
    cur = st.get("step")
    idx = [k for k, _ in updater.STEPS].index(cur) if cur in [k for k, _ in updater.STEPS] else -1
    for i, (key, label) in enumerate(updater.STEPS):
        state = "done" if (i < idx or st.get("result") == "ok" and cur == "done") else ("current" if i == idx else "pending")
        if st.get("result") == "failed" and i == idx:
            state = "failed"
        steps.append({"key": key, "label": label, "state": state})
    busy = bool(st.get("updating") or st.get("checking"))
    return {"ver": updater.current(), "upd": st, "steps": steps, "busy": busy, "just_updated": done}


def _status_response(request: Request):
    return templates.TemplateResponse(request, "partials/update_status.html", _update_ctx())


@router.get("/partials/update-status", name="update_status")
def update_status(request: Request):
    return _status_response(request)


@router.post("/settings/update/check", name="settings_update_check")
def update_check(request: Request):
    from ... import updater
    if request.headers.get("HX-Request") == "true":
        updater.check_async()
        return _status_response(request)
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
        import time
        time.sleep(0.2)
    if request.headers.get("HX-Request") == "true":
        return _status_response(request)
    return RedirectResponse(str(request.url_for("settings")) + "?m=Updating… the server restarts itself when done", status_code=303)


# ---- backups ---------------------------------------------------------------

@router.post("/settings/backup", name="settings_backup")
def backup_create(request: Request):
    from ... import backup as B
    p = B.create_backup()
    return RedirectResponse(str(request.url_for("settings")) + f"?m=Backup written: {p.name}#backups", status_code=303)


@router.get("/settings/backups/{name}", name="settings_backup_download")
def backup_download(name: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from ... import backup as B
    p = B.backups_dir() / name
    if "/" in name or not name.endswith(".zip") or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/zip", filename=name)


@router.post("/settings/backups/{name}/delete", name="settings_backup_delete")
def backup_delete(name: str, request: Request):
    from ... import backup as B
    p = B.backups_dir() / name
    if "/" not in name and name.endswith(".zip") and p.is_file():
        p.unlink()
    return RedirectResponse(str(request.url_for("settings")) + "?m=Backup deleted#backups", status_code=303)


@router.post("/settings/backups/{name}/restore", name="settings_backup_restore")
def backup_restore(name: str, request: Request, mode: str = Form("merge"), overwrite: str = Form("0")):
    from fastapi import HTTPException
    from ... import backup as B
    p = B.backups_dir() / name
    if "/" in name or not p.is_file():
        raise HTTPException(404)
    try:
        stats = B.restore(p, mode="replace" if mode == "replace" else "merge", overwrite=overwrite == "1")
        msg = ("Replaced the library from " if mode == "replace" else "Merged ") + name + ": " + stats.summary()
    except Exception as e:  # noqa: BLE001
        msg = f"Restore failed: {type(e).__name__}: {e}"
    return RedirectResponse(str(request.url_for("settings")) + "?m=" + msg + "#backups", status_code=303)


@router.post("/settings/backups/upload", name="settings_backup_upload")
async def backup_upload(request: Request, file: UploadFile = File(...)):
    """Save an uploaded backup zip into the backups folder (restore it from the list)."""
    import re
    from ... import backup as B
    name = re.sub(r"[^\w.-]", "_", file.filename or "upload.zip")
    if not name.endswith(".zip"):
        name += ".zip"
    dest = B.backups_dir() / name
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    return RedirectResponse(str(request.url_for("settings")) + f"?m=Uploaded {name}; choose Merge or Replace below#backups", status_code=303)


SHRINK = {"running": False, "result": None}


@router.post("/settings/shrink", name="settings_shrink")
def shrink_files(request: Request):
    """Recompress stored PDFs and pictures in the background."""
    import threading

    from ...capture.shrink import shrink_library
    if not SHRINK["running"]:
        SHRINK.update(running=True, result=None)

        def run():
            try:
                t = shrink_library()
                SHRINK["result"] = f"{t['files']} files looked at, {t['changed']} shrunk, {(t['before'] - t['after']) / 1048576:.1f} MB saved"
            except Exception as e:  # noqa: BLE001
                SHRINK["result"] = f"failed: {type(e).__name__}: {e}"
            finally:
                SHRINK["running"] = False
        threading.Thread(target=run, name="shrink", daemon=True).start()
    return RedirectResponse(str(request.url_for("settings")) + "?m=Shrinking stored files in the background; the result shows under Maintenance when done", status_code=303)
