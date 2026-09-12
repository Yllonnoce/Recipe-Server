"""Turn a print job payload (PDF, PWG raster, URF, JPEG/PNG) into a PDF file."""
from __future__ import annotations

from pathlib import Path

from ...capture import pdf as pdfops
from . import pwg, urf
from .rle import RasterError


def sniff(head: bytes) -> str:
    if head[:5] == b"%PDF-":
        return "application/pdf"
    if pwg.is_pwg(head):
        return "image/pwg-raster"
    if urf.is_urf(head):
        return "image/urf"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "application/octet-stream"


def convert_bytes(data: bytes) -> bytes:
    kind = sniff(data[:16])
    if kind == "application/pdf":
        return data
    if kind == "image/pwg-raster":
        pages = pwg.decode(data)
    elif kind == "image/urf":
        pages = urf.decode(data)
    elif kind in ("image/jpeg", "image/png"):
        return pdfops.images_to_pdf([data])
    else:
        raise RasterError("unrecognised document format")
    return pdfops.pixmaps_to_pdf([(p.width, p.height, p.channels, p.samples, p.dpi) for p in pages])


def convert_file(src: Path, dst: Path) -> Path:
    dst.write_bytes(convert_bytes(src.read_bytes()))
    return dst
