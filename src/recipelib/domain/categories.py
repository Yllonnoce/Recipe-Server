"""A fixed list of categories every recipe can belong to (several at once).

Assigned three ways: the extractor is asked to pick from this list, keyword
rules over the title and ingredients fill gaps, and the edit page lets a
person tick them directly. Stored as tags with kind='category'.
"""
from __future__ import annotations

import re

# name, keyword hints (title + ingredient names, whole words), or None for "no auto rule"
CATEGORIES: list[tuple[str, list[str]]] = [
    ("Breakfast", ["pancake", "pancakes", "waffle", "waffles", "oatmeal", "porridge", "granola", "omelette", "omelet",
                   "frittata", "scrambled", "french toast", "breakfast", "brunch", "muesli", "hash browns"]),
    ("Lunch", ["sandwich", "sandwiches", "wrap", "wraps", "lunch", "panini", "quesadilla"]),
    ("Dinner", []),
    ("Appetizer", ["appetizer", "appetizers", "starter", "dip", "dips", "bruschetta", "crostini", "nachos", "wings",
                   "spring rolls", "hors d'oeuvre", "canapes", "canapés"]),
    ("Side", ["side", "sides", "coleslaw", "slaw", "mashed potatoes", "roasted vegetables", "pilaf", "stuffing"]),
    ("Soup", ["soup", "soups", "chowder", "bisque", "broth", "stew", "chili", "gumbo", "ramen", "pho"]),
    ("Salad", ["salad", "salads", "caesar", "vinaigrette", "tabbouleh"]),
    ("Pasta", ["pasta", "spaghetti", "penne", "lasagne", "lasagna", "fettuccine", "linguine", "macaroni", "rigatoni",
               "ravioli", "gnocchi", "tagliatelle", "noodles", "orzo", "carbonara", "mac and cheese"]),
    ("Seafood", ["fish", "salmon", "tuna", "cod", "haddock", "halibut", "tilapia", "trout", "shrimp", "prawn", "prawns",
                 "crab", "lobster", "scallop", "scallops", "clam", "clams", "mussel", "mussels", "oyster", "oysters",
                 "squid", "calamari", "octopus", "seafood", "anchovy", "anchovies", "sardines", "mackerel", "seabass", "sea bass"]),
    ("Poultry", ["chicken", "turkey", "duck", "poultry", "hen", "drumsticks", "thighs"]),
    ("Beef", ["beef", "steak", "steaks", "brisket", "ground beef", "mince", "minced beef", "burger", "burgers", "meatballs",
              "roast beef", "short ribs", "sirloin", "ribeye", "chuck", "veal"]),
    ("Pork", ["pork", "bacon", "ham", "sausage", "sausages", "chorizo", "prosciutto", "pancetta", "ribs", "pulled pork",
              "tenderloin", "pork chops", "salami", "pepperoni"]),
    ("Lamb", ["lamb", "mutton"]),
    ("Vegetarian", []),
    ("Vegan", ["vegan"]),
    ("Rice & Grains", ["rice", "risotto", "quinoa", "couscous", "bulgur", "farro", "barley", "fried rice", "biryani", "paella"]),
    ("Baking", ["bread", "loaf", "rolls", "buns", "biscuits", "scones", "muffins", "bagels", "focaccia", "sourdough",
                "dough", "pizza", "pastry", "croissant", "brioche", "pretzel"]),
    ("Dessert", ["dessert", "cake", "cakes", "cupcake", "cupcakes", "cookie", "cookies", "brownie", "brownies", "pie",
                 "tart", "cheesecake", "pudding", "ice cream", "sorbet", "mousse", "fudge", "candy", "frosting", "icing",
                 "cobbler", "crumble", "trifle", "custard", "meringue", "macaron", "macarons", "truffle", "truffles", "sweet"]),
    ("Snack", ["snack", "snacks", "popcorn", "trail mix", "energy balls", "granola bars", "chips", "crackers"]),
    ("Drinks", ["smoothie", "smoothies", "cocktail", "cocktails", "lemonade", "juice", "latte", "milkshake", "punch",
                "mocktail", "drink", "drinks", "beverage", "tea", "coffee", "hot chocolate", "sangria", "margarita"]),
    ("Sauces & Condiments", ["sauce", "sauces", "dressing", "marinade", "salsa", "pesto", "chutney", "relish", "jam", "jelly",
                             "gravy", "aioli", "mayonnaise", "ketchup", "hummus", "guacamole", "condiment", "pickles", "pickled"]),
    ("Slow Cooker & Instant Pot", ["slow cooker", "crockpot", "crock pot", "instant pot", "pressure cooker"]),
    ("Grilling", ["grilled", "grill", "bbq", "barbecue", "barbecued", "skewers", "kebab", "kebabs", "kabobs"]),
]
NAMES = [n for n, _ in CATEGORIES]
_MEAT = {"Seafood", "Poultry", "Beef", "Pork", "Lamb"}
_RX = {n: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) for n, words in CATEGORIES if words}
_COURSE_MAP = {"breakfast": "Breakfast", "brunch": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "main": "Dinner",
               "main course": "Dinner", "mains": "Dinner", "entree": "Dinner", "entrée": "Dinner", "supper": "Dinner",
               "dessert": "Dessert", "desserts": "Dessert", "appetizer": "Appetizer", "appetizers": "Appetizer",
               "starter": "Appetizer", "starters": "Appetizer", "side": "Side", "side dish": "Side", "sides": "Side",
               "snack": "Snack", "snacks": "Snack", "drink": "Drinks", "drinks": "Drinks", "beverage": "Drinks",
               "soup": "Soup", "salad": "Salad", "bread": "Baking", "baking": "Baking", "sauce": "Sauces & Condiments"}
