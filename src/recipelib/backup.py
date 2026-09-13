"""Backups (zip of the database + assets) and restore/merge.

A backup zip holds `recipes.db` (a consistent SQLite snapshot), the `assets/`
tree, and `manifest.json`. Restore reads the backup database with plain
sqlite3 and inserts through the ORM, so an old backup is migrated to the
current schema first and a merge can match recipes the library already has
(same PDF fingerprint or same source URL).
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text

from . import __version__
from .config import get_settings
from .db import fts
from .db.engine import session_scope
from .db.models import (Asset, Bookmark, Ingredient, MealPlanEntry, Recipe, RecipeText, ShoppingItem,
                        ShoppingList, Step, Tag, utcnow)

log = logging.getLogger(__name__)
STATE = {"running": False, "last": None}


def backups_dir() -> Path:
    d = get_settings().library_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- create

def create_backup(out: Path | None = None, include_assets: bool = True) -> Path:
    cfg = get_settings()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out or (backups_dir() / f"recipelib-{stamp}.zip")
    tmpdir = Path(tempfile.mkdtemp(prefix="rlbackup-", dir=str(cfg.library_dir / "tmp")))
    snap = tmpdir / "recipes.db"
    src = sqlite3.connect(str(cfg.db_path))
    dst = sqlite3.connect(str(snap))
    src.backup(dst)
    dst.close()
    src.close()
    con = sqlite3.connect(str(snap))
    n_recipes = con.execute("SELECT COUNT(*) FROM recipes WHERE deleted_at IS NULL").fetchone()[0]
    schema = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    manifest = {"app": "recipelib", "version": __version__, "schema": schema, "created": utcnow(),
                "recipes": n_recipes, "assets": include_assets}
    part = out.with_suffix(".zip.part")
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "recipes.db")
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        if include_assets and cfg.assets_dir.exists():
            for f in sorted(cfg.assets_dir.rglob("*")):
                if f.is_file():
                    z.write(f, (Path("assets") / f.relative_to(cfg.assets_dir)).as_posix(), compress_type=zipfile.ZIP_STORED)
    part.replace(out)
    shutil.rmtree(tmpdir, ignore_errors=True)
    STATE["last"] = time.time()
    log.info("backup written: %s (%d recipes)", out, n_recipes)
    return out


def list_backups() -> list[dict]:
    out = []
    for f in sorted(backups_dir().glob("*.zip"), reverse=True):
        info = {"name": f.name, "path": f, "bytes": f.stat().st_size, "mtime": datetime.fromtimestamp(f.stat().st_mtime)}
        try:
            with zipfile.ZipFile(f) as z:
                m = json.loads(z.read("manifest.json"))
                info.update(recipes=m.get("recipes"), version=m.get("version"), created=m.get("created"))
        except Exception:  # noqa: BLE001
            info.update(recipes=None, version=None, created=None)
        out.append(info)
    return out


def prune(keep: int) -> int:
    files = sorted(backups_dir().glob("recipelib-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    gone = 0
    for f in files[keep:]:
        f.unlink(missing_ok=True)
        gone += 1
    return gone


# ---------------------------------------------------------------- restore / merge

@dataclass
class RestoreStats:
    added: int = 0
    skipped: int = 0
    replaced: int = 0
    assets_copied: int = 0
    plan_added: int = 0
    shopping_added: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"{self.added} recipe{'s' if self.added != 1 else ''} added"
        if self.replaced:
            s += f", {self.replaced} replaced"
        if self.skipped:
            s += f", {self.skipped} already present"
        if self.plan_added or self.shopping_added:
            s += f", {self.plan_added} plan entries and {self.shopping_added} shopping items merged"
        if self.errors:
            s += f"; {len(self.errors)} problem(s): " + "; ".join(self.errors[:3])
        return s


def _rows(con: sqlite3.Connection, sql: str, *args) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args)]


def restore(zip_path: Path, mode: str = "merge", overwrite: bool = False) -> RestoreStats:
    """mode: 'merge' (add what is missing) or 'replace' (wipe first).
    overwrite: in merge mode, replace local recipes that match backup ones."""
    cfg = get_settings()
    stats = RestoreStats()
    STATE["running"] = True
    tmpdir = Path(tempfile.mkdtemp(prefix="rlrestore-", dir=str(cfg.library_dir / "tmp")))
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = set(z.namelist())
            if "recipes.db" not in names:
                raise ValueError("not a Recipe Library backup (no recipes.db inside)")
            z.extract("recipes.db", tmpdir)
            # bring an older backup up to the current schema
            from .db.migrate import migrate
            migrate(tmpdir / "recipes.db")
            bcon = sqlite3.connect(str(tmpdir / "recipes.db"))
            # copy asset files (deduplicated by their sha-named paths)
            for n in names:
                if n.startswith("assets/") and not n.endswith("/"):
                    dest = cfg.assets_dir / n[len("assets/"):]
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(n) as src, dest.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                        stats.assets_copied += 1
        with session_scope() as s:
            if mode == "replace":
                _wipe(s)
            local_by_sha = {sha: rid for rid, sha in s.execute(text(
                "SELECT r.id, a.sha256 FROM recipes r JOIN assets a ON a.id = r.pdf_asset_id WHERE r.deleted_at IS NULL"))}
            local_by_url = {u: rid for rid, u in s.execute(text(
                "SELECT id, source_url_norm FROM recipes WHERE deleted_at IS NULL AND source_url_norm IS NOT NULL"))}
            id_map: dict[int, int] = {}
            for br in _rows(bcon, "SELECT * FROM recipes WHERE deleted_at IS NULL ORDER BY id"):
                try:
                    match = None
                    b_pdf = _rows(bcon, "SELECT * FROM assets WHERE id = ?", br["pdf_asset_id"])[0] if br["pdf_asset_id"] else None
                    if b_pdf and b_pdf["sha256"] in local_by_sha:
                        match = local_by_sha[b_pdf["sha256"]]
                    elif br["source_url_norm"] and br["source_url_norm"] in local_by_url:
                        match = local_by_url[br["source_url_norm"]]
                    if match is not None:
                        if not overwrite:
                            stats.skipped += 1
                            id_map[br["id"]] = match
                            continue
                        old = s.get(Recipe, match)
                        if old is not None:
                            s.delete(old)
                            s.flush()
                            fts.reindex_recipe(s, match)
                        stats.replaced += 1
                    new_id = _import_recipe(s, bcon, br, b_pdf)
                    id_map[br["id"]] = new_id
                    if b_pdf:
                        local_by_sha[b_pdf["sha256"]] = new_id
                    if br["source_url_norm"]:
                        local_by_url[br["source_url_norm"]] = new_id
                    if match is None:
                        stats.added += 1
                except Exception as e:  # noqa: BLE001
                    log.exception("restore: recipe %s failed", br.get("title"))
                    stats.errors.append(f"{br.get('title')}: {type(e).__name__}")
            stats.plan_added = _import_plan(s, bcon, id_map)
            stats.shopping_added = _import_shopping(s, bcon)
        bcon.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        STATE["running"] = False
    log.info("restore from %s (%s): %s", zip_path.name, mode, stats.summary())
    return stats


def _wipe(s) -> None:
    for tbl in ("recipe_tags", "ingredients", "steps", "recipe_bookmarks", "recipe_text", "meal_plan_entries",
                "shopping_items", "shopping_lists", "capture_jobs", "recipes", "tags"):
        s.execute(text(f"DELETE FROM {tbl}"))
    s.execute(text("DELETE FROM recipes_fts"))
    s.flush()


def _asset(s, cfg, row: dict | None) -> Asset | None:
    if not row:
        return None
    a = s.scalar(select(Asset).where(Asset.sha256 == row["sha256"], Asset.kind == row["kind"]))
    if a is None:
        a = Asset(kind=row["kind"], rel_path=row["rel_path"], sha256=row["sha256"], bytes=row["bytes"], mime=row["mime"],
                  page_count=row.get("page_count"), width=row.get("width"), height=row.get("height"))
        s.add(a)
        s.flush()
    return a


def _import_recipe(s, bcon: sqlite3.Connection, br: dict, b_pdf: dict | None) -> int:
    cfg = get_settings()
    cols = {k: br[k] for k in ("title", "description", "source_url", "source_url_norm", "source_name", "yield_text", "servings",
                               "prep_min", "cook_min", "total_min", "status", "extraction_method", "confidence", "favorite",
                               "notes", "language", "last_page", "page_count")}
    r = Recipe(**cols, created_at=br.get("created_at") or utcnow(), updated_at=br.get("updated_at") or utcnow())
    for fld in ("pdf_asset_id", "cover_asset_id", "thumb_asset_id"):
        arow = _rows(bcon, "SELECT * FROM assets WHERE id = ?", br[fld])[0] if br.get(fld) else None
        a = _asset(s, cfg, arow)
        setattr(r, fld, a.id if a else None)
    s.add(r)
    s.flush()
    for ing in _rows(bcon, "SELECT * FROM ingredients WHERE recipe_id = ? ORDER BY position", br["id"]):
        ing.pop("id"); ing.pop("recipe_id")
        r.ingredients.append(Ingredient(**ing))
    for st in _rows(bcon, "SELECT * FROM steps WHERE recipe_id = ? ORDER BY position", br["id"]):
        st.pop("id"); st.pop("recipe_id")
        r.steps.append(Step(**st))
    for bm in _rows(bcon, "SELECT * FROM recipe_bookmarks WHERE recipe_id = ? ORDER BY page", br["id"]):
        r.bookmarks.append(Bookmark(page=bm["page"], label=bm["label"], created_at=bm.get("created_at") or utcnow()))
    rt = _rows(bcon, "SELECT * FROM recipe_text WHERE recipe_id = ?", br["id"])
    if rt:
        r.text = RecipeText(text=rt[0]["text"], text_source=rt[0]["text_source"], page_texts=rt[0]["page_texts"])
    for tg in _rows(bcon, "SELECT t.name, t.kind FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id = ?", br["id"]):
        t = s.scalar(select(Tag).where(Tag.kind == tg["kind"], Tag.name == tg["name"]))
        if t is None:
            t = Tag(name=tg["name"], kind=tg["kind"])
            s.add(t)
            s.flush()
        if t not in r.tags:
            r.tags.append(t)
    s.flush()
    fts.reindex_recipe(s, r.id)
    return r.id


def _import_plan(s, bcon, id_map: dict[int, int]) -> int:
    n = 0
    existing = {(e.date, e.slot, e.recipe_id, e.note) for e in s.scalars(select(MealPlanEntry))}
    for e in _rows(bcon, "SELECT * FROM meal_plan_entries ORDER BY date, position"):
        rid = id_map.get(e["recipe_id"]) if e["recipe_id"] else None
        if e["recipe_id"] and rid is None:
            continue
        key = (e["date"], e["slot"], rid, e["note"])
        if key in existing:
            continue
        s.add(MealPlanEntry(date=e["date"], slot=e["slot"], recipe_id=rid, note=e["note"],
                            servings_override=e["servings_override"], position=e["position"]))
        existing.add(key)
        n += 1
    return n


def _import_shopping(s, bcon) -> int:
    from .domain import shopping as S
    lst = S.default_list(s)
    have = {it.name_norm for it in lst.items}
    n = 0
    for it in _rows(bcon, "SELECT si.* FROM shopping_items si ORDER BY si.position"):
        if it["name_norm"] in have or it["checked"]:
            continue
        S.add_ingredient(s, lst, name=it["name"], name_norm=it["name_norm"], quantity=it["quantity"], unit=it["unit"],
                         manual=bool(it["manual"]))
        have.add(it["name_norm"])
        n += 1
    return n


# ---------------------------------------------------------------- scheduler

class AutoBackup:
    """Daily backup (configurable) keeping the newest N zips."""

    def __init__(self, every_days: float, keep: int):
        self.every = every_days * 86400
        self.keep = keep
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def start(self) -> None:
        if self.every <= 0:
            return
        self._t = threading.Thread(target=self._run, name="auto-backup", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def _latest(self) -> float:
        files = list(backups_dir().glob("recipelib-*.zip"))
        return max((f.stat().st_mtime for f in files), default=0.0)

    def _run(self) -> None:
        self._stop.wait(120)
        while not self._stop.is_set():
            try:
                if time.time() - self._latest() >= self.every:
                    create_backup()
                    prune(self.keep)
            except Exception:  # noqa: BLE001
                log.exception("automatic backup failed")
            self._stop.wait(3600)
