"""The ingest state machine shared by every capture source.

Every source ends up as "a PDF on disk + optional URL + optional structured
draft". Stages run in order, each idempotent so a job can resume after a
crash or a deferred LLM stage. The recipe row is created as early as possible
so a card shows up in the library while the slow stages run.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import fts
from ..db.engine import session_scope
from ..db.models import Asset, CaptureJob, Recipe, utcnow
from ..domain import recipes as R
from ..extract.llm import LLMUnavailable
from ..extract.normalize import detect_minutes, parse_line
from . import pdf as pdfops
from .dedup import normalize_url, site_name

log = logging.getLogger(__name__)


class JobCanceled(Exception):
    pass


class InputGone(Exception):
    """The staged upload/print file no longer exists (restart or cleanup ate it)."""


@dataclass
class Ctx:
    job_id: int
    source: str
    pdf_path: Path | None
    url: str | None
    title_hint: str | None
    force: bool
    recipe_id: int | None = None
    asset_id: int | None = None
    page_texts: list[str] = field(default_factory=list)
    text_source: str = "layer"
    draft: dict | None = None          # structured recipe from scraper/LLM
    html_text: str | None = None
    cover_png: bytes | None = None
    queue: object = None


STAGES = ["fetch", "raster_to_pdf", "shrink", "hash_dedup", "store", "text", "ocr", "thumbnail",
          "structured", "llm", "normalize", "index"]


def run_job(job_id: int, queue) -> None:
    with session_scope() as s:
        j = s.get(CaptureJob, job_id)
        if j is None:
            return
        ctx = Ctx(job_id=job_id, source=j.source, pdf_path=Path(j.input_path) if j.input_path else None,
                  url=j.input_url, title_hint=j.title_hint, force=bool(j.force),
                  recipe_id=j.recipe_id, asset_id=j.asset_id, queue=queue)
        done_stages = {e["stage"] for e in j.log_entries if e.get("msg") == "ok"}
    if ctx.pdf_path is not None and not ctx.pdf_path.exists() and ctx.recipe_id is None:
        raise InputGone(f"the {ctx.source} file is no longer on disk ({ctx.pdf_path.name}); please add it again")
    for stage in STAGES:
        if stage in done_stages and stage not in ("llm", "normalize", "index"):
            continue
        fn = globals()[f"stage_{stage}"]
        try:
            skipped = fn(ctx)
        except LLMUnavailable:
            raise
        except JobCanceled:
            return
        _mark(ctx, stage, "skipped" if skipped else "ok")
        if skipped == "duplicate":
            break
    with session_scope() as s:
        j = s.get(CaptureJob, job_id)
        if j is not None:
            j.state = "done"
            j.finished_at = utcnow()
            j.recipe_id = ctx.recipe_id
            j.asset_id = ctx.asset_id
            j.log("done", "ok")


def _mark(ctx: Ctx, stage: str, msg: str) -> None:
    with session_scope() as s:
        j = s.get(CaptureJob, ctx.job_id)
        if j is None:
            raise JobCanceled()
        if j.state == "canceled":
            raise JobCanceled()
        j.log(stage, msg)
        j.recipe_id = ctx.recipe_id
        j.asset_id = ctx.asset_id


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def stage_fetch(ctx: Ctx):
    if ctx.source != "url" or ctx.pdf_path is not None:
        return True
    from ..capture.sources import url as urlsrc
    result = urlsrc.fetch_to_pdf(ctx.url)
    ctx.pdf_path = result.pdf_path
    ctx.html_text = result.body_text
    ctx.draft = result.draft
    ctx.cover_png = result.cover_png
    if result.title and not ctx.title_hint:
        ctx.title_hint = result.title
    return False


def stage_raster_to_pdf(ctx: Ctx):
    """Printer jobs may arrive as PWG/URF raster; convert to a real PDF."""
    if ctx.pdf_path is None or pdfops.is_pdf(ctx.pdf_path):
        return True
    from ..ipp.raster import to_pdf
    out = ctx.pdf_path.with_suffix(".pdf")
    to_pdf.convert_file(ctx.pdf_path, out)
    ctx.pdf_path = out
    return False


def stage_shrink(ctx: Ctx):
    """Downsample images inside a freshly captured PDF (never an already stored asset)."""
    cfg = get_settings()
    if not cfg.shrink_files or ctx.pdf_path is None or ctx.recipe_id is not None:
        return True
    if str(cfg.assets_dir) in str(ctx.pdf_path):
        return True
    from . import shrink
    before, after = shrink.shrink_pdf(ctx.pdf_path)
    if after == before:
        return True
    _mark(ctx, "shrink", f"{before // 1024} KB -> {after // 1024} KB")
    return False


def stage_hash_dedup(ctx: Ctx):
    if ctx.pdf_path is None or not ctx.pdf_path.is_file():
        raise FileNotFoundError(f"input PDF missing: {ctx.pdf_path}")
    sha = pdfops.sha256_file(ctx.pdf_path)
    norm = normalize_url(ctx.url)
    with session_scope() as s:
        a = s.scalar(select(Asset).where(Asset.sha256 == sha, Asset.kind == "pdf"))
        if a is not None and not ctx.force:
            r = s.scalar(select(Recipe).where(Recipe.pdf_asset_id == a.id, Recipe.deleted_at.is_(None)))
            if r is not None:
                return _duplicate(ctx, s, r, "same PDF already in the library")
        if norm and not ctx.force:
            r = s.scalar(select(Recipe).where(Recipe.source_url_norm == norm, Recipe.deleted_at.is_(None)))
            if r is not None and ctx.source == "url":
                return _duplicate(ctx, s, r, "URL already captured")
    ctx.sha = sha  # type: ignore[attr-defined]
    return False


def _duplicate(ctx: Ctx, s, r: Recipe, why: str) -> str:
    j = s.get(CaptureJob, ctx.job_id)
    if j is not None:
        j.duplicate_of = r.id
        j.recipe_id = r.id
        j.log("hash_dedup", f"duplicate of #{r.id}: {why}")
    ctx.recipe_id = r.id
    if ctx.pdf_path and ctx.pdf_path.exists() and str(get_settings().library_dir / "tmp") in str(ctx.pdf_path):
        ctx.pdf_path.unlink(missing_ok=True)
    return "duplicate"


def stage_store(ctx: Ctx):
    cfg = get_settings()
    sha = getattr(ctx, "sha", None) or pdfops.sha256_file(ctx.pdf_path)
    rel = Path("pdf") / sha[:2] / f"{sha}.pdf"
    dest = cfg.assets_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(ctx.pdf_path, dest)
    if str(cfg.library_dir / "tmp") in str(ctx.pdf_path) or str(cfg.inbox_dir / "print") in str(ctx.pdf_path):
        ctx.pdf_path.unlink(missing_ok=True)
    n_pages = pdfops.page_count(dest)
    with session_scope() as s:
        a = s.scalar(select(Asset).where(Asset.sha256 == sha, Asset.kind == "pdf"))
        if a is None:
            a = Asset(kind="pdf", rel_path=rel.as_posix(), sha256=sha, bytes=dest.stat().st_size,
                      mime="application/pdf", page_count=n_pages)
            s.add(a)
            s.flush()
        ctx.asset_id = a.id
        r = s.get(Recipe, ctx.recipe_id) if ctx.recipe_id else None
        if r is None:
            title = (ctx.title_hint or "").strip() or "Untitled capture"
            r = Recipe(title=title[:200], status="processing", source_url=ctx.url,
                       source_url_norm=normalize_url(ctx.url), page_count=n_pages,
                       source_name=_source_name(ctx))
            s.add(r)
            s.flush()
            ctx.recipe_id = r.id
        r.pdf_asset_id = a.id
        r.page_count = n_pages
        if ctx.force:
            r.status = "processing"
        fts.reindex_recipe(s, r.id)
    ctx.pdf_path = dest
    return False


def _source_name(ctx: Ctx) -> str | None:
    if ctx.url:
        return site_name(ctx.url)
    return {"printer": "Printed", "upload": "Uploaded", "watch": "Inbox"}.get(ctx.source)


def stage_text(ctx: Ctx):
    ctx.page_texts = pdfops.extract_page_texts(ctx.pdf_path)
    ctx.text_source = "layer"
    with session_scope() as s:
        r = s.get(Recipe, ctx.recipe_id)
        R.set_text(s, r, ctx.page_texts, "layer")
        if r.title == "Untitled capture":
            r.title = pdfops.guess_title(ctx.page_texts, r.title)
        fts.reindex_recipe(s, r.id)
    return False


def stage_ocr(ctx: Ctx):
    cfg = get_settings()
    if not cfg.ocr_enabled:
        return True
    need = pdfops.pages_needing_ocr(ctx.page_texts)
    if not need:
        return True
    from . import ocr
    if not ocr.available():
        log.warning("OCR needed for job %s but rapidocr is not installed", ctx.job_id)
        return True
    changed = False
    for i in need:
        png, _w, _h = pdfops.render_page_pixmap_bytes(ctx.pdf_path, i, dpi=200)
        txt = ocr.image_to_text(png)
        if txt.strip():
            ctx.page_texts[i] = txt
            changed = True
    if changed:
        ctx.text_source = "ocr"
        with session_scope() as s:
            r = s.get(Recipe, ctx.recipe_id)
            R.set_text(s, r, ctx.page_texts, "ocr")
            if r.title in ("Untitled capture",) or (ctx.title_hint and r.title == ctx.title_hint[:200]):
                r.title = pdfops.guess_title(ctx.page_texts, r.title)
            fts.reindex_recipe(s, r.id)
    return False


def stage_thumbnail(ctx: Ctx):
    """Every recipe gets a picture: the site's photo, else the best photo found
    in the PDF's first pages, else a crop of the top of page 1."""
    cfg = get_settings()
    thumb = pdfops.render_page_png(ctx.pdf_path, 0, pdfops.THUMB_WIDTH)
    cover = ctx.cover_png
    if cover is None:
        cands = pdfops.candidate_images(ctx.pdf_path, max_pages=2)
        if cands:
            cover = pdfops.image_png(ctx.pdf_path, cands[0]["xref"])
    if cover is None:
        cover = pdfops.render_page_top_png(ctx.pdf_path, 0, pdfops.COVER_WIDTH)
    if cover is not None:
        cover, _w, _h = pdfops.resize_png(cover, pdfops.COVER_WIDTH)
    with session_scope() as s:
        r = s.get(Recipe, ctx.recipe_id)
        r.thumb_asset_id = R.store_image(s, thumb, "thumb").id
        # a cover the user picked by hand (re-extract) is kept
        if cover is not None and not (ctx.force and r.cover_asset_id):
            r.cover_asset_id = R.store_image(s, cover, "cover").id
    return False


