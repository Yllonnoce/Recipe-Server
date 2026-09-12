"""PDF reader page, the PDF bytes (range-capable), reading progress and bookmarks."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.engine import get_db
from ...db.models import Bookmark, utcnow
from ...domain import recipes as R
from ..templating import templates

router = APIRouter(prefix="/recipes")
_REL = re.compile(r"^pdf/[0-9a-f]{2}/[0-9a-f]{64}\.pdf$")


def _get(s: Session, recipe_id: int):
    r = R.get(s, recipe_id)
    if r is None:
        raise HTTPException(404, "recipe not found")
    return r


def _pdf_path(r):
    if r.pdf_asset is None or not _REL.match(r.pdf_asset.rel_path):
        raise HTTPException(404, "no PDF for this recipe")
    p = get_settings().assets_dir / r.pdf_asset.rel_path
    if not p.is_file():
        raise HTTPException(404, "PDF missing on disk")
    return p


@router.get("/{recipe_id}/read", name="recipe_read")
def read(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    _pdf_path(r)
    start = R.clamp_page(r, request.query_params.get("page")) or r.last_page or 1
    return templates.TemplateResponse(request, "pages/recipe_read.html", {
        "r": r, "start_page": start, "bookmarks": R.bookmark_views(r),
        "embed": request.query_params.get("embed") == "1",
    })


@router.get("/{recipe_id}/file", name="recipe_file")
def file(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    p = _pdf_path(r)
    safe = re.sub(r"[^\w\s.-]", "", r.title).strip()[:80] or "recipe"
    dl = request.query_params.get("dl") == "1"
    headers = {"Content-Disposition": f'{"attachment" if dl else "inline"}; filename="{safe}.pdf"'}
    # Starlette's FileResponse handles Range / If-Range / ETag / Last-Modified
    return FileResponse(p, media_type="application/pdf", headers=headers)


@router.post("/{recipe_id}/progress", name="recipe_progress")
async def progress(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    data = await _json(request)
    try:
        count = int(data.get("page_count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        r.page_count = count
    page = R.clamp_page(r, data.get("page"))
    if page:
        r.last_page = page
    s.commit()
    return {"ok": True, "last_page": r.last_page, "page_count": r.page_count}


@router.get("/{recipe_id}/bookmarks", name="recipe_bookmarks")
def bookmarks(recipe_id: int, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    return {"ok": True, "bookmarks": R.bookmark_views(r)}


@router.post("/{recipe_id}/bookmarks", name="recipe_bookmark_add")
async def bookmark_add(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    data = await _json(request)
    page = R.clamp_page(r, data.get("page"))
    if not page:
        return JSONResponse({"ok": False, "error": "page must be a number from 1"}, status_code=400)
    bm, created = R.upsert_bookmark(s, r, page, data.get("label"))
    r.updated_at = utcnow()
    s.commit()
    return {"ok": True, "created": created, "bookmark": bm.view(), "bookmarks": R.bookmark_views(r)}


@router.post("/{recipe_id}/bookmarks/{bookmark_id}/rename", name="recipe_bookmark_rename")
async def bookmark_rename(recipe_id: int, bookmark_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    bm = s.get(Bookmark, bookmark_id)
    if bm is None or bm.recipe_id != r.id:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    data = await _json(request)
    bm.label = R.clean_label(data.get("label"), bm.label)
    s.commit()
    return {"ok": True, "bookmark": bm.view(), "bookmarks": R.bookmark_views(r)}


@router.post("/{recipe_id}/bookmarks/{bookmark_id}/delete", name="recipe_bookmark_delete")
def bookmark_delete(recipe_id: int, bookmark_id: int, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    bm = s.get(Bookmark, bookmark_id)
    if bm is None or bm.recipe_id != r.id:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    s.delete(bm)
    s.flush()
    s.refresh(r)
    s.commit()
    return {"ok": True, "bookmarks": R.bookmark_views(r)}


async def _json(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        form = await request.form()
        return {k: form.get(k) for k in form.keys()}