_ANIMAL = re.compile(r"\b(egg|eggs|cheese|milk|butter|cream|yogurt|yoghurt|honey|gelatin|gelatine|fish sauce|"
                     r"worcestershire|parmesan|mozzarella|cheddar|feta|ghee|lard|anchov\w*)\b", re.I)


def normalize(name: str) -> str | None:
    """Map a free-form category/course word to one of the fixed names, or None."""
    n = (name or "").strip().lower()
    if not n:
        return None
    for c in NAMES:
        if c.lower() == n:
            return c
    if n in _COURSE_MAP:
        return _COURSE_MAP[n]
    for c in NAMES:
        if n.replace("&", "and") == c.lower().replace("&", "and"):
            return c
    return None


def suggest(title: str, ingredient_names: list[str], courses: list[str] = (), llm_categories: list[str] = ()) -> list[str]:
    """Categories for a recipe from its title, ingredients, course words and
    whatever the extractor proposed. Ordered as in CATEGORIES."""
    found: set[str] = set()
    for c in list(llm_categories) + list(courses):
        m = normalize(str(c))
        if m:
            found.add(m)
    title_l = (title or "").lower()
    ings = " | ".join(ingredient_names).lower()
    for name, rx in _RX.items():
        if rx.search(title_l):
            found.add(name)
    # ingredient rules only for the protein / dietary groups (a lasagne mentions
    # "sauce" but is not a condiment)
    for name in ("Seafood", "Poultry", "Beef", "Pork", "Lamb", "Pasta"):
        if _RX[name].search(ings):
            found.add(name)
    sweet_or_other = found & {"Dessert", "Baking", "Sauces & Condiments", "Snack"}
    # plant milks and "vegan cheese" are not animal products
    ings = re.sub(r"\b(oat|almond|soy|soya|coconut|rice|cashew|hemp|pea|macadamia)\s+milk\b", "plantmilk", ings)
    ings = re.sub(r"\bvegan\s+\w+", "veganitem", ings)
    if not (found & _MEAT) and not sweet_or_other and ings.strip():
        found.add("Vegetarian")
        if not _ANIMAL.search(ings):
            found.add("Vegan")
    if "Vegan" in found:
        found.add("Vegetarian")
    if "Dessert" in found or "Drinks" in found or "Sauces & Condiments" in found:
        found.discard("Dinner")
    # a savoury main with a protein and no other meal slot is dinner
    if found & _MEAT and not (found & {"Breakfast", "Lunch", "Appetizer", "Side", "Snack", "Soup", "Salad"}):
        found.add("Dinner")
    if "Pasta" in found or "Rice & Grains" in found:
        if not (found & {"Breakfast", "Dessert", "Side", "Salad"}):
            found.add("Dinner")
    return [n for n in NAMES if n in found]
