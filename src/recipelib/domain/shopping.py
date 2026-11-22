"""One shared shopping list. Ingredients from recipes are merged by
(normalised name, unit family): amounts in the same family are summed in the
unit of the line already on the list; other families become separate lines."""
from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Recipe, ShoppingItem, ShoppingList
from ..extract.normalize import UNITS, normalize_name, parse_line, to_family_base, unit_family

AISLES = [
    ("produce", ["onion", "onions", "garlic", "tomato", "tomatoes", "lettuce", "pepper", "peppers", "carrot", "carrots", "celery",
                 "potato", "potatoes", "lemon", "lemons", "lime", "limes", "apple", "apples", "banana", "bananas", "berry", "berries",
                 "herb", "herbs", "parsley", "cilantro", "coriander", "basil", "spinach", "kale", "mushroom", "mushrooms", "avocado",
                 "ginger", "cucumber", "zucchini", "courgette", "squash", "broccoli", "cauliflower", "cabbage", "corn", "peas",
                 "orange", "oranges", "scallion", "scallions", "leek", "leeks", "chili", "chilli", "chilies", "shallot", "shallots",
                 "thyme", "rosemary", "mint", "dill", "chives", "salad", "greens"]),
    ("pantry", ["flour", "sugar", "salt", "oil", "vinegar", "rice", "pasta", "noodles", "lasagne", "lasagna", "spaghetti", "stock",
                "broth", "can", "cans", "tin", "tins", "sauce", "cumin", "paprika", "oregano", "cinnamon", "vanilla", "honey", "soy",
                "baking", "yeast", "oats", "lentils", "chickpeas", "beans", "paste", "purée", "puree", "nuts", "almonds", "peanut",
                "chocolate", "coffee", "tea", "spice", "spices", "pepper", "mustard", "ketchup", "mayonnaise", "syrup", "cornstarch",
                "cornflour", "breadcrumbs", "crackers", "sheets", "tortillas"]),
    ("meat & fish", ["chicken", "beef", "pork", "lamb", "turkey", "bacon", "sausage", "sausages", "ham", "steak", "mince",
                     "fish", "salmon", "shrimp", "prawns", "tuna", "cod", "thighs", "breast", "breasts", "rashers"]),
    ("dairy & eggs", ["milk", "butter", "cheese", "cream", "crème", "creme", "yogurt", "yoghurt", "egg", "eggs", "parmesan",
                      "mozzarella", "cheddar", "feta", "fraîche", "fraiche"]),
    ("bakery", ["bread", "buns", "rolls", "pita", "baguette", "naan", "loaf"]),
    ("frozen", ["frozen", "ice"]),]


_AISLE_RE = [(aisle, re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)) for aisle, words in AISLES]


def aisle_for(name_norm: str) -> str:
    """Whole-word keyword match; 'chicken stock' is pantry, 'chicken thighs' is meat."""
    n = name_norm.lower()
    # a pantry word anywhere wins over meat/dairy words ("chicken stock", "egg noodles")
    for aisle, rx in _AISLE_RE:
        if aisle == "pantry" and rx.search(n):
            return aisle
    for aisle, rx in _AISLE_RE:
        if rx.search(n):
            return aisle
    return "other"


def default_list(s: Session) -> ShoppingList:
    lst = s.scalars(select(ShoppingList).order_by(ShoppingList.id)).first()
    if lst is None:
        lst = ShoppingList(name="Shopping")
        s.add(lst)
        s.flush()
    return lst


def _find_merge_target(lst: ShoppingList, name_norm: str, unit: str | None) -> ShoppingItem | None:
    fam = unit_family(unit)
    for it in lst.items:
        if it.checked or it.name_norm != name_norm:
            continue
        if unit_family(it.unit) == fam:
            return it
    return None


def _convert(qty: float, from_unit: str | None, to_unit: str | None) -> float:
    if from_unit == to_unit or unit_family(from_unit) == "count" or unit_family(to_unit) == "count":
        return qty
    base = to_family_base(qty, from_unit)
    return base / UNITS[to_unit][1] if to_unit in UNITS else qty


def add_ingredient(s: Session, lst: ShoppingList, *, name: str, name_norm: str, quantity: float | None,
                   unit: str | None, source: dict | None = None, manual: bool = False) -> ShoppingItem:
    target = _find_merge_target(lst, name_norm, unit)
    if target is not None:
        if quantity is not None:
            if target.quantity is None:
                target.quantity = _convert(quantity, unit, target.unit) if target.unit else quantity
                target.unit = target.unit or unit
            else:
                target.quantity = round(target.quantity + _convert(quantity, unit, target.unit), 3)
        if source:
            src = json.loads(target.sources or "[]")
            src.append(source)
            target.sources = json.dumps(src)
        return target
    item = ShoppingItem(name=name, name_norm=name_norm, quantity=quantity, unit=unit, manual=1 if manual else 0,
                        category=aisle_for(name_norm), sources=json.dumps([source] if source else []),
                        position=len(lst.items))
    lst.items.append(item)
    s.flush()
    return item


