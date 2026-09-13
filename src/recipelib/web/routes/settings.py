from __future__ import annotations

import re
import time

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


# ---- Ollama (local or on another machine) ---------------------------------

def _ollama_ctx(test: dict | None = None, scan: list | None = None) -> dict:
    from ... import llmhost
    cfg = get_settings()
    return {"cfg": cfg, "test": test, "scan": scan, "pull": llmhost.PULL,
            "has_model": llmhost.has_model(test["models"], cfg.ollama_model) if test and test.get("ok") else None}


@router.get("/partials/ollama", name="ollama_partial")
def ollama_partial(request: Request):
    from ... import llmhost
    cfg = get_settings()
    return templates.TemplateResponse(request, "partials/ollama.html", _ollama_ctx(test=llmhost.test_host(cfg.ollama_host, timeout=2.5)))


@router.post("/settings/ollama", name="settings_ollama_save")
def ollama_save(request: Request, host: str = Form(""), model: str = Form("")):
    from ... import llmhost
    llmhost.save(host, model or get_settings().ollama_model)
    cfg = get_settings()
    t = llmhost.test_host(cfg.ollama_host)
    if t["ok"] and cfg.llm_enabled and cfg.llm_keep_loaded and llmhost.has_model(t["models"], cfg.ollama_model):
        import threading

        from ...extract.llm import warm_up
        threading.Thread(target=warm_up, kwargs={"keep": True}, daemon=True).start()
    return templates.TemplateResponse(request, "partials/ollama.html", _ollama_ctx(test=t))


@router.post("/settings/ollama/test", name="settings_ollama_test")
def ollama_test(request: Request, host: str = Form("")):
    from ... import llmhost
    return templates.TemplateResponse(request, "partials/ollama.html", _ollama_ctx(test=llmhost.test_host(host or get_settings().ollama_host)))


@router.post("/settings/ollama/pull", name="settings_ollama_pull")
def ollama_pull(request: Request, model: str = Form("")):
    from ... import llmhost
    cfg = get_settings()
    llmhost.pull_async(model or cfg.ollama_model, cfg.ollama_host)
    return templates.TemplateResponse(request, "partials/ollama.html", _ollama_ctx(test=llmhost.test_host(cfg.ollama_host, timeout=2.5)))


@router.post("/settings/ollama/scan", name="settings_ollama_scan")
def ollama_scan(request: Request):
    from ... import llmhost
    cfg = get_settings()
    return templates.TemplateResponse(request, "partials/ollama.html",
                                      _ollama_ctx(test=llmhost.test_host(cfg.ollama_host, timeout=2.5), scan=llmhost.scan_lan()))


# ---- network scan -----------------------------------------------------------

@router.post("/settings/scan", name="settings_scan")
def network_scan(request: Request, port: str = Form("")):
    from ... import netscan
    ports = None
    if port.strip():
        try:
            ports = [int(x) for x in re.split(r"[,\s]+", port.strip()) if x]
        except ValueError:
            ports = None
    found = netscan.scan(ports=ports)
    return templates.TemplateResponse(request, "partials/netscan.html", {"found": found, "port": port})


# ---- backup server (mirror) -------------------------------------------------

@router.get("/api/backup/info", name="api_backup_info")
def api_backup_info(s: Session = Depends(get_db)):
    """What a backup server would get: counts and version."""
    from sqlalchemy import func, select

    from ... import __version__
    from ...db.models import Recipe
    n = s.scalar(select(func.count()).select_from(Recipe).where(Recipe.deleted_at.is_(None))) or 0
    return {"app": "recipelib", "version": __version__, "recipes": n}


