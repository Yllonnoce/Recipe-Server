from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...db.engine import get_db
from ...domain import recipes as R
from ..templating import templates

router = APIRouter()


def _filter(request: Request) -> R.ListFilter:
    q = request.query_params
    try:
        page = max(1, int(q.get("page", 1)))
    except ValueError:
        page = 1
    return R.ListFilter(
        q=q.get("q", ""), tag=q.get("tag") or None, favorites=q.get("fav") == "1",
        status=q.get("status") or None, sort=q.get("sort", "newest"), page=page,
    )


@router.get("/", name="library")
def library(request: Request, s: Session = Depends(get_db)):
    f = _filter(request)
    rows, total = R.list_recipes(s, f)
    return templates.TemplateResponse(request, "pages/library.html", {
        "recipes": rows, "total": total, "f": f, "tags": R.tag_counts(s),
        "status_counts": R.status_counts(s), "pages": (total + f.per_page - 1) // f.per_page,
    })


@router.get("/partials/cards", name="library_cards")
def cards(request: Request, s: Session = Depends(get_db)):
    f = _filter(request)
    rows, total = R.list_recipes(s, f)
    return templates.TemplateResponse(request, "partials/cards.html", {
        "recipes": rows, "total": total, "f": f, "pages": (total + f.per_page - 1) // f.per_page,
    })
