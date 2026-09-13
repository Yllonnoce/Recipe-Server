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


def shrink_library(progress=None) -> dict:
    """Retroactively shrink every stored PDF, cover and thumbnail. Assets are
    named by their hash, so a changed file is moved to its new name and the
    row updated. Returns totals in bytes."""
    from sqlalchemy import select

    from ..capture.pdf import sha256_file
    from ..db.engine import session_scope
    from ..db.models import Asset
    cfg = get_settings()
    totals = {"before": 0, "after": 0, "files": 0, "changed": 0}
    with session_scope() as s:
        assets = list(s.scalars(select(Asset)))
    for a in assets:
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
            _rename_asset(a.id, p)
        if progress:
            progress(totals)
    return totals


def _rename_asset(asset_id: int, p: Path) -> None:
    """After the bytes changed: new hash, new name, updated row."""
    from ..capture.pdf import sha256_file
    from ..db.engine import session_scope
    from ..db.models import Asset
    cfg = get_settings()
    sha = sha256_file(p)
    with session_scope() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            return
        head = p.open("rb").read(4)
        ext = ".pdf" if a.kind == "pdf" else (".jpg" if head[:3] == b"\xff\xd8\xff" else ".png")
        rel = Path(a.kind) / sha[:2] / f"{sha}{ext}"
        dest = cfg.assets_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest != p:
            if dest.exists():
                p.unlink(missing_ok=True)
            else:
                p.replace(dest)
        a.rel_path = rel.as_posix()
        a.sha256 = sha
        a.bytes = dest.stat().st_size
        if a.kind != "pdf":
            a.mime = "image/jpeg" if ext == ".jpg" else "image/png"
