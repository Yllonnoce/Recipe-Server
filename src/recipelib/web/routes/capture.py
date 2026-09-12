from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...capture.queue import get_queue
from ...capture.sources.upload import stage_images_as_one, stage_upload
from ...config import get_settings
from ...db.engine import get_db
from ...db.models import CaptureJob, Recipe
from ..templating import templates

router = APIRouter()


def _jobs(s: Session, limit: int = 60) -> list[dict]:
    rows = list(s.scalars(select(CaptureJob).order_by(CaptureJob.id.desc()).limit(limit)))
    out = []
    for j in rows:
        r = s.get(Recipe, j.recipe_id) if j.recipe_id else None
        out.append({"j": j, "r": r if (r and not r.deleted_at) else None})
    return out


@router.get("/capture", name="capture")
def capture(request: Request, s: Session = Depends(get_db)):
    cfg = get_settings()
    printer = getattr(request.app.state, "printer", None)
    return templates.TemplateResponse(request, "pages/capture.html", {
        "jobs": _jobs(s), "cfg": cfg, "printer": printer,
        "host": request.url.hostname, "message": request.query_params.get("m"),
    })


@router.get("/partials/jobs", name="jobs_partial")
def jobs_partial(request: Request, s: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "partials/jobs.html", {"jobs": _jobs(s)})


@router.post("/capture/upload", name="capture_upload")
async def upload(request: Request, files: list[UploadFile], combine: str = Form("0")):
    q = get_queue()
    n = 0
    errors: list[str] = []
    blobs: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        blobs.append((f.filename or "upload", data))
    images = [(n_, d) for n_, d in blobs if d[:5] != b"%PDF-"]
    if combine == "1" and len(images) > 1:
        path, hint = stage_images_as_one([n_ for n_, _ in images], [d for _, d in images])
        q.enqueue("upload", pdf_path=str(path), title_hint=hint)
        n += 1
        blobs = [(n_, d) for n_, d in blobs if d[:5] == b"%PDF-"]
    for name, data in blobs:
        try:
            path, hint = stage_upload(name, data)
            q.enqueue("upload", pdf_path=str(path), title_hint=hint)
            n += 1
        except ValueError as e:
            errors.append(f"{name}: {e}")
    msg = f"{n} file(s) queued" + (("; " + "; ".join(errors)) if errors else "")
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "partials/upload_result.html", {"message": msg})
    return RedirectResponse(str(request.url_for("capture")) + f"?m={msg}", status_code=303)


@router.post("/capture/url", name="capture_url")
def capture_url(request: Request, url: str = Form(""), force: str = Form("0")):
    url = url.strip()
    if not url:
        return RedirectResponse(str(request.url_for("capture")) + "?m=Enter a URL", status_code=303)
    if "://" not in url:
        url = "https://" + url
    get_queue().enqueue("url", url=url, force=force == "1", priority=2)
    return RedirectResponse(str(request.url_for("capture")) + "?m=URL queued", status_code=303)


@router.post("/capture/jobs/{job_id}/retry", name="job_retry")
def job_retry(job_id: int, request: Request, s: Session = Depends(get_db)):
    get_queue().retry(job_id)
    return templates.TemplateResponse(request, "partials/jobs.html", {"jobs": _jobs(s)})


@router.post("/capture/jobs/{job_id}/cancel", name="job_cancel")
def job_cancel(job_id: int, request: Request, s: Session = Depends(get_db)):
    get_queue().cancel(job_id)
    return templates.TemplateResponse(request, "partials/jobs.html", {"jobs": _jobs(s)})
