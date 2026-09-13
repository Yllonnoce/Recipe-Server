"""PDF helpers on top of PyMuPDF: hashing, text, thumbnails, image -> PDF."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pymupdf

THUMB_WIDTH = 320
COVER_WIDTH = 800
MIN_CHARS_PER_PAGE = 40      # below this a page is treated as image-only (OCR candidate)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def extract_page_texts(path: Path) -> list[str]:
    out = []
    with pymupdf.open(path) as doc:
        for page in doc:
            out.append(page.get_text("text"))
    return out


def pages_needing_ocr(page_texts: list[str]) -> list[int]:
    return [i for i, t in enumerate(page_texts) if len(t.strip()) < MIN_CHARS_PER_PAGE]


def render_page_png(path: Path, page_index: int, width: int) -> bytes:
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        zoom = width / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


def render_page_top_png(path: Path, page_index: int, width: int, aspect: float = 0.75) -> bytes:
    """The top of a page cropped to a landscape photo shape (for page-as-cover)."""
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        pw, ph = page.rect.width, page.rect.height
        clip = pymupdf.Rect(0, 0, pw, min(ph, pw * aspect))
        zoom = width / pw
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
        return pix.tobytes("png")


def render_page_pixmap_bytes(path: Path, page_index: int, dpi: int = 200) -> tuple[bytes, int, int]:
    """PNG bytes of a page at the given dpi (for OCR)."""
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("png"), pix.width, pix.height


def candidate_images(path: Path, max_pages: int = 3, min_px: int = 200) -> list[dict]:
    """Photos embedded in the first pages, best cover candidate first.

    Ranked by the area the image occupies *on the page* (a hero photo beats a
    high-resolution but tiny thumbnail), with banner/strip shapes and tiny
    images dropped. Each entry: page, xref, width, height, area."""
    out: list[dict] = []
    with pymupdf.open(path) as doc:
        seen: set[int] = set()
        for pno in range(min(max_pages, doc.page_count)):
            page = doc[pno]
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                w, h = info[2], info[3]
                if w < min_px or h < min_px:
                    continue
                ratio = w / h
                if ratio > 2.6 or ratio < 0.4:          # banners, ads, side strips
                    continue
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                shown = max(r.width * r.height for r in rects)
                if shown < 40 * 40:
                    continue
                seen.add(xref)
                # earlier pages win ties; page 1 gets a bonus so a hero shot on
                # the first page beats a bigger photo further down
                out.append({"page": pno, "xref": xref, "width": w, "height": h,
                            "area": shown * (1.3 if pno == 0 else 1.0)})
    out.sort(key=lambda d: -d["area"])
    return out


def image_png(path: Path, xref: int) -> bytes | None:
    with pymupdf.open(path) as doc:
        try:
            pix = pymupdf.Pixmap(doc, xref)
        except Exception:
            return None
        if pix.n - pix.alpha >= 4:          # CMYK etc -> RGB
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        return pix.tobytes("png")


def largest_image_on_page(path: Path, page_index: int = 0, min_px: int = 300) -> bytes | None:
    """Best cover candidate from the first page (kept for the pipeline)."""
    cands = [c for c in candidate_images(path, max_pages=1, min_px=min_px) if c["page"] == page_index]
    if not cands:
        return None
    return image_png(path, cands[0]["xref"])


def resize_png(png: bytes, width: int) -> tuple[bytes, int, int]:
    pix = pymupdf.Pixmap(png)
    if pix.width <= width:
        return png, pix.width, pix.height
    scale = width / pix.width
    h = max(1, int(pix.height * scale))
    # PyMuPDF has no resize on Pixmap; go through a one-page doc
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=h)
    page.insert_image(page.rect, pixmap=pix)
    out = page.get_pixmap(alpha=False)
    data = out.tobytes("png")
    doc.close()
    return data, out.width, out.height


def images_to_pdf(images: list[bytes], dpi: int = 150) -> bytes:
    """Wrap raw image bytes (png/jpeg/etc, or a decoded pixmap) into a PDF, one page each.
    Photos are recompressed first when shrinking is on."""
    from ..config import get_settings
    if get_settings().shrink_files:
        from .shrink import shrink_image_bytes
        images = [shrink_image_bytes(d)[0] for d in images]
    doc = pymupdf.open()
    for data in images:
        pix = pymupdf.Pixmap(data)
        w_pt = pix.width * 72.0 / dpi
        h_pt = pix.height * 72.0 / dpi
        page = doc.new_page(width=w_pt, height=h_pt)
        page.insert_image(page.rect, pixmap=pix)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def pixmaps_to_pdf(pages: list[tuple[int, int, int, bytes, int]]) -> bytes:
    """(width, height, channels, samples, dpi) tuples -> PDF, JPEG-compressed pages."""
    doc = pymupdf.open()
    for w, h, n, samples, dpi in pages:
        cs = pymupdf.csGRAY if n == 1 else pymupdf.csRGB
        pix = pymupdf.Pixmap(cs, w, h, samples, False)
        page = doc.new_page(width=w * 72.0 / dpi, height=h * 72.0 / dpi)
        page.insert_image(page.rect, stream=pix.tobytes("jpeg", jpg_quality=85))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def first_lines(page_texts: list[str], n: int = 3) -> list[str]:
    for t in page_texts:
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        if lines:
            return lines[:n]
    return []


def guess_title(page_texts: list[str], fallback: str) -> str:
    """Longest of the first few non-trivial lines, trimmed to a sane length."""
    lines = [ln for ln in first_lines(page_texts, 6) if 3 < len(ln) < 120]
    lines = [ln for ln in lines if not ln.lower().startswith(("http", "www."))]
    if not lines:
        return fallback
    # first line usually the title; prefer it unless it's just a site name
    cand = lines[0]
    if len(cand) < 8 and len(lines) > 1:
        cand = lines[1]
    return cand[:120]
