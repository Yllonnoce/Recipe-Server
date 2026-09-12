"""Cook mode: a large-type, hands-free view of one recipe for the kitchen tablet."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ...db.engine import get_db
from ...domain import recipes as R
from ..templating import templates

router = APIRouter(prefix="/recipes")


def recipe_json(r) -> dict:
    return {
        "id": r.id, "title": r.title, "servings": r.servings, "yield_text": r.yield_text,
        "ingredients": [{"id": i.id, "group": i.group_name, "raw": i.raw_text, "quantity": i.quantity,
                         "quantity_max": i.quantity_max, "unit": i.unit, "unit_raw": i.unit_raw, "name": i.name,
                         "preparation": i.preparation, "optional": bool(i.optional)} for i in r.ingredients],
        "steps": [{"id": s.id, "group": s.group_name, "text": s.text, "minutes": s.minutes} for s in r.steps],
    }


@router.get("/{recipe_id}/cook", name="recipe_cook")
def cook(recipe_id: int, request: Request, s: Session = Depends(get_db)):
    r = R.get(s, recipe_id)
    if r is None:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "pages/cook.html", {"r": r, "data": recipe_json(r)})
