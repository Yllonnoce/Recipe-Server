"""Tiny PWG / URF encoders used to synthesise test fixtures (and to feed the
printer end-to-end). Deterministic and independent of the decoders."""
from __future__ import annotations

import struct


def rle_row(row: bytes, bpp: int) -> bytes:
    """PackBits-style encoding of one row of pixels (bpp bytes each)."""
    px = [row[i:i + bpp] for i in range(0, len(row), bpp)]
    out = bytearray()
    i = 0
    n = len(px)
    while i < n:
        # count a run
        j = i
        while j + 1 < n and px[j + 1] == px[i] and j - i < 127:
            j += 1
        run = j - i + 1
        if run >= 2:
            out.append(run - 1)
            out += px[i]
            i = j + 1
            continue
        # literal stretch
        k = i
        while k + 1 < n and px[k + 1] != px[k] and k - i < 126:
            k += 1
        lit = k - i + 1
        if lit == 1:
            out.append(0)
            out += px[i]
        else:
            out.append(257 - lit)
            for p in px[i:k + 1]:
                out += p
        i = k + 1
    return bytes(out)


def encode_page_data(width: int, height: int, bpp: int, samples: bytes) -> bytes:
    out = bytearray()
    row_len = width * bpp
    y = 0
    while y < height:
        row = samples[y * row_len:(y + 1) * row_len]
        rep = 1
        while y + rep < height and rep < 256 and samples[(y + rep) * row_len:(y + rep + 1) * row_len] == row:
            rep += 1
        out.append(rep - 1)
        out += rle_row(row, bpp)
        y += rep
    return bytes(out)


def pwg(pages: list[tuple[int, int, int, bytes]], dpi: int = 300, cspace: int | None = None) -> bytes:
    """pages: (width, height, channels, samples). channels 1 -> sgray_8, 3 -> srgb_8."""
    out = bytearray(b"RaS2")
    for w, h, ch, samples in pages:
        hdr = bytearray(1796)
        hdr[0:9] = b"PwgRaster"
        struct.pack_into(">II", hdr, 276, dpi, dpi)
        struct.pack_into(">II", hdr, 372, w, h)
        cs = cspace if cspace is not None else (18 if ch == 1 else 19)
        struct.pack_into(">IIIII", hdr, 384, 8, 8 * ch, w * ch, 0, cs)
        struct.pack_into(">I", hdr, 420, ch)
        out += hdr
        out += encode_page_data(w, h, ch, samples)
    return bytes(out)


def pwg_1bit(width: int, height: int, packed_rows: bytes, dpi: int = 300, cspace: int = 3) -> bytes:
    """black_1 page: packed 1-bit rows (1 = black when cspace == 3 / K)."""
    out = bytearray(b"RaS2")
    hdr = bytearray(1796)
    hdr[0:9] = b"PwgRaster"
    struct.pack_into(">II", hdr, 276, dpi, dpi)
    struct.pack_into(">II", hdr, 372, width, height)
    row_bytes = (width + 7) // 8
    struct.pack_into(">IIIII", hdr, 384, 1, 1, row_bytes, 0, cspace)
    out += hdr
    out += encode_page_data(row_bytes, height, 1, packed_rows)
    return bytes(out)


def urf(pages: list[tuple[int, int, int, bytes]], dpi: int = 300) -> bytes:
    out = bytearray(b"UNIRAST\x00" + struct.pack(">I", len(pages)))
    for w, h, ch, samples in pages:
        hdr = bytearray(32)
        hdr[0] = 8 * ch
        hdr[1] = 0 if ch == 1 else 1          # SW or SRGB
        hdr[2] = 1                             # simplex
        hdr[3] = 4                             # quality normal
        struct.pack_into(">III", hdr, 12, w, h, dpi)
        out += hdr
        out += encode_page_data(w, h, ch, samples)
    return bytes(out)


def urf_with_eol(width: int, height: int) -> bytes:
    """A gray URF page whose rows are 'one black pixel then 0x80 clear to end'."""
    out = bytearray(b"UNIRAST\x00" + struct.pack(">I", 1))
    hdr = bytearray(32)
    hdr[0], hdr[1] = 8, 0
    struct.pack_into(">III", hdr, 12, width, height, 300)
    out += hdr
    out += bytes([height - 1, 0, 0x00, 0x80])   # repeat all rows: pixel 0 once, then clear
    return bytes(out)


def gradient(width: int, height: int, channels: int) -> bytes:
    buf = bytearray()
    for y in range(height):
        for x in range(width):
            if channels == 1:
                buf.append((x * 255) // max(1, width - 1))
            else:
                buf += bytes(((x * 255) // max(1, width - 1), (y * 255) // max(1, height - 1), 128))
    return bytes(buf)
