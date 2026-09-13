"""Clean-ups applied to the extractor's output before it is stored.

Small local models are good at the content and sloppy about structure: they
paste a heading together with its whole block as one "ingredient", repeat a
heading as an item, or label the ingredient sections but not the steps.
These fixes are deterministic and keep the document's own order.
"""
from __future__ import annotations

import re

_HEADING_START = re.compile(r"^(for the|for|make the|to make|to assemble|assemble|prepare|preparing|making|"
                            r"cook the|cooking|to serve|serving|topping|garnish|filling|sauce|dough|stock|broth|"
                            r"marinade|dressing|crust|frosting|icing|glaze)\b", re.I)
_QTY_START = re.compile(r"^\s*(\d|[½⅓⅔¼¾⅛⅜⅝⅞]|a pinch|pinch|salt|pepper|to taste)", re.I)
_SENTENCE_END = re.compile(r"[.!?]\s*$")


def looks_like_heading(line: str) -> bool:
    s = line.strip().rstrip(":").strip()
    if not s or len(s) > 48 or len(s.split()) > 7:
        return False
    if _QTY_START.match(s) or _SENTENCE_END.search(line.strip()) and not line.strip().endswith(":"):
        return False
    return bool(line.strip().endswith(":") or _HEADING_START.match(s) or (s[:1].isupper() and len(s.split()) <= 4 and not re.search(r"\d", s)))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def clean_ingredients(items: list[dict]) -> list[dict]:
    """Split multi-line 'raw' blobs into lines, turning heading lines into the
    group for what follows; drop entries that are just a heading; de-duplicate."""
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        raw = (it.get("raw") or "").strip()
        group = (it.get("group") or "").strip() or None
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if len(lines) > 1:
            g = group
            for ln in lines:
                if looks_like_heading(ln) and not _QTY_START.match(ln):
                    g = ln.rstrip(":").strip()
                    continue
                key = _norm(ln)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"group": g, "raw": ln, "name": ln, "quantity": None, "quantity_max": None,
                            "unit": None, "preparation": None, "optional": False})
            continue
        if not raw:
            continue
        if group and _norm(raw) == _norm(group):
            continue                                 # the heading repeated as an item
        if looks_like_heading(raw) and not _QTY_START.match(raw) and it.get("quantity") in (None, 0):
            continue                                 # a heading that arrived as an ingredient
        key = _norm(raw)
        if key in seen:
            # keep the richer (parsed) version over a bare line added from a blob
            idx = next((i for i, o in enumerate(out) if _norm(o["raw"]) == key), None)
            if idx is not None and out[idx].get("quantity") is None and it.get("quantity") is not None:
                out[idx] = dict(it, group=out[idx].get("group") or group)
            continue
        seen.add(key)
        out.append(dict(it, group=group))
    return out


def infer_step_groups(steps: list[dict], text: str, ingredient_groups: list[str]) -> list[dict]:
    """When the steps came back without sections but the text has step
    headings ('Make the stock', 'For the chowder'), assign each step the
    nearest heading above it. Steps before the first heading stay ungrouped."""
    if not steps or any(st.get("group") for st in steps):
        return steps
    lines = [ln.strip() for ln in (text or "").splitlines()]
    # candidate headings: heading-looking lines, or lines matching an ingredient group name
    gnorm = {_norm(g) for g in ingredient_groups if g}
    gnorm |= {re.sub(r"^(for the|for) ", "", g) for g in list(gnorm)}
    heads: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        n = _norm(ln.rstrip(":"))
        if not n:
            continue
        if n in gnorm or re.sub(r"^(for the|for|make the|to make|making) ", "", n) in gnorm or looks_like_heading(ln):
            if not _QTY_START.match(ln):
                heads.append((i, ln.rstrip(":").strip()))
    if not heads:
        return steps
    # locate each step in the text by its first words
    positions: list[int | None] = []
    for st in steps:
        key = _norm(st.get("text", ""))[:40]
        pos = None
        for i, ln in enumerate(lines):
            if key and _norm(re.sub(r"^\s*(step\s*)?\d+[.)]\s*", "", ln))[:40] == key:
                pos = i
                break
        positions.append(pos)
    if all(p is None for p in positions):
        return steps
    # only headings that sit in the steps region count (after the first located step's heading area)
    first = min(p for p in positions if p is not None)
    region_heads = [(i, h) for i, h in heads if i >= max(0, first - 6)]
    if not region_heads:
        return steps
    assigned = 0
    for st, pos in zip(steps, positions):
        if pos is None:
            continue
        prev = [h for i, h in region_heads if i < pos]
        if prev:
            st["group"] = prev[-1]
            assigned += 1
    if assigned == 0:
        return steps
    # steps we could not locate inherit the group of their neighbour
    last = None
    for st in steps:
        if st.get("group"):
            last = st["group"]
        elif last:
            st["group"] = last
    # a single section covering everything is not a section
    if len({st.get("group") for st in steps}) == 1:
        for st in steps:
            st["group"] = None
    return steps


def infer_ingredient_groups(items: list[dict], text: str) -> list[dict]:
    """When the ingredients came back without sections, read the headings that
    sit between ingredient lines in the original text ('For the stock',
    'For the chowder') and assign them."""
    if not items or any(it.get("group") for it in items):
        return items
    lines = [ln.strip() for ln in (text or "").splitlines()]
    positions: list[int | None] = []
    for it in items:
        key = _norm(it.get("raw", ""))[:40]
        pos = next((i for i, ln in enumerate(lines) if key and _norm(ln)[:40] == key), None)
        positions.append(pos)
    located = [p for p in positions if p is not None]
    if len(located) < 2:
        return items
    lo, hi = max(0, min(located) - 3), max(located)
    heads = [(i, ln.rstrip(":").strip()) for i, ln in enumerate(lines[lo:hi + 1], start=lo)
             if ln and looks_like_heading(ln) and not _QTY_START.match(ln) and _norm(ln) not in {_norm(it.get("raw", "")) for it in items}]
    if not heads:
        return items
    for it, pos in zip(items, positions):
        if pos is None:
            continue
        prev = [h for i, h in heads if i < pos]
        if prev:
            it["group"] = prev[-1]
    last = None
    for it in items:
        if it.get("group"):
            last = it["group"]
        elif last:
            it["group"] = last
    if len({it.get("group") for it in items}) == 1:
        for it in items:
            it["group"] = None
    return items
