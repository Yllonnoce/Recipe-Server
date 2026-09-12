from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ...capture.queue import get_queue
from ...db.engine import get_db
from ...domain import recipes as R
from ..templating import templates

router = APIRouter(prefix="/recipes")


def _get(s: Session, recipe_id: int):
    r = R.get(s, recipe_id)
    if r is None:
        raise HTTPException(404, "recipe not found")
    return r


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/{recipe_id}", name="recipe_detail")
def detail(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    return templates.TemplateResponse(request, "pages/recipe.html", {
        "r": r, "bookmarks": R.bookmark_views(r),
    })


def _pdf_path(r):
    from ...config import get_settings
    if r.pdf_asset is None:
        return None
    p = get_settings().assets_dir / r.pdf_asset.rel_path
    return p if p.is_file() else None


@router.get("/{recipe_id}/edit", name="recipe_edit")
def edit(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    from ...capture import pdf as pdfops
    r = _get(s, recipe_id)
    pdf = _pdf_path(r)
    candidates = pdfops.candidate_images(pdf, max_pages=3)[:12] if pdf else []
    n_pages = min(r.page_count or 1, 3)
    return templates.TemplateResponse(request, "pages/edit.html", {
        "r": r, "ingredients_text": R.ingredients_as_text(r), "steps_text": R.steps_as_text(r),
        "candidates": candidates, "preview_pages": list(range(n_pages)),
        "course": ", ".join(t.name for t in r.tags_of("course")),
        "cuisine": ", ".join(t.name for t in r.tags_of("cuisine")),
        "custom": ", ".join(t.name for t in r.tags_of("custom")),
        "all_tags": R.tag_counts(s),
    })


@router.post("/{recipe_id}/edit", name="recipe_edit_save")
async def edit_save(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    form = await request.form()
    R.apply_edit_form(s, r, {k: form.get(k) for k in form.keys()})
    s.commit()
    return RedirectResponse(request.url_for("recipe_detail", recipe_id=r.id), status_code=303)


@router.post("/{recipe_id}/favorite", name="recipe_favorite")
def favorite(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    R.set_favorite(s, r, not r.favorite)
    s.commit()
    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/fav_button.html", {"r": r})
    return RedirectResponse(request.url_for("recipe_detail", recipe_id=r.id), status_code=303)


@router.post("/{recipe_id}/delete", name="recipe_delete")
def delete(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    R.soft_delete(s, r)
    s.commit()
    return RedirectResponse(request.url_for("library"), status_code=303)


@router.post("/{recipe_id}/tags", name="recipe_tag_add")
def tag_add(recipe_id: int, request: Request, name: str = Form(""), kind: str = Form("custom"),
            s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    if name.strip():
        R.add_tag(s, r, name, kind)
        s.commit()
    return templates.TemplateResponse(request, "partials/tags.html", {"r": r})


@router.post("/{recipe_id}/tags/{tag_id}/delete", name="recipe_tag_remove")
def tag_remove(recipe_id: int, tag_id: int, request: Request, s: Session = Depends(get_db)):
    r = _get(s, recipe_id)
    R.remove_tag(s, r, tag_id)
    s.commit()
    return templates.TemplateResponse(request, "partials/tags.html", {"r": r})


@router.post("/{recipe_id}/reextract", name="recipe_reextract")
def reextract(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    """Run the extraction stages again on the stored PDF (or re-fetch the URL)."""
    r = _get(s, recipe_id)
    from ...config import get_settings
    cfg = get_settings()
    if r.pdf_asset is None:
        raise HTTPException(400, "recipe has no PDF")
    path = cfg.assets_dir / r.pdf_asset.rel_path
    q = get_queue()
    r.status = "processing"
    s.commit()
    jid = q.enqueue("upload" if not r.source_url else "url", pdf_path=str(path), url=r.source_url,
                    title_hint=r.title, force=True, priority=5)
    # link the job to this recipe so the pipeline updates it instead of creating a new one
    from ...db.models import CaptureJob
    j = s.get(CaptureJob, jid)
    if j is not None:
        j.recipe_id = r.id
        s.commit()
    return RedirectResponse(request.url_for("recipe_detail", recipe_id=r.id), status_code=303)


# ---- cover photo -----------------------------------------------------------

@router.get("/{recipe_id}/pdf-image/{xref}", name="recipe_pdf_image")
def pdf_image(recipe_id: int, xref: int, s: Session = Depends(get_db)):
    """An image embedded in the recipe's PDF, for the cover picker."""
    from ...capture import pdf as pdfops
    r = _get(s, recipe_id)
    pdf = _pdf_path(r)
    png = pdfops.image_png(pdf, xref) if pdf else None
    if png is None:
        raise HTTPException(404)
    png, _w, _h = pdfops.resize_png(png, 480)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/{recipe_id}/page-image/{page}", name="recipe_page_image")
def page_image(recipe_id: int, page: int, s: Session = Depends(get_db)):
    from ...capture import pdf as pdfops
    r = _get(s, recipe_id)
    pdf = _pdf_path(r)
    if pdf is None or page < 0 or page >= (r.page_count or 1):
        raise HTTPException(404)
    png = pdfops.render_page_png(pdf, page, 480)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.post("/{recipe_id}/cover", name="recipe_cover")
async def set_cover(recipe_id: int, request: Request, choice: str = Form("keep"),
                    file: UploadFile | None = File(None), s: Session = Depends(get_db)):
    """choice: xref:<n> (image from the PDF), page:<n> (rendered page), upload, none, keep."""
    from ...capture import pdf as pdfops
    r = _get(s, recipe_id)
    pdf = _pdf_path(r)
    png: bytes | None = None
    if choice == "upload":
        data = await file.read() if file is not None else b""
        if not data:
            raise HTTPException(400, "choose an image file to upload")
        import io

        from PIL import Image
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"not an image: {e}") from e
        im.thumbnail((1600, 1600))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        png = buf.getvalue()
    elif choice.startswith("xref:") and pdf:
        png = pdfops.image_png(pdf, int(choice[5:]))
        if png is None:
            raise HTTPException(404, "image not found in the PDF")
    elif choice.startswith("page:") and pdf:
        page = int(choice[5:])
        if page < 0 or page >= (r.page_count or 1):
            raise HTTPException(404)
        png = pdfops.render_page_top_png(pdf, page, 800)
    elif choice == "none":
        png = None
    else:
        return RedirectResponse(request.url_for("recipe_edit", recipe_id=r.id), status_code=303)
    R.set_cover_from_png(s, r, png)
    s.commit()
    return RedirectResponse(request.url_for("recipe_detail", recipe_id=r.id), status_code=303)
