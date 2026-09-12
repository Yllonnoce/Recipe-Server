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


def render_page_pixmap_bytes(path: Path, page_index: int, dpi: int = 200) -> tuple[bytes, int, int]:
    """PNG bytes of a page at the given dpi (for OCR)."""
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("png"), pix.width, pix.height


def largest_image_on_page(path: Path, page_index: int = 0, min_px: int = 300) -> bytes | None:
    """The biggest embedded image on a page, as PNG, or None if nothing sizable."""
    with pymupdf.open(path) as doc:
        page = doc[page_index]
        best = None
        for info in page.get_images(full=True):
            xref = info[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
            except Exception:
                continue
            if pix.width < min_px or pix.height < min_px:
                continue
            area = pix.width * pix.height
            if best is None or area > best[0]:
                best = (area, xref)
        if best is None:
            return None
        pix = pymupdf.Pixmap(doc, best[1])
        if pix.n - pix.alpha >= 4:          # CMYK etc -> RGB
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        return pix.tobytes("png")


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
    """Wrap raw image bytes (png/jpeg/etc, or a decoded pixmap) into a PDF, one page each."""
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
