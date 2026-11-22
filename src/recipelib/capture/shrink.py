"""Make stored files smaller: photos are recompressed with Pillow, and the
images inside PDFs are downsampled with PyMuPDF. All optional (config
`shrink_files`) and lossy-but-sensible: a phone photo of a recipe card does
not need 12 megapixels, and a printed web page does not need 300 dpi
banners. The original is kept whenever shrinking would not save much."""
from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)
MIN_SAVING = 0.10          # keep the original unless we save at least 10 %


def shrink_image_bytes(data: bytes, max_px: int | None = None, quality: int | None = None) -> tuple[bytes, str]:
    """Return (bytes, mime). Large photos come back as a capped-size JPEG;
    anything that would not shrink is returned unchanged."""
    cfg = get_settings()
    max_px = max_px or cfg.image_max_px
    quality = quality or cfg.jpeg_quality
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        fmt = (im.format or "").lower()
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        out = buf.getvalue()
        if len(out) < len(data) * (1 - MIN_SAVING) or max(Image.open(io.BytesIO(data)).size) > max_px:
            return out, "image/jpeg"
        return data, {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}.get(fmt, "application/octet-stream")
    except Exception as e:  # noqa: BLE001
        log.debug("image shrink skipped: %s", e)
        return data, "application/octet-stream"


def to_jpeg(png: bytes, quality: int | None = None) -> bytes:
    """PNG (or any image) -> JPEG bytes, for covers and thumbnails."""
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality or get_settings().jpeg_quality, optimize=True, progressive=True)
    return buf.getvalue()


