"""Cook mode shows the ingredients each step uses, with amounts, in a collapsible list."""
import re


def _recipe():
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Ingredient, Recipe, Step
    with session_scope() as s:
        r = Recipe(title="Soup", status="ready", servings=2)
        r.ingredients.append(Ingredient(position=0, quantity=1, name="onion", name_norm="onion", raw_text="1 onion"))
        r.ingredients.append(Ingredient(position=1, quantity=2, unit="tbsp", name="olive oil", name_norm="olive oil",
                                        raw_text="2 tbsp olive oil"))
        r.ingredients.append(Ingredient(position=2, quantity=800, unit="g", name="tomatoes", name_norm="tomato",
                                        raw_text="800 g tomatoes"))
        r.steps.append(Step(position=0, text="Soften the onion in the oil."))
        r.steps.append(Step(position=1, text="Add the tomatoes."))
        r.steps.append(Step(position=2, text="Blend."))
        s.add(r)
        s.flush()
        return r.id


def _items(page, n):
    return [re.sub(r"\s+", " ", t).strip() for t in page.locator(f".step >> nth={n} >> details.step-ing li").all_text_contents()]


def test_each_step_lists_its_ingredients_with_amounts(live):
    rid = _recipe()
    browser, base = live
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/recipes/{rid}/cook")
    page.wait_for_selector(".step")

    assert _items(page, 0) == ["1 onion", "2 tbsp olive oil"]
    assert _items(page, 1) == ["800 g tomatoes"]
    assert page.locator(".step >> nth=2 >> details.step-ing").count() == 0       # "Blend." uses none
    # the current step's list starts open, the others closed
    assert page.locator(".step >> nth=0 >> details.step-ing").get_attribute("open") is not None
    assert page.locator(".step >> nth=1 >> details.step-ing").get_attribute("open") is None

    # opening a list does not tick the step off
    page.click(".step >> nth=1 >> details.step-ing summary")
    assert page.locator(".step >> nth=1 >> details.step-ing").get_attribute("open") is not None
    assert "done" not in (page.locator(".step >> nth=1").get_attribute("class") or "")

    # amounts follow the servings
    page.click("#srvPlus")
    page.click("#srvPlus")                    # 2 -> 4 servings
    assert _items(page, 0) == ["2 onion", "4 tbsp olive oil"]
    # and the list the cook opened stays open after the redraw
    assert page.locator(".step >> nth=1 >> details.step-ing").get_attribute("open") is not None
    assert errors == []
