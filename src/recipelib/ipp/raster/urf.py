"""Apple URF / UNIRAST: 'UNIRAST\\0' + uint32 page count, then per page a
32-byte header (bpp, colour space, duplex, quality, ..., width @12,
height @16, dpi @20, all big-endian) and run-length rows. Layout follows
CUPS raster-stream.c; offsets verified against airprinthq's decoder."""
from __future__ import annotations

import struct

from .pwg import Page
from .rle import RasterError, cmyk_to_rgb, decode_rows

SYNC = b"UNIRAST\x00"
# colour space byte -> (channels, blank pixel)
CS = {0: 1, 1: 3, 3: 3, 4: 1, 5: 3, 6: 4}   # SW, SRGB, (2 CIELab unsupported), ADOBERGB, W, RGB, CMYK


def is_urf(data: bytes) -> bool:
    return data[:8] == SYNC


def decode(data: bytes) -> list[Page]:
    if not is_urf(data):
        raise RasterError("not a URF stream")
    (count,) = struct.unpack(">I", data[8:12])
    pos = 12
    pages: list[Page] = []
    while pos + 32 <= len(data) and (count == 0 or len(pages) < count):
        h = data[pos:pos + 32]
        pos += 32
        bpp_bits, cspace = h[0], h[1]
        width, height, dpi = struct.unpack(">III", h[12:24])
        if width == 0 or height == 0:
            break
        if cspace not in CS:
            raise RasterError(f"unsupported URF colour space {cspace}")
        channels = CS[cspace]
        bpp = max(1, bpp_bits // 8)
        if bpp != channels:
            # e.g. 16-bit gray; take it as declared and downsample below
            pass
        blank = (b"\x00" if channels == 4 else b"\xff") * bpp
        samples, pos = decode_rows(data, pos, width, height, bpp, blank)
        s = bytes(samples)
        if bpp == channels * 2:
            s = bytes(s[i] for i in range(0, len(s), 2))
        if channels == 4:
            pages.append(Page(width, height, 3, cmyk_to_rgb(s), dpi or 300))
        else:
            pages.append(Page(width, height, channels, s, dpi or 300))
    if not pages:
        raise RasterError("URF stream has no pages")
    return pages