def shrink_pdf(path: Path, dpi: int | None = None, quality: int | None = None) -> tuple[int, int]:
    """Downsample the images inside a PDF in place. Returns (bytes before, bytes after);
    the file is left untouched when the saving is small."""
    import pymupdf
    cfg = get_settings()
    dpi = dpi if dpi is not None else cfg.pdf_image_dpi
    quality = quality or cfg.jpeg_quality
    before = path.stat().st_size
    if dpi <= 0 or before < 200_000:
        return before, before
    tmp = Path(tempfile.mkstemp(suffix=".pdf", dir=str(path.parent))[1])
    try:
        with pymupdf.open(path) as doc:
            if doc.is_encrypted or doc.page_count == 0:
                return before, before
            doc.rewrite_images(dpi_threshold=int(dpi * 1.25), dpi_target=dpi, quality=quality,
                               lossy=True, lossless=True, bitonal=False, color=True, gray=True)
            doc.save(tmp, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
        after = tmp.stat().st_size
        if after < before * (1 - MIN_SAVING):
            tmp.replace(path)
            log.info("shrunk %s: %d KB -> %d KB", path.name, before // 1024, after // 1024)
            return before, after
        tmp.unlink(missing_ok=True)
        return before, before
    except Exception as e:  # noqa: BLE001
        log.warning("pdf shrink skipped for %s: %s", path.name, e)
        tmp.unlink(missing_ok=True)
        return before, before


def shrink_library(progress=None, report=None) -> dict:
    """Retroactively shrink every stored PDF, cover and thumbnail. Assets are
    named by their hash, so a changed file is moved to its new name and the
    row updated. Returns totals in bytes. report(step, done, total) feeds a progress bar."""
    from sqlalchemy import select

    from ..capture.pdf import sha256_file
    from ..db.engine import session_scope
    from ..db.models import Asset
    cfg = get_settings()
    totals = {"before": 0, "after": 0, "files": 0, "changed": 0, "merged": 0, "repaired": 0, "problems": 0}
    totals["repaired"] = repair_missing_thumbnails(report)
    with session_scope() as s:
        assets = list(s.scalars(select(Asset)))
    for i, a in enumerate(assets):
        if report:
            report("Shrinking PDFs and pictures", i, len(assets))
        p = cfg.assets_dir / a.rel_path
        if not p.is_file():
            continue
        before = p.stat().st_size
        try:
            if a.kind == "pdf":
                shrink_pdf(p)
            elif a.kind in ("cover", "thumb") and a.mime == "image/png":
                jpg = to_jpeg(p.read_bytes())
                if len(jpg) < before * (1 - MIN_SAVING):
                    p.write_bytes(jpg)
            else:
                continue
        except Exception as e:  # noqa: BLE001
            log.warning("shrink skipped %s: %s", a.rel_path, e)
            continue
        after = p.stat().st_size
        totals["files"] += 1
        totals["before"] += before
        totals["after"] += after
        if after != before:
            totals["changed"] += 1
            try:
                if _rename_asset(a.id, p) == "merged":
                    totals["merged"] += 1
            except Exception as e:  # noqa: BLE001  one picture must not stop the run
                log.warning("shrink: could not rename %s: %s: %s", a.rel_path, type(e).__name__, e)
                totals["problems"] += 1
        if progress:
            progress(totals)
    return totals


def _rename_asset(asset_id: int, p: Path) -> str | None:
    """After the bytes changed: new hash, new name, updated row. When another stored
    picture of the same kind already has exactly these bytes (an old PNG thumbnail whose
    JPEG a later re-extract stored), everything pointing at this one is moved to that one
    and this row goes ("merged"). Files are only removed after the database has changed,
    so a failure never leaves a row pointing at a deleted file."""
    from sqlalchemy import select

    from ..capture.pdf import sha256_file
    from ..db.engine import session_scope
    from ..db.models import Asset
    cfg = get_settings()
    sha = sha256_file(p)
    with p.open("rb") as fh:
        head = fh.read(4)
    with session_scope() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            return None
        twin = s.scalar(select(Asset).where(Asset.sha256 == sha, Asset.kind == a.kind, Asset.id != a.id))
        if twin is not None:
            twin_file = cfg.assets_dir / twin.rel_path
            if not twin_file.is_file():               # keep the bytes: they are the twin's picture
                twin_file.parent.mkdir(parents=True, exist_ok=True)
                twin_file.write_bytes(p.read_bytes())
            _repoint(s, a.id, twin.id)
            s.delete(a)
        else:
            ext = ".pdf" if a.kind == "pdf" else (".jpg" if head[:3] == b"\xff\xd8\xff" else ".png")
            rel = Path(a.kind) / sha[:2] / f"{sha}{ext}"
            dest = cfg.assets_dir / rel
            if dest != p:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.write_bytes(p.read_bytes())  # copy now, remove the old name after the commit
            a.rel_path = rel.as_posix()
            a.sha256 = sha
            a.bytes = dest.stat().st_size
            if a.kind != "pdf":
                a.mime = "image/jpeg" if ext == ".jpg" else "image/png"
            if dest == p:
                return None
    p.unlink(missing_ok=True)                         # the database no longer points here
    return "merged" if twin is not None else None


def _repoint(s, old_id: int, new_id: int) -> None:
    """Move every reference to asset old_id over to new_id."""
    from sqlalchemy import update

    from ..db.models import CaptureJob, Recipe
    for col in (Recipe.pdf_asset_id, Recipe.cover_asset_id, Recipe.thumb_asset_id):
        s.execute(update(Recipe).where(col == old_id).values({col.key: new_id}))
    s.execute(update(CaptureJob).where(CaptureJob.asset_id == old_id).values(asset_id=new_id))
    s.flush()


def repair_missing_thumbnails(report=None) -> int:
    """Redraw the thumbnail of every recipe whose thumbnail file is gone, from its PDF.
    Returns how many were redrawn."""
    from sqlalchemy import select

    from ..capture import pdf as pdfops
    from ..db.engine import session_scope
    from ..db.models import Asset, Recipe
    from ..domain import recipes as R
    cfg = get_settings()
    n = 0
    with session_scope() as s:
        # deleted recipes too: they come back with a restore or merge, and should look right
        rows = list(s.scalars(select(Recipe).where(Recipe.pdf_asset_id.is_not(None))))
        for i, r in enumerate(rows):
            if report:
                report("Checking thumbnails", i, len(rows))
            t = s.get(Asset, r.thumb_asset_id) if r.thumb_asset_id else None
            if t is not None and (cfg.assets_dir / t.rel_path).is_file():
                continue
            pdf = cfg.assets_dir / r.pdf_asset.rel_path if r.pdf_asset else None
            if not pdf or not pdf.is_file():
                continue
            try:
                png = pdfops.render_page_png(pdf, 0, pdfops.THUMB_WIDTH)
                new = R.store_image(s, png, "thumb")
                path = cfg.assets_dir / new.rel_path
                if not path.is_file():                 # store_image found a row whose file is gone too
                    from .shrink import to_jpeg
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(to_jpeg(png) if path.suffix == ".jpg" else png)
                r.thumb_asset_id = new.id
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("could not redraw the thumbnail of %r: %s", r.title, e)
    if n:
        log.info("redrew %d missing thumbnail(s)", n)
    return n
