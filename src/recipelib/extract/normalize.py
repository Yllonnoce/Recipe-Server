"""Ingredient line parsing and pragmatic unit normalisation.

parse_line("1 1/2 cups all-purpose flour, sifted") ->
    ParsedIngredient(quantity=1.5, unit="cup", name="all-purpose flour", preparation="sifted")

Good enough for scaling and shopping-list merging; the raw line is always
kept so nothing is lost when the parse is wrong.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

VULGAR = {
    "½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75, "⅕": 0.2, "⅖": 0.4, "⅗": 0.6,
    "⅘": 0.8, "⅙": 1 / 6, "⅚": 5 / 6, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}

# canonical unit -> (family, factor to family base, synonyms)
UNITS: dict[str, tuple[str, float, list[str]]] = {
    "tsp": ("volume_us", 4.92892, ["tsp", "tsps", "teaspoon", "teaspoons", "t"]),
    "tbsp": ("volume_us", 14.7868, ["tbsp", "tbsps", "tbs", "tablespoon", "tablespoons", "T"]),
    "cup": ("volume_us", 236.588, ["cup", "cups", "c"]),
    "floz": ("volume_us", 29.5735, ["fl oz", "fl. oz", "fl.oz", "floz", "fluid ounce", "fluid ounces"]),
    "pint": ("volume_us", 473.176, ["pint", "pints", "pt"]),
    "quart": ("volume_us", 946.353, ["quart", "quarts", "qt"]),
    "gallon": ("volume_us", 3785.41, ["gallon", "gallons", "gal"]),
    "ml": ("volume_metric", 1.0, ["ml", "mL", "milliliter", "milliliters", "millilitre", "millilitres"]),
    "l": ("volume_metric", 1000.0, ["l", "L", "liter", "liters", "litre", "litres"]),
    "oz": ("mass_us", 28.3495, ["oz", "ounce", "ounces"]),
    "lb": ("mass_us", 453.592, ["lb", "lbs", "pound", "pounds"]),
    "g": ("mass_metric", 1.0, ["g", "gram", "grams", "gr"]),
    "kg": ("mass_metric", 1000.0, ["kg", "kilogram", "kilograms"]),
    "pinch": ("count", 1.0, ["pinch", "pinches"]),
    "dash": ("count", 1.0, ["dash", "dashes"]),
    "clove": ("count", 1.0, ["clove", "cloves"]),
    "can": ("count", 1.0, ["can", "cans"]),
    "slice": ("count", 1.0, ["slice", "slices"]),
    "bunch": ("count", 1.0, ["bunch", "bunches"]),
    "piece": ("count", 1.0, ["piece", "pieces", "pc", "pcs"]),
    "stick": ("count", 1.0, ["stick", "sticks"]),
    "sprig": ("count", 1.0, ["sprig", "sprigs"]),
    "handful": ("count", 1.0, ["handful", "handfuls"]),
    "package": ("count", 1.0, ["package", "packages", "pkg", "packet", "packets"]),
    "head": ("count", 1.0, ["head", "heads"]),
    "stalk": ("count", 1.0, ["stalk", "stalks", "rib", "ribs"]),
    "large": ("count", 1.0, ["large"]),
    "medium": ("count", 1.0, ["medium"]),
    "small": ("count", 1.0, ["small"]),
}
_SYN: dict[str, str] = {}
for _canon, (_fam, _f, _syns) in UNITS.items():
    for _s in _syns:
        _SYN[_s.lower()] = _canon
    _SYN[_canon] = _canon
# multi-word first so "fl oz" wins over "oz"
_UNIT_ALTS = sorted(_SYN.keys(), key=len, reverse=True)
_UNIT_RE = "|".join(re.escape(u) for u in _UNIT_ALTS)

_NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d*[.,]\d+|\d+|[½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])"
_QTY_RE = re.compile(
    rf"^\s*(?:about|approx\.?|approximately|roughly)?\s*"
    rf"(?P<q1>{_NUM})\s*(?P<vf>[½⅓⅔¼¾⅛⅜⅝⅞])?"
    rf"(?:\s*(?:-|–|—|to)\s*(?P<q2>{_NUM}))?\s*"
    rf"(?:(?P<unit>{_UNIT_RE})\b\.?)?\s*(?:of\s+)?(?P<rest>.*)$",
    re.IGNORECASE,
)
_PAREN = re.compile(r"\s*\([^)]*\)")
_STRIP_WORDS = {
    "fresh", "freshly", "large", "small", "medium", "chopped", "diced", "minced", "sliced",
    "ground", "finely", "roughly", "coarsely", "peeled", "grated", "shredded", "ripe", "whole",
    "raw", "cooked", "boneless", "skinless", "extra", "virgin", "cold", "warm", "hot", "room",
    "temperature", "softened", "melted", "divided", "plus", "more", "for", "serving", "optional",
}
_IRREGULAR = {"tomatoes": "tomato", "potatoes": "potato", "leaves": "leaf", "loaves": "loaf",
              "knives": "knife", "halves": "half", "cloves": "clove", "radishes": "radish"}
_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)?\s*(\d+(?:\.\d+)?)?\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.I)


def parse_number(tok: str) -> float | None:
    tok = tok.strip().replace(",", ".")
    if not tok:
        return None
    if tok in VULGAR:
        return VULGAR[tok]
    if len(tok) > 1 and tok[-1] in VULGAR:          # "1½"
        try:
            return float(tok[:-1]) + VULGAR[tok[-1]]
        except ValueError:
            return None
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", tok)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", tok)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(tok)
    except ValueError:
        return None


def canonical_unit(u: str | None) -> str | None:
    if not u:
        return None
    key = u.strip().rstrip(".").lower()
    if key == "t":
        return "tsp" if u.strip() == "t" else "tbsp"
    return _SYN.get(key)


def unit_family(unit: str | None) -> str:
    if unit and unit in UNITS:
        return UNITS[unit][0]
    return "count"


def to_family_base(qty: float, unit: str | None) -> float:
    if unit and unit in UNITS:
        return qty * UNITS[unit][1]
    return qty


def singularize(word: str) -> str:
    w = word.lower()
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ches", "shes", "sses", "xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def normalize_name(name: str) -> str:
    """Lowercase, drop parentheticals and preparation words, singularise the
    last word. Used as the merge key for shopping lists."""
    n = _PAREN.sub("", name or "").lower()
    n = n.split(",")[0]
    words = [w for w in re.findall(r"[a-z0-9'’-]+", n) if w not in _STRIP_WORDS]
    if not words:
        words = re.findall(r"[a-z0-9'’-]+", n) or [n.strip()]
    words[-1] = singularize(words[-1])
    return " ".join(words).strip()


@dataclass
class ParsedIngredient:
    raw: str
    quantity: float | None = None
    quantity_max: float | None = None
    unit: str | None = None
    unit_raw: str | None = None
    name: str = ""
    preparation: str | None = None
    optional: bool = False

    @property
    def name_norm(self) -> str:
        return normalize_name(self.name)


def parse_line(line: str) -> ParsedIngredient:
    raw = (line or "").strip()
    p = ParsedIngredient(raw=raw)
    txt = raw.lstrip("-•*·◦▪ ").strip()
    if re.search(r"\boptional\b", txt, re.I):
        p.optional = True
    m = _QTY_RE.match(txt)
    rest = txt
    if m and m.group("q1"):
        q = parse_number(m.group("q1"))
        if m.group("vf") and q is not None:
            q += VULGAR[m.group("vf")]
        p.quantity = q
        if m.group("q2"):
            p.quantity_max = parse_number(m.group("q2"))
        if m.group("unit"):
            p.unit_raw = m.group("unit")
            p.unit = canonical_unit(m.group("unit"))
        rest = m.group("rest") or ""
    # "flour, sifted" -> name / preparation
    name, _, prep = rest.partition(",")
    name = _PAREN.sub("", name).strip(" .;:")
    if not name:
        name = rest.strip() or txt
    p.name = name
    prep = prep.strip(" .;:")
    p.preparation = prep or None
    return p


def parse_ingredient_block(block: str) -> list[dict]:
    """One ingredient per line; a line ending in ':' starts a group."""
    out: list[dict] = []
    group: str | None = None
    for line in (block or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.endswith(":") and len(s) < 60:
            group = s[:-1].strip() or None
            continue
        pi = parse_line(s)
        out.append(
            {
                "group_name": group,
                "raw_text": pi.raw,
                "quantity": pi.quantity,
                "quantity_max": pi.quantity_max,
                "unit": pi.unit,
                "unit_raw": pi.unit_raw,
                "name": pi.name,
                "name_norm": pi.name_norm,
                "preparation": pi.preparation,
                "optional": 1 if pi.optional else 0,
            }
        )
    return out


def detect_minutes(text: str) -> int | None:
    """First duration mentioned in a step, in minutes (for cook-mode timers)."""
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    a = float(m.group(1))
    unit = m.group(3).lower()
    mins = a * 60 if unit.startswith("h") else a
    return int(round(mins)) or None


def parse_step_block(block: str) -> list[dict]:
    out: list[dict] = []
    group: str | None = None
    for line in (block or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.endswith(":") and len(s) < 60:
            group = s[:-1].strip() or None
            continue
        s = re.sub(r"^(?:step\s*)?\d+[.)]\s*", "", s, flags=re.I)
        out.append({"group_name": group, "text": s, "minutes": detect_minutes(s)})
    return out


def format_quantity(q: float | None, unit: str | None = None) -> str:
    """1.5 -> '1 ½'; metric units get one decimal."""
    if q is None:
        return ""
    fam = unit_family(unit)
    if fam.endswith("metric"):
        return f"{q:.1f}".rstrip("0").rstrip(".")
    fr = Fraction(q).limit_denominator(8)
    whole, rem = divmod(fr.numerator, fr.denominator)
    glyph = {
        Fraction(1, 2): "½", Fraction(1, 3): "⅓", Fraction(2, 3): "⅔", Fraction(1, 4): "¼",
        Fraction(3, 4): "¾", Fraction(1, 8): "⅛", Fraction(3, 8): "⅜", Fraction(5, 8): "⅝", Fraction(7, 8): "⅞",
    }
    part = Fraction(rem, fr.denominator)
    if part == 0:
        return str(whole)
    frac = glyph.get(part, f"{part.numerator}/{part.denominator}")
    return f"{whole} {frac}" if whole else frac


_PREP = re.compile(r"\bprep(?:aration)?(?:\s*time)?\s*[:\-]?\s*(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minutes?)\b", re.I)
_COOK = re.compile(r"\b(?:cook|cooking|bake|baking)(?:\s*time)?\s*[:\-]?\s*(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minutes?)\b", re.I)
_TOTAL = re.compile(r"\btotal(?:\s*time)?\s*[:\-]?\s*(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minutes?)\b", re.I)
_SERVES = re.compile(r"\b(?:serves|servings?|makes|yield[s]?)\s*[:\-]?\s*(\d+)\b", re.I)


def _mins(m) -> int | None:
    if not m:
        return None
    n = int(m.group(1))
    return n * 60 if m.group(2).lower().startswith("h") else n


def detect_times(text: str) -> dict:
    """Prep/cook/total minutes and servings from header lines like
    'Serves 4 · Prep 15 min · Cook 45 min'. Used when the extractor left them blank."""
    head = (text or "")[:6000]
    out = {"prep_min": _mins(_PREP.search(head)), "cook_min": _mins(_COOK.search(head)),
           "total_min": _mins(_TOTAL.search(head))}
    m = _SERVES.search(head)
    out["servings"] = float(m.group(1)) if m else None
    return out
