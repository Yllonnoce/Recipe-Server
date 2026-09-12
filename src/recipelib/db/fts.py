"""FTS5 index over recipes. One row per recipe (rowid = recipe id), rebuilt
whole whenever a recipe is saved; simpler than triggers spanning five tables."""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import Recipe, RecipeText


def reindex_recipe(s: Session, recipe_id: int) -> None:
    r = s.get(Recipe, recipe_id)
    s.execute(text("DELETE FROM recipes_fts WHERE rowid = :id"), {"id": recipe_id})
    if r is None or r.deleted_at:
        return
    body = ""
    rt = s.get(RecipeText, recipe_id)
    if rt is not None:
        body = rt.text[:200_000]
    s.execute(
        text(
            "INSERT INTO recipes_fts(rowid, title, description, ingredients, tags, body) "
            "VALUES (:id, :title, :desc, :ing, :tags, :body)"
        ),
        {
            "id": recipe_id,
            "title": r.title or "",
            "desc": (r.description or "") + "\n" + "\n".join(st.text for st in r.steps),
            "ing": "\n".join(i.raw_text for i in r.ingredients),
            "tags": " ".join(t.name for t in r.tags),
            "body": body,
        },
    )


def rebuild_all(s: Session) -> int:
    s.execute(text("DELETE FROM recipes_fts"))
    ids = [row[0] for row in s.execute(text("SELECT id FROM recipes WHERE deleted_at IS NULL"))]
    for rid in ids:
        reindex_recipe(s, rid)
    return len(ids)


_TOKEN = re.compile(r"[\w'’-]+", re.UNICODE)


def build_query(q: str) -> str:
    """Turn free text into a safe FTS5 query: each word becomes a quoted prefix term."""
    words = _TOKEN.findall(q or "")
    if not words:
        return ""
    return " ".join('"' + w.replace('"', "") + '"*' for w in words[:12])


def search_ids(s: Session, q: str, limit: int = 500) -> list[int]:
    fq = build_query(q)
    if not fq:
        return []
    rows = s.execute(
        text(
            "SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH :q "
            "ORDER BY bm25(recipes_fts, 10.0, 3.0, 5.0, 4.0, 1.0) LIMIT :lim"
        ),
        {"q": fq, "lim": limit},
    )
    return [r[0] for r in rows]
