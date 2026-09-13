from recipelib.extract.postprocess import clean_ingredients, infer_ingredient_groups, infer_step_groups, looks_like_heading

TEXT = """Lobster Corn Chowder
For the lobster stock
2 lobster shells
8 cups water
For the chowder
4 slices bacon, chopped
Salt and pepper
Make the stock
1. Put the shells and water in a pot. Simmer for 45 minutes.
2. Strain and reserve 6 cups.
Make the chowder
1. Cook the bacon until crisp.
2. Add the cream and heat through.
"""


def test_clean_ingredients_splits_blobs_and_drops_headings():
    items = [
        {"group": "For the lobster stock", "raw": "For the lobster stock\n2 lobster shells\n8 cups water", "name": "x"},
        {"group": "For the lobster stock", "raw": "8 cups water", "name": "water", "quantity": 8, "unit": "cups"},
        {"group": "For the chowder", "raw": "For the chowder", "name": "For the chowder"},
        {"group": "For the chowder", "raw": "4 slices bacon, chopped", "name": "bacon", "quantity": 4},
        {"group": "For the chowder", "raw": "Salt and pepper", "name": "salt and pepper"},
        {"group": None, "raw": "Topping:", "name": "Topping"},
    ]
    out = clean_ingredients(items)
    raws = [o["raw"] for o in out]
    assert raws == ["2 lobster shells", "8 cups water", "4 slices bacon, chopped", "Salt and pepper"]
    assert out[0]["group"] == "For the lobster stock" and out[2]["group"] == "For the chowder"
    assert out[1]["quantity"] == 8          # the parsed duplicate replaced the bare line


def test_infer_step_groups_from_text():
    steps = [{"text": "Put the shells and water in a pot. Simmer for 45 minutes."}, {"text": "Strain and reserve 6 cups."},
             {"text": "Cook the bacon until crisp."}, {"text": "Add the cream and heat through."}]
    out = infer_step_groups(steps, TEXT, ["For the lobster stock", "For the chowder"])
    assert [s["group"] for s in out] == ["Make the stock", "Make the stock", "Make the chowder", "Make the chowder"]
    # already grouped: untouched; single section: dropped
    assert infer_step_groups([{"text": "a", "group": "X"}], TEXT, [])[0]["group"] == "X"
    one = infer_step_groups([{"text": "Cook the bacon until crisp."}, {"text": "Add the cream and heat through."}], TEXT, [])
    assert all(s.get("group") is None for s in one)


def test_looks_like_heading():
    assert looks_like_heading("For the sauce:") and looks_like_heading("Make the stock") and looks_like_heading("Topping")
    assert not looks_like_heading("2 cups flour") and not looks_like_heading("Simmer for 45 minutes.")


def test_infer_ingredient_groups_from_text():
    items = [{"raw": "2 lobster shells"}, {"raw": "8 cups water"}, {"raw": "4 slices bacon, chopped"}, {"raw": "Salt and pepper"}]
    out = infer_ingredient_groups(items, TEXT)
    assert [i["group"] for i in out] == ["For the lobster stock", "For the lobster stock", "For the chowder", "For the chowder"]
    flat = infer_ingredient_groups([{"raw": "2 cups flour"}, {"raw": "1 egg"}], "Pancakes\n2 cups flour\n1 egg\nMix.")
    assert all(i.get("group") is None for i in flat)