_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']{8,}", re.I)


def stage_structured(ctx: Ctx):
    """Use scraper output when the fetch stage produced it. For printed or
    uploaded pages, look for the URL browsers put in the print footer and, if
    found, queue a proper URL capture linked to this one."""
    if ctx.draft:
        return False
    if ctx.source in ("printer", "upload", "watch") and not ctx.url and ctx.page_texts:
        found = None
        for t in ctx.page_texts[:1] + ctx.page_texts[-1:]:
            for m in _URL_IN_TEXT.finditer(t):
                u = m.group(0).rstrip(".,;)")
                if not re.search(r"\.(png|jpe?g|gif|css|js)$", u, re.I) and "/" in u[8:]:
                    found = u
                    break
            if found:
                break
        if found:
            with session_scope() as s:
                r = s.get(Recipe, ctx.recipe_id)
                r.source_url = found
                r.source_url_norm = normalize_url(found)
                r.source_name = site_name(found)
            ctx.url = found
            _mark(ctx, "structured", f"found source URL in print footer: {found}")
    return True


def stage_llm(ctx: Ctx):
    if ctx.draft:
        return True
    cfg = get_settings()
    if not cfg.llm_enabled:
        return True
    text = "\n\n".join(ctx.page_texts).strip()
    if len(text) < 40:
        return True
    from ..extract import llm
    with ctx.queue.llm_lock:  # type: ignore[union-attr]
        draft = llm.extract_recipe(text, title_hint=ctx.title_hint, source=ctx.url)
    if draft is None:
        return True
    ctx.draft = draft
    return False


