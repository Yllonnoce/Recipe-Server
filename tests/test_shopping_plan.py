from datetime import date

from sqlalchemy import select

from recipelib.db.models import Recipe
from .conftest import make_pdf, wait_for


def _ready_recipe(client, tmp_path, title, ingredients, servings="4"):
    pdf = make_pdf(tmp_path / f"{title}.pdf", [title])
    with pdf.open("rb") as fh:
        client.post("/capture/upload", files=[("files", (f"{title}.pdf", fh, "application/pdf"))])
    wait_for(lambda: "done" in client.get("/partials/jobs").text and "running" not in client.get("/partials/jobs").text)
    from recipelib.db.engine import session_scope
    with session_scope() as s:
        rid = s.scalars(select(Recipe).where(Recipe.title == title)).first().id
    client.post(f"/recipes/{rid}/edit", data={"title": title, "servings": servings, "ingredients": ingredients,
                                             "steps": "Cook for 10 minutes.\nServe."}, follow_redirects=False)
    return rid


def test_shopping_merge_and_scale(client, library, tmp_path):
    a = _ready_recipe(client, tmp_path, "Soup", "2 cups flour\n1 onion, chopped\n3 cloves garlic\nsalt")
    b = _ready_recipe(client, tmp_path, "Bread", "1 cup flour\n2 tbsp butter\n1 large onion\n1/2 cup milk")
    # add soup at double servings, bread as-is
    r = client.post(f"/shopping/add-recipe/{a}", data={"servings": "8"}, headers={"HX-Request": "true"})
    assert "4 ingredients added" in r.text
    client.post(f"/shopping/add-recipe/{b}", data={"servings": ""})
    html = client.get("/shopping").text
    assert "5 cup" in html and "flour" in html            # 2*2 + 1 merged in cups
    assert html.count("onion") == 1                        # onion + large onion merged (count family)
    assert "garlic" in html and "butter" in html and "milk" in html
    from recipelib.db.engine import session_scope
    from recipelib.db.models import ShoppingItem
    with session_scope() as s:
        items = {i.name_norm: i for i in s.scalars(select(ShoppingItem))}
        assert items["flour"].quantity == 5 and items["flour"].unit == "cup"
        assert items["onion"].quantity == 3
        assert items["flour"].category == "pantry" and items["onion"].category == "produce"
        garlic = items["garlic"].id
    # manual item, toggle, clear
    html = client.post("/shopping/items", data={"text": "2 lb apples"}).text
    assert "apple" in html
    html = client.post(f"/shopping/items/{garlic}/toggle").text
    assert 'row checked' in html
    html = client.post("/shopping/clear-checked").text
    assert "garlic" not in html and "apple" in html
    # cook mode page renders with the JSON payload
    cook = client.get(f"/recipes/{a}/cook").text
    assert 'id="recipe-data"' in cook and '"servings": 4.0' in cook and "flour" in cook


def test_meal_plan_week_and_shopping(client, library, tmp_path):
    rid = _ready_recipe(client, tmp_path, "Tacos", "1 lb beef\n8 tortillas")
    monday = date(2026, 9, 14)
    r = client.post("/plan/add", data={"date": "2026-09-16", "slot": "dinner", "recipe_id": str(rid), "servings": "8"}, follow_redirects=False)
    assert r.status_code == 303 and "week=2026-09-14" in r.headers["location"]
    client.post("/plan/add", data={"date": "2026-09-15", "slot": "lunch", "note": "Pizza night out"}, follow_redirects=False)
    html = client.get("/plan", params={"week": monday.isoformat()}).text
    assert "Tacos" in html and "Pizza night out" in html and "Wednesday" in html and "×8" in html
    assert "Tacos" not in client.get("/plan", params={"week": "2026-09-21"}).text
    picker = client.get("/partials/recipe-picker", params={"q": "tac"}).text
    assert "Tacos" in picker
    r = client.post("/plan/shopping", data={"week": monday.isoformat()}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/shopping").text
    assert "beef" in html and "2 lb" in html            # 1 lb scaled to 8 servings from 4
    from recipelib.db.engine import session_scope
    from recipelib.db.models import MealPlanEntry
    with session_scope() as s:
        eid = s.scalars(select(MealPlanEntry).where(MealPlanEntry.note == "Pizza night out")).first().id
    client.post(f"/plan/{eid}/delete", follow_redirects=False)
    assert "Pizza night out" not in client.get("/plan", params={"week": monday.isoformat()}).text
