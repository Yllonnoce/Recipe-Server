import pytest

from recipelib.extract.normalize import (detect_minutes, detect_times, format_quantity, normalize_name,
                                         parse_ingredient_block, parse_line)


@pytest.mark.parametrize("line,qty,unit,name", [
    ("2 cups all-purpose flour", 2.0, "cup", "all-purpose flour"),
    ("1 1/2 tsp salt", 1.5, "tsp", "salt"),
    ("½ cup sugar", 0.5, "cup", "sugar"),
    ("1½ cups milk", 1.5, "cup", "milk"),
    ("2-3 cloves garlic, minced", 2.0, "clove", "garlic"),
    ("3 large eggs", 3.0, "large", "eggs"),
    ("Salt to taste", None, None, "Salt to taste"),
    ("1 (14 oz) can tomatoes", 1.0, None, "can tomatoes"),
    ("250 g butter, softened", 250.0, "g", "butter"),
    ("2 tablespoons olive oil", 2.0, "tbsp", "olive oil"),
    ("1/4 cup fresh parsley (chopped)", 0.25, "cup", "fresh parsley"),
])
def test_parse_line(line, qty, unit, name):
    p = parse_line(line)
    assert p.quantity == pytest.approx(qty) if qty is not None else p.quantity is None
    assert p.unit == unit
    assert p.name == name


def test_range_and_prep():
    p = parse_line("2 to 3 lbs chicken thighs, trimmed")
    assert p.quantity == 2 and p.quantity_max == 3 and p.unit == "lb"
    assert p.preparation == "trimmed"


def test_optional_flag():
    assert parse_line("1 tsp chili flakes (optional)").optional


def test_normalize_name():
    assert normalize_name("fresh tomatoes, chopped") == "tomato"
    assert normalize_name("Large Eggs") == "egg"
    assert normalize_name("all-purpose flour (sifted)") == "all-purpose flour"
    assert normalize_name("cherries") == "cherry"


def test_block_groups():
    rows = parse_ingredient_block("For the crust:\n2 cups flour\n\nFilling:\n3 apples")
    assert [r["group_name"] for r in rows] == ["For the crust", "Filling"]
    assert rows[1]["name"] == "apples"


def test_detect_minutes():
    assert detect_minutes("Bake for 25 minutes until golden") == 25
    assert detect_minutes("Simmer 1 hour") == 60
    assert detect_minutes("Serve") is None


def test_format_quantity():
    assert format_quantity(1.5, "cup") == "1 ½"
    assert format_quantity(0.25) == "¼"
    assert format_quantity(3.0) == "3"
    assert format_quantity(250.0, "g") == "250"
    assert format_quantity(2.333, "cup") == "2 ⅓"


def test_detect_times_header_line():
    t = detect_times("Lemon Chicken\nServes 4 · Prep 15 min · Cook 1 hour\nIngredients")
    assert t == {"prep_min": 15, "cook_min": 60, "total_min": None, "servings": 4.0}
    assert detect_times("no numbers here")["prep_min"] is None