def stage_normalize(ctx: Ctx):
    d = ctx.draft
    with session_scope() as s:
        r = s.get(Recipe, ctx.recipe_id)
        if not d:
            if r.status == "processing":
                r.status = "needs_review"
            return True
        if d.get("is_recipe") is False:
            r.status = "needs_review"
            r.confidence = d.get("confidence")
            return False
        if d.get("title"):
            r.title = str(d["title"]).strip()[:200] or r.title
        r.description = (d.get("description") or None)
        r.yield_text = d.get("yield_text") or r.yield_text
        r.servings = _num(d.get("servings"))
        r.prep_min = _num_int(d.get("prep_minutes"))
        r.cook_min = _num_int(d.get("cook_minutes"))
        r.total_min = _num_int(d.get("total_minutes")) or (
            (r.prep_min or 0) + (r.cook_min or 0) or None)
        if r.prep_min is None or r.cook_min is None or r.servings is None:
            from ..extract.normalize import detect_times
            found = detect_times("\n".join(ctx.page_texts))
            r.prep_min = r.prep_min if r.prep_min is not None else found["prep_min"]
            r.cook_min = r.cook_min if r.cook_min is not None else found["cook_min"]
            r.servings = r.servings if r.servings is not None else found["servings"]
            if r.total_min is None:
                r.total_min = found["total_min"] or ((r.prep_min or 0) + (r.cook_min or 0) or None)
        r.language = d.get("language") or r.language
        r.confidence = _num(d.get("confidence"))
        r.extraction_method = d.get("method") or "llm"
        from ..extract.postprocess import clean_ingredients, infer_ingredient_groups, infer_step_groups
        d["ingredients"] = clean_ingredients([dict(x) for x in (d.get("ingredients") or []) if isinstance(x, dict)])
        d["ingredients"] = infer_ingredient_groups(d["ingredients"], "\n".join(ctx.page_texts))
        d["steps"] = infer_step_groups([dict(x) if isinstance(x, dict) else {"text": str(x)} for x in (d.get("steps") or [])],
                                       "\n".join(ctx.page_texts), [x.get("group") for x in d["ingredients"]])
        ing_rows = []
        for it in d.get("ingredients") or []:
            raw = (it.get("raw") or it.get("name") or "").strip()
            if not raw:
                continue
            # "For the sauce: 1/2 cup stock" -> group + line (models like to glue headings on)
            m = re.match(r"^([A-Za-z][^:\d]{2,40}):\s+(\S.*)$", raw)
            if m and not it.get("group"):
                it["group"], raw = m.group(1).strip(), m.group(2).strip()
            parsed = parse_line(raw)
            q = _num(it.get("quantity"))
            if q is not None and q <= 0:
                q = None
            # trust the model only if its number actually appears in the line
            if q is not None and parsed.quantity is not None and abs(q - parsed.quantity) > 1e-6:
                q = parsed.quantity
            if q is None:
                q = parsed.quantity
            unit = it.get("unit") or parsed.unit
            from ..extract.normalize import canonical_unit, normalize_name
            unit = canonical_unit(unit) or parsed.unit
            name = (it.get("name") or parsed.name or raw).strip()
            prep = it.get("preparation") or parsed.preparation
            if prep and prep.lower() in name.lower():
                name = re.sub(r",?\s*" + re.escape(prep) + r"\s*$", "", name, flags=re.I).strip(" ,") or name
            ing_rows.append({
                "group_name": it.get("group") or None, "raw_text": raw, "quantity": q,
                "quantity_max": _num(it.get("quantity_max")) or parsed.quantity_max,
                "unit": unit, "unit_raw": parsed.unit_raw, "name": name,
                "name_norm": normalize_name(name), "preparation": prep,
                "optional": 1 if it.get("optional") or parsed.optional else 0,
            })
        step_rows = []
        for st in d.get("steps") or []:
            txt = (st.get("text") if isinstance(st, dict) else str(st) or "").strip()
            if txt:
                step_rows.append({"group_name": (st.get("group") if isinstance(st, dict) else None) or None,
                                  "text": txt, "minutes": detect_minutes(txt)})
        R.replace_ingredients(s, r, ing_rows)
        R.replace_steps(s, r, step_rows)
        if d.get("course"):
            R.set_tags(s, r, "course", [str(d["course"])])
        if d.get("cuisine"):
            R.set_tags(s, r, "cuisine", [str(d["cuisine"])])
        if r.source_name:
            R.set_tags(s, r, "source", [r.source_name])
        s.flush()
        R.assign_categories(s, r, extra=[str(c) for c in (d.get("categories") or [])], replace=not ctx.force)
        conf = r.confidence if r.confidence is not None else 0.7
        r.status = "ready" if (conf >= 0.6 and ing_rows and step_rows) else "needs_review"
        r.updated_at = utcnow()
    return False


def stage_index(ctx: Ctx):
    with session_scope() as s:
        r = s.get(Recipe, ctx.recipe_id)
        if r.source_name and not r.tags_of("source"):
            R.set_tags(s, r, "source", [r.source_name])
        if r.status == "processing":
            r.status = "needs_review"
        s.flush()
        fts.reindex_recipe(s, r.id)
    return False


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num_int(v) -> int | None:
    f = _num(v)
    return int(round(f)) if f is not None else None
