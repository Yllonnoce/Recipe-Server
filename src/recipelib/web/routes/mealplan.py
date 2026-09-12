from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...db.engine import get_db
from ...db.models import MealPlanEntry
from ...domain import mealplan as M
from ...domain import recipes as R
from ...domain import shopping as S
from ..templating import templates

router = APIRouter()


def _week_url(request: Request, start: date) -> str:
    return str(request.url_for("plan")) + f"?week={start.isoformat()}"


@router.get("/plan", name="plan")
def plan(request: Request, s: Session = Depends(get_db)):
    start = M.week_start(M.parse_date(request.query_params.get("week")))
    days = M.week_entries(s, start)
    return templates.TemplateResponse(request, "pages/plan.html", {
        "start": start, "days": days, "today": date.today(), "slots": M.SLOTS,
        "prev": start - timedelta(days=7), "next": start + timedelta(days=7),
        "message": request.query_params.get("m"), "timedelta": timedelta,
    })


@router.get("/partials/recipe-picker", name="recipe_picker")
def picker(request: Request, s: Session = Depends(get_db)):
    f = R.ListFilter(q=request.query_params.get("q", ""), per_page=12, status=None)
    rows, _ = R.list_recipes(s, f)
    rows = [r for r in rows if r.ingredients or r.steps or True]
    return templates.TemplateResponse(request, "partials/recipe_picker.html", {"recipes": rows})


@router.post("/plan/add", name="plan_add")
def plan_add(request: Request, date_: str = Form("", alias="date"), slot: str = Form("dinner"),
             recipe_id: str = Form(""), note: str = Form(""), servings: str = Form(""),
             s: Session = Depends(get_db)):
    d = M.parse_date(date_) or date.today()
    try:
        rid = int(recipe_id) if recipe_id.strip() else None
    except ValueError:
        rid = None
    try:
        srv = float(servings) if servings.strip() else None
    except ValueError:
        srv = None
    M.add_entry(s, d, slot, rid, note, srv)
    s.commit()
    return RedirectResponse(_week_url(request, M.week_start(d)), status_code=303)


@router.post("/plan/{entry_id}/delete", name="plan_delete")
def plan_delete(entry_id: int, request: Request, s: Session = Depends(get_db)):
    e = s.get(MealPlanEntry, entry_id)
    start = M.week_start(date.fromisoformat(e.date)) if e else M.week_start()
    if e is not None:
        s.delete(e)
        s.commit()
    return RedirectResponse(_week_url(request, start), status_code=303)


@router.post("/plan/shopping", name="plan_shopping")
def plan_shopping(request: Request, week: str = Form(""), s: Session = Depends(get_db)):
    start = M.week_start(M.parse_date(week))
    lst = S.default_list(s)
    n = 0
    for r, srv in M.week_recipes(s, start):
        n += S.add_recipe(s, lst, r, srv)
    s.commit()
    return RedirectResponse(str(request.url_for("shopping")) + f"?m={n} ingredients added from the week's plan", status_code=303)
