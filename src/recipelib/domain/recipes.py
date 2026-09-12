"""Recipe CRUD and queries. All writes to a recipe go through here so the
FTS index is always rebuilt."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import fts
from ..db.models import Bookmark, Ingredient, Recipe, RecipeText, Step, Tag, utcnow
from ..extract.normalize import parse_ingredient_block, parse_step_block

TAG_KINDS = ("category", "course", "cuisine", "source", "custom")


def get(s: Session, recipe_id: int) -> Recipe | None:
    r = s.get(Recipe, recipe_id)
    if r is None or r.deleted_at:
        return None
    return r


@dataclass
class ListFilter:
    q: str = ""
    tag: str | None = None          # "kind:name"
    favorites: bool = False
    status: str | None = None       # processing | needs_review | ready | failed
    sort: str = "newest"            # newest | title | favorites
    page: int = 1
    per_page: int = 48
    ids: list[int] = field(default_factory=list)


def list_recipes(s: Session, f: ListFilter) -> tuple[list[Recipe], int]:
    stmt = select(Recipe).where(Recipe.deleted_at.is_(None))
    order_ids: list[int] | None = None
    if f.q.strip():
        order_ids = fts.search_ids(s, f.q)
        if not order_ids:
            return [], 0
        stmt = stmt.where(Recipe.id.in_(order_ids))
    if f.tag and ":" in f.tag:
        kind, name = f.tag.split(":", 1)
        stmt = stmt.where(Recipe.tags.any((Tag.kind == kind) & (Tag.name == name)))
    if f.favorites:
        stmt = stmt.where(Recipe.favorite == 1)
    if f.status:
        stmt = stmt.where(Recipe.status == f.status)
    total = s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    if order_ids is None:
        if f.sort == "title":
            stmt = stmt.order_by(func.lower(Recipe.title))
        elif f.sort == "favorites":
            stmt = stmt.order_by(Recipe.favorite.desc(), Recipe.updated_at.desc())
        else:
            stmt = stmt.order_by(Recipe.created_at.desc(), Recipe.id.desc())
        stmt = stmt.offset((f.page - 1) * f.per_page).limit(f.per_page)
        rows = list(s.scalars(stmt.options(selectinload(Recipe.tags))))
    else:
        rows = list(s.scalars(stmt.options(selectinload(Recipe.tags))))
        rank = {rid: i for i, rid in enumerate(order_ids)}
        rows.sort(key=lambda r: rank.get(r.id, 1 << 30))
        rows = rows[(f.page - 1) * f.per_page: f.page * f.per_page]
    return rows, total


def tag_counts(s: Session) -> dict[str, list[tuple[str, int]]]:
    rows = s.execute(
        select(Tag.kind, Tag.name, func.count(Recipe.id))
        .join(Tag.recipes)
        .where(Recipe.deleted_at.is_(None))
        .group_by(Tag.kind, Tag.name)
        .order_by(Tag.kind, func.lower(Tag.name))
    )
    out: dict[str, list[tuple[str, int]]] = {k: [] for k in TAG_KINDS}
    for kind, name, n in rows:
        out.setdefault(kind, []).append((name, n))
    return out


def status_counts(s: Session) -> dict[str, int]:
    rows = s.execute(
        select(Recipe.status, func.count()).where(Recipe.deleted_at.is_(None)).group_by(Recipe.status)
    )
    return {k: v for k, v in rows}


def get_or_create_tag(s: Session, name: str, kind: str = "custom") -> Tag:
    name = re.sub(r"\s+", " ", (name or "").strip()).strip("#, ")[:60]
    if not name:
        raise ValueError("empty tag")
    kind = kind if kind in TAG_KINDS else "custom"
    if kind in ("course", "cuisine"):
        name = name.lower()
    if kind == "category":
        from .categories import normalize
        name = normalize(name) or name
    t = s.scalar(select(Tag).where(Tag.kind == kind, func.lower(Tag.name) == name.lower()))
    if t is None:
        t = Tag(name=name, kind=kind)
        s.add(t)
        s.flush()
    return t


def set_tags(s: Session, r: Recipe, kind: str, names: list[str]) -> None:
    keep = [t for t in r.tags if t.kind != kind]
    seen: set[str] = set()
    for n in names:
        n = (n or "").strip()
        if not n or n.lower() in seen:
            continue
        seen.add(n.lower())
        keep.append(get_or_create_tag(s, n, kind))
    r.tags = keep


def add_tag(s: Session, r: Recipe, name: str, kind: str = "custom") -> None:
    t = get_or_create_tag(s, name, kind)
    if t not in r.tags:
        r.tags.append(t)
    touch(s, r)


def remove_tag(s: Session, r: Recipe, tag_id: int) -> None:
    r.tags = [t for t in r.tags if t.id != tag_id]
    touch(s, r)


def touch(s: Session, r: Recipe) -> None:
    r.updated_at = utcnow()
    s.flush()
    fts.reindex_recipe(s, r.id)


def set_favorite(s: Session, r: Recipe, on: bool) -> None:
    r.favorite = 1 if on else 0
    r.updated_at = utcnow()


def soft_delete(s: Session, r: Recipe) -> None:
    r.deleted_at = utcnow()
    s.flush()
    fts.reindex_recipe(s, r.id)


def replace_ingredients(s: Session, r: Recipe, rows: list[dict]) -> None:
    r.ingredients = [Ingredient(position=i, **row) for i, row in enumerate(rows)]


def replace_steps(s: Session, r: Recipe, rows: list[dict]) -> None:
    r.steps = [Step(position=i, **row) for i, row in enumerate(rows)]


def ingredients_as_text(r: Recipe) -> str:
    out = []
    for group, items in r.ingredient_groups():
        if group:
            out.append(f"{group}:")
        out.extend(i.raw_text for i in items)
    return "\n".join(out)


def steps_as_text(r: Recipe) -> str:
    out = []
    for group, items in r.step_groups():
        if group:
            out.append(f"{group}:")
        out.extend(st.text for st in items)
    return "\n".join(out)


def _int_or_none(v) -> int | None:
    try:
        v = str(v).strip()
        return int(float(v)) if v else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v) -> float | None:
    try:
        v = str(v).strip().replace(",", ".")
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def apply_edit_form(s: Session, r: Recipe, form: dict) -> None:
    """Save the edit page. Ingredients/steps arrive as one-per-line text."""
    r.title = (form.get("title") or r.title or "Untitled").strip()[:200]
    r.description = (form.get("description") or "").strip() or None
    r.source_url = (form.get("source_url") or "").strip() or None
    r.source_name = (form.get("source_name") or "").strip() or None
    r.yield_text = (form.get("yield_text") or "").strip() or None
    r.servings = _float_or_none(form.get("servings"))
    r.prep_min = _int_or_none(form.get("prep_min"))
    r.cook_min = _int_or_none(form.get("cook_min"))
    r.total_min = _int_or_none(form.get("total_min"))
    if r.total_min is None and (r.prep_min or r.cook_min):
        r.total_min = (r.prep_min or 0) + (r.cook_min or 0)
    r.notes = (form.get("notes") or "").strip() or None
    replace_ingredients(s, r, parse_ingredient_block(form.get("ingredients") or ""))
    replace_steps(s, r, parse_step_block(form.get("steps") or ""))
    if "categories" in form or form.get("_categories_present"):
        set_tags(s, r, "category", _split_tags(form.get("categories")))
    set_tags(s, r, "course", _split_tags(form.get("course")))
    set_tags(s, r, "cuisine", _split_tags(form.get("cuisine")))
    set_tags(s, r, "custom", _split_tags(form.get("tags")))
    from ..capture.dedup import normalize_url
    r.source_url_norm = normalize_url(r.source_url)
    r.extraction_method = "manual"
    if r.status in ("needs_review", "failed", "processing") and r.ingredients and r.steps:
        r.status = "ready"
    elif r.status == "processing":
        r.status = "needs_review"
    touch(s, r)


def _split_tags(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [x.strip() for x in re.split(r"[,\n;]", str(v)) if x.strip()]


def set_text(s: Session, r: Recipe, page_texts: list[str], source: str) -> None:
    body = "\n\n".join(page_texts)
    if r.text is None:
        r.text = RecipeText(text=body, text_source=source, page_texts=json.dumps(page_texts))
    else:
        r.text.text = body
        r.text.text_source = source
        r.text.page_texts = json.dumps(page_texts)


def assign_categories(s: Session, r: Recipe, extra: list[str] = (), replace: bool = False) -> list[str]:
    """Auto-categorise from title, ingredients and course tags. Keeps
    hand-picked categories unless replace=True."""
    from .categories import suggest
    names = suggest(r.title, [i.name for i in r.ingredients], [t.name for t in r.tags_of("course")], list(extra))
    if not replace:
        names = [t.name for t in r.tags_of("category")] + [n for n in names if n not in {t.name for t in r.tags_of("category")}]
    set_tags(s, r, "category", names)
    return names


def categorize_all(s: Session, replace: bool = False) -> int:
    n = 0
    for r in s.scalars(select(Recipe).where(Recipe.deleted_at.is_(None))):
        if r.ingredients or r.steps or r.title:
            assign_categories(s, r, replace=replace)
            fts.reindex_recipe(s, r.id)
            n += 1
    return n


# ---- images ----------------------------------------------------------------

def store_image(s: Session, png: bytes, kind: str):
    """Save PNG bytes under assets/<kind>/<sha>.png (deduplicated) and return the Asset."""
    from pathlib import Path

    import pymupdf

    from ..capture.pdf import sha256_bytes
    from ..config import get_settings
    from ..db.models import Asset
    cfg = get_settings()
    sha = sha256_bytes(png)
    a = s.scalar(select(Asset).where(Asset.sha256 == sha, Asset.kind == kind))
    if a is not None:
        return a
    rel = Path(kind) / sha[:2] / f"{sha}.png"
    dest = cfg.assets_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png)
    pix = pymupdf.Pixmap(png)
    a = Asset(kind=kind, rel_path=rel.as_posix(), sha256=sha, bytes=len(png), mime="image/png",
              width=pix.width, height=pix.height)
    s.add(a)
    s.flush()
    return a


def set_cover_from_png(s: Session, r: Recipe, png: bytes | None) -> None:
    """png=None clears the cover (the page thumbnail is shown instead)."""
    if png is None:
        r.cover_asset_id = None
    else:
        from ..capture.pdf import COVER_WIDTH, resize_png
        png, _w, _h = resize_png(png, COVER_WIDTH)
        r.cover_asset_id = store_image(s, png, "cover").id
    r.updated_at = utcnow()


# ---- reader state ----------------------------------------------------------

def clamp_page(r: Recipe, value) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError):
        return 0
    if p < 1:
        return 0
    if r.page_count:
        p = min(p, r.page_count)
    return p


def bookmark_views(r: Recipe) -> list[dict]:
    return [b.view() for b in sorted(r.bookmarks, key=lambda b: b.page)]


def clean_label(value, default: str) -> str:
    v = re.sub(r"\s+", " ", str(value or "")).strip()
    return v[:80] or default


def upsert_bookmark(s: Session, r: Recipe, page: int, label) -> tuple[Bookmark, bool]:
    existing = next((b for b in r.bookmarks if b.page == page), None)
    if existing is None:
        bm = Bookmark(page=page, label=clean_label(label, f"Page {page}"))
        r.bookmarks.append(bm)
        s.flush()
        return bm, True
    if label is not None:
        existing.label = clean_label(label, existing.label)
    return existing, False
