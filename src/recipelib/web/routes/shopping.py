from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...db.engine import get_db
from ...db.models import ShoppingItem
from ...domain import recipes as R
from ...domain import shopping as S
from ..templating import templates

router = APIRouter()


def _ctx(s: Session) -> dict:
    lst = S.default_list(s)
    return {"lst": lst, "groups": S.grouped(lst), "sources_of": S.sources_of,
            "n_open": sum(1 for i in lst.items if not i.checked)}


@router.get("/shopping", name="shopping")
def shopping(request: Request, s: Session = Depends(get_db)):
    from datetime import date
    return templates.TemplateResponse(request, "pages/shopping.html", {**_ctx(s), "today": date.today().strftime("%A %b %-d")})


@router.get("/shopping.txt", name="shopping_text")
def shopping_text(request: Request, s: Session = Depends(get_db)):
    """Plain text, one item per line. Paste into Google Keep and tap
    'Show checkboxes' to get a checklist; also handy for automations."""
    from fastapi.responses import PlainTextResponse
    body = S.as_text(S.default_list(s), include_checked=request.query_params.get("all") == "1")
    return PlainTextResponse(body + ("\n" if body else ""), headers={"Cache-Control": "no-store"})


@router.get("/partials/shopping", name="shopping_partial")
def shopping_partial(request: Request, s: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "partials/shopping_list.html", _ctx(s))


@router.post("/shopping/add-recipe/{recipe_id}", name="shopping_add_recipe")
def add_recipe(recipe_id: int, request: Request, servings: str = Form(""), s: Session = Depends(get_db)):
    r = R.get(s, recipe_id)
    if r is None:
        raise HTTPException(404)
    try:
        srv = float(servings) if servings.strip() else None
    except ValueError:
        srv = None
    n = S.add_recipe(s, S.default_list(s), r, srv)
    s.commit()
    if request.headers.get("HX-Request") == "true":
        from fastapi.responses import HTMLResponse
        return HTMLResponse(f'{n} ingredient{"s" if n != 1 else ""} added · <a href="{request.url_for("shopping")}">open list</a>')
    return RedirectResponse(request.url_for("shopping"), status_code=303)


@router.post("/shopping/items", name="shopping_add_item")
def add_item(request: Request, text: str = Form(""), s: Session = Depends(get_db)):
    S.add_manual(s, S.default_list(s), text)
    s.commit()
    return templates.TemplateResponse(request, "partials/shopping_list.html", _ctx(s))


@router.post("/shopping/items/{item_id}/toggle", name="shopping_toggle")
def toggle(item_id: int, request: Request, s: Session = Depends(get_db)):
    it = s.get(ShoppingItem, item_id)
    if it is not None:
        it.checked = 0 if it.checked else 1
        s.commit()
    return templates.TemplateResponse(request, "partials/shopping_list.html", _ctx(s))


@router.post("/shopping/items/{item_id}/delete", name="shopping_delete")
def delete(item_id: int, request: Request, s: Session = Depends(get_db)):
    it = s.get(ShoppingItem, item_id)
    if it is not None:
        S.remove_item(s, S.default_list(s), it)
        s.commit()
    return templates.TemplateResponse(request, "partials/shopping_list.html", _ctx(s))


@router.post("/shopping/clear-checked", name="shopping_clear")
def clear(request: Request, s: Session = Depends(get_db)):
    S.clear_checked(s, S.default_list(s))
    s.commit()
    return templates.TemplateResponse(request, "partials/shopping_list.html", _ctx(s))


@router.post("/shopping/clear-all", name="shopping_clear_all")
def clear_all(request: Request, s: Session = Depends(get_db)):
    S.clear_all(s, S.default_list(s))
    s.commit()
    return templates.TemplateResponse(request, "partials/shopping_list.html", _ctx(s))
