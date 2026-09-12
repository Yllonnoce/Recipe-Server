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
