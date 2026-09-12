"""Uploads from the web UI: PDFs straight through, images wrapped into a PDF."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from ...config import get_settings
from .. import pdf as pdfops

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
MAX_BYTES = 300 * 1024 * 1024


def safe_stem(name: str) -> str:
    stem = Path(name or "upload").stem
    stem = re.sub(r"[^\w\s.-]", "", stem).strip()[:80]
    return stem or "upload"


def stage_upload(filename: str, data: bytes) -> tuple[Path, str]:
    """Write the upload into the library tmp dir as a PDF. Returns (path, title hint)."""
    if len(data) > MAX_BYTES:
        raise ValueError("file is too large (300 MB max)")
    cfg = get_settings()
    tmp = cfg.library_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(filename)
    ext = Path(filename or "").suffix.lower()
    if data[:5] == b"%PDF-":
        out = tmp / f"{uuid.uuid4().hex}.pdf"
        out.write_bytes(data)
    elif ext in IMAGE_EXT or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
        out = tmp / f"{uuid.uuid4().hex}.pdf"
        out.write_bytes(pdfops.images_to_pdf([data]))
    else:
        raise ValueError("only PDF or image files are accepted")
    return out, stem.replace("_", " ").replace("-", " ")


def stage_images_as_one(filenames: list[str], blobs: list[bytes]) -> tuple[Path, str]:
    """Several photos of one recipe -> one multi-page PDF."""
    cfg = get_settings()
    tmp = cfg.library_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / f"{uuid.uuid4().hex}.pdf"
    out.write_bytes(pdfops.images_to_pdf(blobs))
    return out, safe_stem(filenames[0] if filenames else "scan")
