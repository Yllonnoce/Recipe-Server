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


# ---- photos inside scanned / printed pages ---------------------------------
# A print job arrives as one flat picture per page, so the recipe's photo is
# not a separate object in the PDF: it has to be found by looking at the page.

_SCAN_COVERAGE = 0.85        # an image filling this much of a page makes it a "flat" page
_GRID_WIDTH = 800            # page is analysed at this width...
_CELL = 8                    # ...in cells of this many pixels


def photo_boxes(im) -> list[tuple[float, float, float, float]]:
    """Photo-like rectangles in a page picture, as (x0, y0, x1, y1) fractions of
    the page, largest first.

    Text is ink on white, so a cell of text is mostly paper; a cell of a photo
    is covered edge to edge. Solidly covered cells are grouped, and a group
    counts as a photo when it is a reasonably filled rectangle of sensible
    size and shape with real tonal variation (not a rule, banner or flat box)."""
    from collections import deque

    from PIL import Image, ImageChops, ImageStat

    im = im.convert("RGB")
    w = _GRID_WIDTH
    h = max(_CELL, round(im.height * w / im.width))
    small = im.resize((w, h), Image.BILINEAR)
    r, g, b = small.split()
    ink = ImageChops.darker(ImageChops.darker(r, g), b).point(lambda v: 255 if v < 232 else 0)
    gw, gh = w // _CELL, h // _CELL
    cells = ink.crop((0, 0, gw * _CELL, gh * _CELL)).resize((gw, gh), Image.BOX).tobytes()
    solid = [v >= 215 for v in cells]
    gray = small.convert("L")

    # groups of touching solid cells: [cells, x0, y0, x1, y1]
    groups: list[list[int]] = []
    seen = [False] * (gw * gh)
    for start in range(gw * gh):
        if not solid[start] or seen[start]:
            continue
        seen[start] = True
        todo = deque([start])
        n, x0, y0, x1, y1 = 0, gw, gh, -1, -1
        while todo:
            i = todo.popleft()
            cy, cx = divmod(i, gw)
            n += 1
            x0, y0, x1, y1 = min(x0, cx), min(y0, cy), max(x1, cx), max(y1, cy)
            for j, ok in ((i - 1, cx > 0), (i + 1, cx < gw - 1), (i - gw, cy > 0), (i + gw, cy < gh - 1)):
                if ok and solid[j] and not seen[j]:
                    seen[j] = True
                    todo.append(j)
        if n >= 3:
            groups.append([n, x0, y0, x1, y1])
    # white areas in a photo (a plate, icing, sky) split it into pieces: rejoin
    # pieces that sit within a couple of cells of each other
    gap = 2
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(len(groups) - 1, i, -1):
                a, b = groups[i], groups[j]
                if a[1] - gap <= b[3] and b[1] - gap <= a[3] and a[2] - gap <= b[4] and b[2] - gap <= a[4]:
                    groups[i] = [a[0] + b[0], min(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4])]
                    del groups[j]
                    merged = True

    boxes: list[tuple[int, tuple[float, float, float, float]]] = []
    for n, x0, y0, x1, y1 in groups:
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw < gw * 0.15 or bh < gh * 0.08:            # rules, icons, avatars
            continue
        if bw * bh > gw * gh * 0.85:                    # a dark page is not a photo
            continue
        if n < bw * bh * 0.5:                           # not a filled rectangle
            continue
        if not 0.4 <= bw / bh <= 2.6:                   # banners, ads, side strips
            continue
        px = (x0 * _CELL, y0 * _CELL, (x1 + 1) * _CELL, (y1 + 1) * _CELL)
        if ImageStat.Stat(gray.crop(px)).stddev[0] < 12:    # flat colour block
            continue
        boxes.append((bw * bh, (px[0] / w, px[1] / h, px[2] / w, px[3] / h)))
    boxes.sort(key=lambda t: -t[0])
    return [b for _a, b in boxes]


def _page_photo_candidates(page, pno: int) -> list[dict]:
    from PIL import Image
    pix = page.get_pixmap(matrix=pymupdf.Matrix(_GRID_WIDTH / page.rect.width, _GRID_WIDTH / page.rect.width),
                          alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    pw, ph = page.rect.width, page.rect.height
    out = []
    for fx0, fy0, fx1, fy1 in photo_boxes(im):
        x0, y0, x1, y1 = round(fx0 * pw), round(fy0 * ph), round(fx1 * pw), round(fy1 * ph)
        out.append({"page": pno, "key": f"clip:{pno}:{x0}:{y0}:{x1}:{y1}",
                    "width": round((x1 - x0) * COVER_WIDTH / pw), "height": round((y1 - y0) * COVER_WIDTH / pw),
                    "area": (x1 - x0) * (y1 - y0) * (1.3 if pno == 0 else 1.0)})
    return out


def candidate_images(path: Path, max_pages: int = 3, min_px: int = 200) -> list[dict]:
    """Photos in the first pages, best cover candidate first.

    Ranked by the area the image occupies *on the page* (a hero photo beats a
    high-resolution but tiny thumbnail), with banner/strip shapes and tiny
    images dropped. A page that is one flat picture (a print job, a scan) is
    searched for the photos inside it instead of offering the whole page.
    Each entry: page, key (for candidate_png), width, height, area."""
    out: list[dict] = []
    with pymupdf.open(path) as doc:
        seen: set[int] = set()
        for pno in range(min(max_pages, doc.page_count)):
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            flat = False
            found: list[dict] = []
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                w, h = info[2], info[3]
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                shown = max(r.width * r.height for r in rects)
                if shown >= page_area * _SCAN_COVERAGE:
                    flat = True
                    break
                if w < min_px or h < min_px:
                    continue
                ratio = w / h
                if ratio > 2.6 or ratio < 0.4:          # banners, ads, side strips
                    continue
                if shown < 40 * 40:
                    continue
                # earlier pages win ties; page 1 gets a bonus so a hero shot on
                # the first page beats a bigger photo further down
                found.append({"page": pno, "xref": xref, "key": f"xref:{xref}", "width": w, "height": h,
                              "area": shown * (1.3 if pno == 0 else 1.0)})
            if flat:
                out.extend(_page_photo_candidates(page, pno))
            else:
                seen.update(c["xref"] for c in found)
                out.extend(found)
    out.sort(key=lambda d: -d["area"])
    return out


def candidate_png(path: Path, key: str, width: int = COVER_WIDTH) -> bytes | None:
    """The picture behind a candidate_images() key: 'xref:<n>' or 'clip:<page>:<x0>:<y0>:<x1>:<y1>'."""
    kind, _, rest = key.partition(":")
    try:
        nums = [int(v) for v in rest.split(":")]
    except ValueError:
        return None
    if kind == "xref" and len(nums) == 1:
        return image_png(path, nums[0])
    if kind != "clip" or len(nums) != 5:
        return None
    with pymupdf.open(path) as doc:
        if not 0 <= nums[0] < doc.page_count:
            return None
        page = doc[nums[0]]
        clip = pymupdf.Rect(*nums[1:]) & page.rect
        if clip.is_empty or clip.width < 8 or clip.height < 8:
            return None
        zoom = min(width / clip.width, 4.0)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False).tobytes("png")


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
    return candidate_png(path, cands[0]["key"])


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