def add_recipe(s: Session, lst: ShoppingList, r: Recipe, servings: float | None = None) -> int:
    factor = 1.0
    if servings and r.servings:
        factor = servings / r.servings
    n = 0
    for ing in r.ingredients:
        if ing.optional:
            continue
        q = round(ing.quantity * factor, 3) if ing.quantity is not None else None
        add_ingredient(s, lst, name=ing.name, name_norm=ing.name_norm or normalize_name(ing.name), quantity=q,
                       unit=ing.unit, source={"recipe_id": r.id, "title": r.title, "ingredient_id": ing.id, "qty": q, "unit": ing.unit})
        n += 1
    return n


def add_manual(s: Session, lst: ShoppingList, text: str) -> ShoppingItem | None:
    text = (text or "").strip()
    if not text:
        return None
    p = parse_line(text)
    return add_ingredient(s, lst, name=p.name or text, name_norm=p.name_norm, quantity=p.quantity, unit=p.unit, manual=True)


def grouped(lst: ShoppingList) -> list[tuple[str, list[ShoppingItem]]]:
    order = [a for a, _ in AISLES] + ["other"]
    buckets: dict[str, list[ShoppingItem]] = {k: [] for k in order}
    for it in lst.items:
        buckets.setdefault(it.category or "other", []).append(it)
    out = []
    for k in order:
        items = sorted(buckets.get(k, []), key=lambda i: (i.checked, i.name_norm))
        if items:
            out.append((k, items))
    return out


HAND = "Added by hand"


def _sources(item: ShoppingItem) -> list[dict]:
    try:
        return [x for x in json.loads(item.sources or "[]") if isinstance(x, dict) and x.get("title")]
    except ValueError:
        return []


def rows(lst: ShoppingList, by: str = "aisle") -> list[tuple[str, list[dict]]]:
    """The list as (heading, rows). by='aisle' groups by shop aisle; by='recipe' gives each
    recipe its own heading, so an item two recipes share shows under both (with that
    recipe's own amount when it is known) and items typed in by hand come last.
    A row is {it, qty, unit, note}; ticking a row ticks the one item behind it."""
    if by != "recipe":
        return [(aisle, [{"it": it, "qty": it.quantity, "unit": it.unit, "note": ", ".join(sources_of(it)) or None}
                         for it in items]) for aisle, items in grouped(lst)]
    aisle_rank = {a: i for i, (a, _) in enumerate(AISLES)}
    order: list[str] = []
    groups: dict[str, list[dict]] = {}
    for it in lst.items:
        srcs = _sources(it)
        titles = list(dict.fromkeys(x["title"] for x in srcs))
        if not titles:
            groups.setdefault(HAND, []).append({"it": it, "qty": it.quantity, "unit": it.unit, "note": None})
            continue
        for t in titles:
            if t not in groups:
                order.append(t)
                groups[t] = []
            mine = [x for x in srcs if x["title"] == t]
            others = [o for o in titles if o != t]
            qty, unit = it.quantity, it.unit
            # this recipe's own share, when the item is shared and the share is recorded in a unit we know
            if others and all(x.get("qty") is not None and "unit" in x for x in mine) and len({x["unit"] for x in mine}) == 1:
                qty, unit = round(sum(x["qty"] for x in mine), 3), mine[0]["unit"]
            groups[t].append({"it": it, "qty": qty, "unit": unit, "note": ("also for " + ", ".join(others)) if others else None})
    out = []
    for t in order + ([HAND] if HAND in groups else []):
        rs = sorted(groups[t], key=lambda r: (r["it"].checked, aisle_rank.get(r["it"].category or "other", 99), r["it"].name_norm))
        out.append((t, rs))
    return out


def clear_checked(s: Session, lst: ShoppingList) -> int:
    """Remove through the relationship (delete-orphan) so the in-session
    collection is current when the page re-renders."""
    keep = [it for it in lst.items if not it.checked]
    gone = len(lst.items) - len(keep)
    lst.items = keep
    s.flush()
    return gone


def remove_item(s: Session, lst: ShoppingList, item: ShoppingItem) -> None:
    lst.items = [it for it in lst.items if it.id != item.id]
    s.flush()


def clear_all(s: Session, lst: ShoppingList) -> None:
    lst.items = []
    s.flush()


def sources_of(item: ShoppingItem) -> list[str]:
    try:
        return sorted({str(x.get("title")) for x in json.loads(item.sources or "[]") if x.get("title")})
    except ValueError:
        return []


def as_text(lst: ShoppingList, include_checked: bool = False, by: str = "aisle", flat: bool = False) -> str:
    """Ready to paste into Google Keep (Show checkboxes), Notes, Reminders or a text message:
    a heading per aisle (or per recipe) with its items under it, groups separated by a
    blank line. flat=True gives just the items, one per line, for automations."""
    from ..extract.normalize import format_quantity
    blocks = []
    for head, rs in rows(lst, "aisle" if flat else by):   # flat: one line per item, with totals
        lines = []
        for r in rs:
            it = r["it"]
            if it.checked and not include_checked:
                continue
            q = format_quantity(r["qty"], r["unit"])
            lines.append(" ".join(x for x in (q, r["unit"] or "", it.name) if x))
        if lines:
            blocks.append(lines if flat else [head.upper()] + lines)
    if flat:
        return "\n".join(line for b in blocks for line in b)
    return "\n\n".join("\n".join(b) for b in blocks)
