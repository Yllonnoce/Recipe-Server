"""Weekly meal plan: entries per day and slot, either a recipe or a free note."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db.models import MealPlanEntry, Recipe

SLOTS = ["breakfast", "lunch", "dinner", "snack", "other"]


def week_start(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())


def parse_date(v: str | None) -> date | None:
    try:
        return date.fromisoformat((v or "").strip())
    except ValueError:
        return None


def week_entries(s: Session, start: date) -> dict[date, list[MealPlanEntry]]:
    end = start + timedelta(days=6)
    rows = s.scalars(
        select(MealPlanEntry).options(selectinload(MealPlanEntry.recipe))
        .where(MealPlanEntry.date >= start.isoformat(), MealPlanEntry.date <= end.isoformat())
        .order_by(MealPlanEntry.date, MealPlanEntry.position, MealPlanEntry.id)
    )
    out: dict[date, list[MealPlanEntry]] = {start + timedelta(days=i): [] for i in range(7)}
    slot_rank = {sl: i for i, sl in enumerate(SLOTS)}
    for e in rows:
        d = date.fromisoformat(e.date)
        if e.recipe is not None and e.recipe.deleted_at:
            continue
        out.setdefault(d, []).append(e)
    for d in out:
        out[d].sort(key=lambda e: (slot_rank.get(e.slot, 9), e.position, e.id))
    return out


def add_entry(s: Session, d: date, slot: str, recipe_id: int | None = None, note: str | None = None,
              servings: float | None = None) -> MealPlanEntry | None:
    slot = slot if slot in SLOTS else "dinner"
    r = s.get(Recipe, recipe_id) if recipe_id else None
    if r is None and not (note or "").strip():
        return None
    e = MealPlanEntry(date=d.isoformat(), slot=slot, recipe_id=r.id if r else None,
                      note=(note or "").strip() or None, servings_override=servings)
    s.add(e)
    s.flush()
    return e


def week_recipes(s: Session, start: date) -> list[tuple[Recipe, float | None]]:
    out = []
    for entries in week_entries(s, start).values():
        for e in entries:
            if e.recipe is not None:
                out.append((e.recipe, e.servings_override))
    return out
