"""ORM mirror of db/migrations/*.sql (the SQL files are the source of truth)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Base(DeclarativeBase):
    pass


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    rel_path: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    bytes: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String)
    page_count: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)


class Recipe(Base):
    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String)
    source_url_norm: Mapped[str | None] = mapped_column(String)
    source_name: Mapped[str | None] = mapped_column(String)
    yield_text: Mapped[str | None] = mapped_column(String)
    servings: Mapped[float | None] = mapped_column(Float)
    prep_min: Mapped[int | None] = mapped_column(Integer)
    cook_min: Mapped[int | None] = mapped_column(Integer)
    total_min: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="processing")
    extraction_method: Mapped[str] = mapped_column(String, default="none")
    confidence: Mapped[float | None] = mapped_column(Float)
    pdf_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    cover_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    thumb_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    favorite: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String)
    last_page: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    updated_at: Mapped[str] = mapped_column(String, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[str | None] = mapped_column(String)

    ingredients: Mapped[list[Ingredient]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="Ingredient.position"
    )
    steps: Mapped[list[Step]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="Step.position"
    )
    tags: Mapped[list[Tag]] = relationship(secondary="recipe_tags", back_populates="recipes")
    bookmarks: Mapped[list[Bookmark]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="Bookmark.page"
    )
    text: Mapped[RecipeText | None] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", uselist=False
    )
    pdf_asset: Mapped[Asset | None] = relationship(foreign_keys=[pdf_asset_id])
    cover_asset: Mapped[Asset | None] = relationship(foreign_keys=[cover_asset_id])
    thumb_asset: Mapped[Asset | None] = relationship(foreign_keys=[thumb_asset_id])

    def tags_of(self, kind: str) -> list[Tag]:
        return [t for t in self.tags if t.kind == kind]

    def ingredient_groups(self) -> list[tuple[str | None, list[Ingredient]]]:
        return _group(self.ingredients)

    def step_groups(self) -> list[tuple[str | None, list[Step]]]:
        return _group(self.steps)


def _group(items):
    out: list[tuple[str | None, list]] = []
    for it in items:
        if not out or out[-1][0] != it.group_name:
            out.append((it.group_name, []))
        out[-1][1].append(it)
    return out


class Ingredient(Base):
    __tablename__ = "ingredients"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    group_name: Mapped[str | None] = mapped_column(String)
    raw_text: Mapped[str] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Float)
    quantity_max: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String)
    unit_raw: Mapped[str | None] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    name_norm: Mapped[str] = mapped_column(String)
    preparation: Mapped[str | None] = mapped_column(String)
    optional: Mapped[int] = mapped_column(Integer, default=0)
    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")


class Step(Base):
    __tablename__ = "steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    group_name: Mapped[str | None] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    minutes: Mapped[int | None] = mapped_column(Integer)
    recipe: Mapped[Recipe] = relationship(back_populates="steps")


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="custom")
    recipes: Mapped[list[Recipe]] = relationship(secondary="recipe_tags", back_populates="tags")


class RecipeTag(Base):
    __tablename__ = "recipe_tags"
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class RecipeText(Base):
    __tablename__ = "recipe_text"
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    text_source: Mapped[str] = mapped_column(String)
    page_texts: Mapped[str] = mapped_column(Text, default="[]")
    recipe: Mapped[Recipe] = relationship(back_populates="text")

    @property
    def pages(self) -> list[str]:
        try:
            return json.loads(self.page_texts)
        except ValueError:
            return []


class Bookmark(Base):
    __tablename__ = "recipe_bookmarks"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    page: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    recipe: Mapped[Recipe] = relationship(back_populates="bookmarks")

    def view(self) -> dict:
        return {"id": self.id, "page": self.page, "label": self.label}


class CaptureJob(Base):
    __tablename__ = "capture_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="queued")
    stage: Mapped[str | None] = mapped_column(String)
    stage_log: Mapped[str] = mapped_column(Text, default="[]")
    input_url: Mapped[str | None] = mapped_column(String)
    input_path: Mapped[str | None] = mapped_column(String)
    title_hint: Mapped[str | None] = mapped_column(String)
    ipp_job_id: Mapped[int | None] = mapped_column(Integer)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id", ondelete="SET NULL"))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("recipes.id", ondelete="SET NULL"))
    force: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[str | None] = mapped_column(String)
    last_error: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)

    def log(self, stage: str, msg: str = "") -> None:
        try:
            entries = json.loads(self.stage_log or "[]")
        except ValueError:
            entries = []
        entries.append({"t": utcnow(), "stage": stage, "msg": msg})
        self.stage_log = json.dumps(entries[-60:])
        self.stage = stage

    @property
    def log_entries(self) -> list[dict]:
        try:
            return json.loads(self.stage_log or "[]")
        except ValueError:
            return []


class ShoppingList(Base):
    __tablename__ = "shopping_lists"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    items: Mapped[list[ShoppingItem]] = relationship(
        back_populates="list", cascade="all, delete-orphan", order_by="ShoppingItem.position"
    )


class ShoppingItem(Base):
    __tablename__ = "shopping_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("shopping_lists.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String)
    name_norm: Mapped[str] = mapped_column(String)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String)
    checked: Mapped[int] = mapped_column(Integer, default=0)
    manual: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str | None] = mapped_column(String)
    sources: Mapped[str] = mapped_column(Text, default="[]")
    list: Mapped[ShoppingList] = relationship(back_populates="items")


class MealPlanEntry(Base):
    __tablename__ = "meal_plan_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String)
    slot: Mapped[str] = mapped_column(String)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    note: Mapped[str | None] = mapped_column(String)
    servings_override: Mapped[float | None] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer, default=0)
    recipe: Mapped[Recipe | None] = relationship()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
