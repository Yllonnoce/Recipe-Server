"""The shopping list grouped by aisle or by recipe, and the text export keeping those headings."""
import html as H
import re

from recipelib.db.engine import session_scope
from recipelib.db.models import Ingredient, Recipe


def _recipe(title: str, lines: list[tuple[float | None, str | None, str]]) -> int:
    with session_scope() as s:
        r = Recipe(title=title, status="ready", servings=4)
        for i, (q, u, name) in enumerate(lines):
            r.ingredients.append(Ingredient(position=i, quantity=q, unit=u, name=name, name_norm=name, raw_text=f"{q} {u} {name}"))
        s.add(r)
        s.flush()
        return r.id


def _fill(client):
    pie = _recipe("Apple Pie", [(6, None, "apples"), (2, "cup", "flour"), (1, None, "egg")])
    pan = _recipe("Pancakes", [(1, "cup", "flour"), (2, None, "egg"), (1, "cup", "milk")])
    client.post(f"/shopping/add-recipe/{pie}")
    client.post(f"/shopping/add-recipe/{pan}")
    client.post("/shopping/items", data={"text": "dish soap"})


def _groups(html: str) -> dict[str, list[str]]:
    out = {}
    for head, body in re.findall(r'<div class="group[^"]*">\s*<h3>(.*?)</h3>(.*?)</div>\s*(?=<div class="group|$)', html, re.S):
        out[H.unescape(re.sub(r"<[^>]+>", "", head).strip())] = [H.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", n)).strip())
                                                     for n in re.findall(r'<span class="nm">(.*?)</span>', body, re.S)]
    return out


def test_by_aisle_is_the_default(client):
    _fill(client)
    g = _groups(client.get("/partials/shopping").text)
    assert set(g) >= {"produce", "pantry", "dairy & eggs"}
    assert g["dairy & eggs"] == ["egg", "milk"]


def test_by_recipe_lists_each_recipes_items(client):
    _fill(client)
    client.get("/shopping?by=recipe")                 # the choice is remembered
    html = client.get("/partials/shopping").text
    g = _groups(html)
    assert list(g) == ["Apple Pie", "Pancakes", "Added by hand"]
    assert set(g["Apple Pie"]) == {"apples", "flour", "egg"}
    assert set(g["Pancakes"]) == {"flour", "egg", "milk"}
    assert g["Added by hand"] == ["dish soap"]
    # a shared item shows this recipe's own amount and where else it goes
    pie_flour = re.search(r"Apple Pie</h3>.*?<div class=\"row[^\"]*\">(?:(?!</div>).)*?flour.*?</div>", html, re.S).group(0)
    assert re.search(r'class="q">\s*2\s+cup', pie_flour) and "also for Pancakes" in pie_flour


def test_switching_back_to_aisles(client):
    _fill(client)
    client.get("/shopping?by=recipe")
    client.get("/shopping?by=aisle")
    assert "produce" in _groups(client.get("/partials/shopping").text)


def test_the_export_keeps_the_aisle_headings(client):
    _fill(client)
    text = client.get("/shopping.txt").text
    blocks = [b.splitlines() for b in text.strip().split("\n\n")]
    heads = [b[0] for b in blocks]
    assert heads == ["PRODUCE", "PANTRY", "DAIRY & EGGS", "OTHER"]
    assert blocks[0][1:] == ["6 apples"]
    assert "3 cup flour" in blocks[1]


def test_the_export_follows_the_recipe_view(client):
    _fill(client)
    client.get("/shopping?by=recipe")
    heads = [b.splitlines()[0] for b in client.get("/shopping.txt").text.strip().split("\n\n")]
    assert heads == ["APPLE PIE", "PANCAKES", "ADDED BY HAND"]


def test_flat_export_for_automations(client):
    _fill(client)
    lines = client.get("/shopping.txt?flat=1").text.strip().splitlines()
    assert "PRODUCE" not in lines and "6 apples" in lines and "" not in lines
