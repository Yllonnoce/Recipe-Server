"""PWG Raster (PWG 5102.4): 'RaS2' sync word, then per page a 1796-byte
big-endian CUPS v2 page header followed by run-length rows."""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .rle import RasterError, cmyk_to_rgb, decode_rows, take_high_bytes, unpack_1bit

SYNC = b"RaS2"
HEADER_LEN = 1796

# cupsColorSpace values
CS_W, CS_RGB, CS_K, CS_CMY, CS_CMYK = 0, 1, 3, 4, 6
CS_SW, CS_SRGB, CS_ADOBERGB = 18, 19, 20


@dataclass
class Page:
    width: int
    height: int
    channels: int        # 1 gray or 3 rgb
    samples: bytes
    dpi: int


def is_pwg(data: bytes) -> bool:
    return data[:4] == SYNC


def decode(data: bytes) -> list[Page]:
    if not is_pwg(data):
        raise RasterError("not a PWG raster stream")
    pos = 4
    pages: list[Page] = []
    while pos + HEADER_LEN <= len(data):
        h = data[pos:pos + HEADER_LEN]
        pos += HEADER_LEN
        xres, yres = struct.unpack(">II", h[276:284])
        width, height = struct.unpack(">II", h[372:380])
        bits_per_color, bits_per_pixel, bytes_per_line, color_order, cspace = struct.unpack(">IIIII", h[384:404])
        if width == 0 or height == 0:
            break
        dpi = xres or 300
        if bits_per_pixel == 1:
            row_bytes = (width + 7) // 8
            samples, pos = decode_rows(data, pos, row_bytes, height, 1,
                                       b"\x00" if cspace == CS_K else b"\xff")
            gray = unpack_1bit(bytes(samples), width, height, one_is_black=(cspace == CS_K))
            pages.append(Page(width, height, 1, gray, dpi))
            continue
        bpp = max(1, bits_per_pixel // 8)
        if cspace in (CS_W, CS_SW):
            blank = b"\xff" * bpp
            samples, pos = decode_rows(data, pos, width, height, bpp, blank)
            s = bytes(samples)
            if bits_per_color == 16:
                s = take_high_bytes(s, 1)
            pages.append(Page(width, height, 1, s, dpi))
        elif cspace == CS_K:
            samples, pos = decode_rows(data, pos, width, height, bpp, b"\x00" * bpp)
            s = bytes(samples)
            if bits_per_color == 16:
                s = take_high_bytes(s, 1)
            pages.append(Page(width, height, 1, bytes(255 - b for b in s), dpi))
        elif cspace in (CS_RGB, CS_SRGB, CS_ADOBERGB):
            samples, pos = decode_rows(data, pos, width, height, bpp, b"\xff" * bpp)
            s = bytes(samples)
            if bits_per_color == 16:
                s = take_high_bytes(s, 3)
            pages.append(Page(width, height, 3, s, dpi))
        elif cspace == CS_CMYK:
            samples, pos = decode_rows(data, pos, width, height, bpp, b"\x00" * bpp)
            s = bytes(samples)
            if bits_per_color == 16:
                s = take_high_bytes(s, 4)
            pages.append(Page(width, height, 3, cmyk_to_rgb(s), dpi))
        else:
            raise RasterError(f"unsupported PWG colour space {cspace}")
    if not pages:
        raise RasterError("PWG stream has no pages")
    return pages
