"""Which ingredients each step uses, for the collapsible list under a step in cook mode."""
from types import SimpleNamespace as NS

import pytest

from recipelib.domain.step_ingredients import ingredients_by_step


def _ings(*names):
    return [NS(id=i + 1, name=n, name_norm=n.lower()) for i, n in enumerate(names)]


def _steps(*texts):
    return [NS(id=100 + i, text=t) for i, t in enumerate(texts)]


def _names(ings, steps):
    by_id = {i.id: i.name for i in ings}
    m = ingredients_by_step(steps, ings)
    return [[by_id[i] for i in m.get(s.id, [])] for s in steps]


def test_names_and_main_words_are_found():
    ings = _ings("olive oil", "onion", "packed brown sugar", "chicken stock", "salt")
    steps = _steps("Soften the onion in the oil.", "Stir in the sugar and stock; season with salt.", "Serve.")
    assert _names(ings, steps) == [["olive oil", "onion"], ["packed brown sugar", "chicken stock", "salt"], []]


def test_plurals_both_ways():
    ings = _ings("dried bay leaf", "tomatoes", "whole peppercorns", "berries", "potato")
    steps = _steps("Add the bay leaves and peppercorn.", "Halve the tomato.", "Top with a berry.", "Boil the potatoes.")
    assert _names(ings, steps) == [["dried bay leaf", "whole peppercorns"], ["tomatoes"], ["berries"], ["potato"]]


def test_messy_real_world_names():
    ings = [NS(id=1, name=", 20-25 pound uncooked fresh turkey", name_norm=""),
            NS(id=2, name="Salt & Pepper", name_norm="salt pepper"),
            NS(id=3, name="butter", name_norm="butter")]
    steps = _steps("Pat the turkey dry.", "Season with black pepper.", "Slather with the softened butter.")
    assert _names(ings, steps) == [[", 20-25 pound uncooked fresh turkey"], ["Salt & Pepper"], ["butter"]]


def test_vanilla_extract_is_the_vanilla():
    ings = _ings("egg", "pure vanilla extract")
    assert _names(ings, _steps("Add the egg and vanilla.")) == [["egg", "pure vanilla extract"]]


def test_whole_words_only():
    ings = _ings("egg", "oil", "ham")
    steps = _steps("Use eggplant and boiling water.", "Add the eggs, then the shallots.")
    assert _names(ings, steps) == [[], ["egg"]]


def test_a_two_word_name_matches_before_its_main_word_alone():
    # "rice vinegar" and "rice": a step about the vinegar should not also claim the rice
    ings = _ings("rice", "rice vinegar")
    steps = _steps("Cook the rice.", "Whisk in the rice vinegar.")
    assert _names(ings, steps) == [["rice"], ["rice vinegar"]]


def test_cook_page_data_lists_each_steps_ingredients(client):
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Ingredient, Recipe, Step
    from recipelib.web.routes.cook import recipe_json
    with session_scope() as s:
        r = Recipe(title="Soup", status="ready")
        r.ingredients.append(Ingredient(position=0, quantity=1, name="onion", name_norm="onion", raw_text="1 onion"))
        r.ingredients.append(Ingredient(position=1, quantity=2, unit="tbsp", name="olive oil", name_norm="olive oil", raw_text="2 tbsp olive oil"))
        r.steps.append(Step(position=0, text="Soften the onion in the oil."))
        r.steps.append(Step(position=1, text="Blend."))
        s.add(r)
        s.flush()
        data = recipe_json(r)
        ids = [i["id"] for i in data["ingredients"]]
    assert data["steps"][0]["ingredients"] == ids and data["steps"][1]["ingredients"] == []
