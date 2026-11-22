"""Which ingredients a step uses, worked out from its wording.

Recipes rarely say this outright, so a step is matched against each ingredient's name:
first the whole name ("rice vinegar", "bay leaf"), then its main word, the last one that
is not a description or a unit ("packed brown sugar" -> "sugar", "olive oil" -> "oil").
Words are compared whole and singular ("leaves" = "leaf", "tomatoes" = "tomato"), so
"egg" does not match "eggplant". Words a longer name has matched are not reused by a
shorter one, so "rice vinegar" in a step does not also claim the rice.
"""
from __future__ import annotations

import re

# words that describe or measure an ingredient rather than name it
_SKIP = set("""
a an and or of the to for with in into on at as taste plus about approx approximately optional
fresh freshly frozen dried dry raw cooked uncooked whole packed loosely firmly large medium small extra
chopped minced sliced diced grated shredded crushed ground peeled seeded halved quartered cubed beaten
softened melted cold warm hot room temperature boneless skinless lean finely roughly thinly coarsely
pound pounds lb lbs ounce ounces oz cup cups tablespoon tablespoons tbsp teaspoon teaspoons tsp
gram grams g kg ml liter liters litre litres pinch dash can cans package packages jar stick sticks
""".split())


# "vanilla extract" is called "the vanilla" in steps
_WEAK_HEAD = {"extract", "essence"}


def _sing(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ves"):
        return w[:-3] + "f"
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 4 and w.endswith("es") and re.search(r"(s|x|ch|sh)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _words(text: str) -> list[str]:
    return [_sing(w) for w in re.findall(r"[a-z]+", (text or "").lower())]


def _keys(ing) -> list[tuple[str, ...]]:
    """Phrases that name this ingredient, longest first: the whole name, then the main word
    of each part ("Salt & Pepper" -> salt, pepper)."""
    raw = ing.name_norm or ing.name or ""
    parts = re.split(r"\s*(?:&|\band\b|\bor\b|,|/)\s*", (ing.name or raw).lower()) if "&" in (ing.name or "") or " and " in (ing.name or "").lower() else [raw]
    keys: list[tuple[str, ...]] = []
    for part in [raw, *parts]:
        words = tuple(w for w in _words(part) if w not in _SKIP)
        if not words:
            continue
        extra = [words[-2:-1]] if len(words) > 1 and words[-1] in _WEAK_HEAD else []
        for k in (words, words[-1:], *extra):
            if k not in keys:
                keys.append(k)
    return sorted(keys, key=len, reverse=True)


def ingredients_by_step(steps, ingredients) -> dict[int, list[int]]:
    """{step id: [ingredient ids in list order]} for the steps that mention any ingredient."""
    keyed = [(ing, _keys(ing)) for ing in ingredients]
    out: dict[int, list[int]] = {}
    for st in steps:
        words = _words(st.text)
        taken = [False] * len(words)
        found: set[int] = set()
        # longer names first, across all ingredients, so a phrase is claimed before its parts
        for length in sorted({len(k) for _, ks in keyed for k in ks}, reverse=True):
            for ing, ks in keyed:
                if ing.id in found:
                    continue
                for k in (k for k in ks if len(k) == length):
                    for i in range(len(words) - length + 1):
                        if tuple(words[i:i + length]) == k and not any(taken[i:i + length]):
                            taken[i:i + length] = [True] * length
                            found.add(ing.id)
                            break
                    if ing.id in found:
                        break
        ids = [ing.id for ing in ingredients if ing.id in found]
        if ids:
            out[st.id] = ids
    return out
