from pathlib import Path

from recipelib.capture.sources.url import scrape
from recipelib.extract.llm import choose_window, clean_text


def test_scrape_jsonld_fixture():
    html = Path(__file__).with_name("fixtures").joinpath("jsonld_recipe.html").read_text()
    draft, image = scrape(html, "https://example.com/banana-bread")
    assert draft["method"] == "scraper" and draft["title"] == "Best Banana Bread"
    assert len(draft["ingredients"]) == 8 and draft["ingredients"][0]["raw"] == "3 ripe bananas, mashed"
    assert len(draft["steps"]) == 4 and draft["steps"][-1]["text"] == "Bake for 60 minutes."
    assert draft["prep_minutes"] == 15 and draft["cook_minutes"] == 60 and draft["total_minutes"] == 75
    assert draft["course"] == "dessert" and draft["cuisine"] == "american"
    assert draft["servings"] == 10.0 and draft["yield_text"] == "1 loaf (10 slices)"
    assert image == "https://example.com/banana.jpg"


def test_scrape_non_recipe_page():
    draft, image = scrape("<html><body><h1>About us</h1></body></html>", "https://example.com/about")
    assert draft is None


def test_clean_text_drops_boilerplate():
    t = clean_text("Title\nhttps://example.com/x\n\n\n\n2 cups flour\n3 of 12\n9/12/2026, 10:15 AM\nBake")
    assert "https://" not in t and "3 of 12" not in t and "10:15" not in t
    assert "2 cups flour" in t and "\n\n\n" not in t


def test_choose_window_prefers_recipe_looking_text():
    filler = ("Lorem ipsum dolor sit amet consectetur. " * 100 + "\n")
    recipe = "Ingredients\n2 cups flour\n1 tsp salt\n3 tbsp butter\nDirections\nPreheat the oven and bake for 20 minutes.\n"
    text = filler * 10 + recipe * 3 + filler * 10
    w = choose_window(text, limit=9000)
    assert len(w) <= 9000 and "2 cups flour" in w


def test_llm_sections_are_flattened_to_groups(library, monkeypatch):
    import json
    from recipelib.extract import llm
    reply = {"is_recipe": True, "title": "Chowder", "description": None, "language": "en", "confidence": 0.9,
             "ingredient_sections": [{"heading": "Lobster Stock", "ingredients": [{"raw": "2 lobster shells", "name": "lobster shells", "optional": False}]},
                                     {"heading": "Chowder", "ingredients": [{"raw": "4 slices bacon", "name": "bacon", "optional": False}]}],
             "step_sections": [{"heading": "Lobster Stock", "steps": [{"text": "Simmer the shells."}]},
                               {"heading": "Chowder", "steps": [{"text": "Cook the bacon."}]}]}

    class FakeClient:
        def chat(self, **kw):
            return {"message": {"content": json.dumps(reply)}}
    monkeypatch.setattr(llm, "_client", lambda: FakeClient())
    d = llm.extract_recipe("Lobster Stock\n2 lobster shells\nChowder\n4 slices bacon\nSimmer the shells. Cook the bacon.")
    assert [i["group"] for i in d["ingredients"]] == ["Lobster Stock", "Chowder"]
    assert [s["group"] for s in d["steps"]] == ["Lobster Stock", "Chowder"]
    # a single unnamed section means no groups at all
    reply["ingredient_sections"] = [{"heading": None, "ingredients": reply["ingredient_sections"][0]["ingredients"]}]
    reply["step_sections"] = [{"heading": None, "steps": [{"text": "Simmer."}]}]
    d = llm.extract_recipe("Simple stock\n2 lobster shells\n8 cups water\nSimmer the shells in the water for an hour.")
    assert all(i["group"] is None for i in d["ingredients"]) and all(s["group"] is None for s in d["steps"])