@router.get("/api/backup/latest", name="api_backup_latest")
def api_backup_latest(s: Session = Depends(get_db)):
    """A fresh backup zip. A recent one (under 30 minutes old) is reused only
    when nothing in the library changed after it was written."""
    import time
    from datetime import datetime, timezone

    from fastapi.responses import FileResponse
    from sqlalchemy import func, select

    from ... import backup as B
    from ...db.models import Recipe
    zips = sorted(B.backups_dir().glob("recipelib-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest_change = s.scalar(select(func.max(Recipe.updated_at))) or ""
    p = None
    if zips and time.time() - zips[0].stat().st_mtime < 1800:
        made = datetime.fromtimestamp(zips[0].stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
        if not latest_change or latest_change <= made:
            p = zips[0]
    if p is None:
        p = B.create_backup()
    return FileResponse(p, media_type="application/zip", filename=p.name)


def _mirror_ctx() -> dict:
    from ... import mirror
    return {"cfg": get_settings(), "mirror": mirror.STATE}


@router.get("/partials/mirror", name="mirror_partial")
def mirror_partial(request: Request):
    return templates.TemplateResponse(request, "partials/mirror.html", _mirror_ctx())


@router.post("/settings/mirror", name="settings_mirror_save")
def mirror_save(request: Request, mirror_of: str = Form(""), mirror_interval_hours: str = Form("24"), mirror_mode: str = Form("merge")):
    from ... import mirror
    from ...cli import config_set
    url = mirror.normalize_url(mirror_of)
    try:
        hours = float(mirror_interval_hours or 24)
    except ValueError:
        hours = 24.0
    config_set("mirror_of", url)
    config_set("mirror_interval_hours", str(hours))
    config_set("mirror_mode", "replace" if mirror_mode == "replace" else "merge")
    get_settings(reload=True)
    return templates.TemplateResponse(request, "partials/mirror.html", {**_mirror_ctx(), "saved": True})


@router.post("/settings/mirror/test", name="settings_mirror_test")
def mirror_test(request: Request, mirror_of: str = Form("")):
    from ... import mirror
    ctx = _mirror_ctx()
    try:
        info = mirror.primary_info(mirror_of or get_settings().mirror_of)
        ctx["test"] = f"✅ {mirror.normalize_url(mirror_of or get_settings().mirror_of)} is a Recipe Library v{info.get('version')} with {info.get('recipes')} recipes"
    except Exception as e:  # noqa: BLE001
        ctx["test"] = f"⚠️ not a reachable Recipe Library: {mirror.explain(e)}"
    return templates.TemplateResponse(request, "partials/mirror.html", ctx)


@router.post("/settings/mirror/sync", name="settings_mirror_sync")
def mirror_sync(request: Request):
    from ... import mirror
    mirror.sync_async()
    import time
    time.sleep(0.3)
    return templates.TemplateResponse(request, "partials/mirror.html", _mirror_ctx())


@router.post("/api/backup/receive", name="api_backup_receive")
async def api_backup_receive(request: Request, file: UploadFile = File(...), mode: str = Form("merge")):
    """A main server sends us a backup; merge it in (its version wins) or replace."""
    import re as _re

    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from ... import backup as B
    from ... import mirror
    cfg = get_settings()
    if cfg.sync_token and request.headers.get("X-Recipelib-Token", "") != cfg.sync_token:
        raise HTTPException(403, "sync_token mismatch")
    sender = request.client.host if request.client else "unknown"
    name = f"received-{_re.sub(r'[^0-9A-Za-z.]', '_', sender)}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    dest = B.backups_dir() / name
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    try:
        stats = await run_in_threadpool(B.restore, dest, "replace" if mode == "replace" else "merge", True)
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}, status_code=400)
    for old in sorted(B.backups_dir().glob("received-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[3:]:
        old.unlink(missing_ok=True)
    mirror.STATE.update(last_ok=True, last_run=time.time(), source=f"sent by {sender}",
                        result=f"{time.strftime('%b %d %H:%M')}: received a backup from {sender}: {stats.summary()}")
    return {"ok": True, "summary": stats.summary()}


def _push_ctx() -> dict:
    from ... import mirror
    return {"cfg": get_settings(), "push": mirror.PUSH}


@router.get("/partials/push", name="push_partial")
def push_partial(request: Request):
    return templates.TemplateResponse(request, "partials/push.html", _push_ctx())


@router.post("/settings/push", name="settings_push_save")
def push_save(request: Request, push_to: str = Form(""), push_interval_hours: str = Form("24"), push_mode: str = Form("merge"), sync_token: str = Form("")):
    from ... import mirror
    from ...cli import config_set
    try:
        hours = float(push_interval_hours or 24)
    except ValueError:
        hours = 24.0
    config_set("push_to", mirror.normalize_url(push_to))
    config_set("push_interval_hours", str(hours))
    config_set("push_mode", "replace" if push_mode == "replace" else "merge")
    config_set("sync_token", sync_token.strip())
    get_settings(reload=True)
    return templates.TemplateResponse(request, "partials/push.html", {**_push_ctx(), "saved": True})


@router.post("/settings/push/test", name="settings_push_test")
def push_test(request: Request, push_to: str = Form("")):
    from ... import mirror
    ctx = _push_ctx()
    try:
        info = mirror.primary_info(push_to or get_settings().push_to)
        ctx["test"] = f"✅ {mirror.resolve_url(push_to or get_settings().push_to)} is a Recipe Library v{info.get('version')} holding {info.get('recipes')} recipes; ready to receive"
    except Exception as e:  # noqa: BLE001
        ctx["test"] = f"⚠️ not a reachable Recipe Library: {mirror.explain(e)}"
    return templates.TemplateResponse(request, "partials/push.html", ctx)


@router.post("/settings/push/send", name="settings_push_send")
def push_send(request: Request):
    from ... import mirror
    mirror.push_async()
    time.sleep(0.3)
    return templates.TemplateResponse(request, "partials/push.html", _push_ctx())
