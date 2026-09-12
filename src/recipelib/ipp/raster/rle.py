"""Shared run-length decoder for PWG raster and Apple URF.

Both formats encode a page as rows. Each row starts with a line-repeat byte
(the row is emitted count+1 times). Within a row, a code byte c means:
  0..127   -> repeat the next pixel c+1 times
  128      -> fill the rest of the row with the "blank" colour (URF: white;
              PWG readers treat it the same way)
  129..255 -> 257-c literal pixels follow
"""
from __future__ import annotations


class RasterError(ValueError):
    pass


def decode_rows(data: bytes, pos: int, width: int, height: int, bpp_bytes: int,
                blank: bytes) -> tuple[bytearray, int]:
    """Decode `height` rows of `width` pixels (bpp_bytes each). Returns (samples, new pos)."""
    row_len = width * bpp_bytes
    out = bytearray()
    n = len(data)
    rows_done = 0
    while rows_done < height:
        if pos >= n:
            # a truncated page: pad with blank rows rather than fail the whole job
            out += blank * width * (height - rows_done)
            break
        repeat = data[pos] + 1
        pos += 1
        row = bytearray()
        px = 0
        while px < width:
            if pos >= n:
                raise RasterError("raster data ended mid-row")
            code = data[pos]
            pos += 1
            if code == 128:
                row += blank * (width - px)
                px = width
            elif code < 128:
                cnt = code + 1
                pixel = data[pos:pos + bpp_bytes]
                if len(pixel) != bpp_bytes:
                    raise RasterError("raster data ended mid-pixel")
                pos += bpp_bytes
                cnt = min(cnt, width - px)
                row += pixel * cnt
                px += cnt
            else:
                cnt = 257 - code
                take = min(cnt, width - px)
                chunk = data[pos:pos + cnt * bpp_bytes]
                if len(chunk) != cnt * bpp_bytes:
                    raise RasterError("raster data ended mid-literal")
                pos += cnt * bpp_bytes
                row += chunk[:take * bpp_bytes]
                px += take
        if len(row) != row_len:
            row = (row + blank * width)[:row_len]
        repeat = min(repeat, height - rows_done)
        out += bytes(row) * repeat
        rows_done += repeat
    return out, pos


def unpack_1bit(samples: bytes, width: int, height: int, one_is_black: bool) -> bytes:
    """1 bit per pixel packed rows -> 8-bit gray."""
    row_bytes = (width + 7) // 8
    out = bytearray(width * height)
    for y in range(height):
        row = samples[y * row_bytes:(y + 1) * row_bytes]
        base = y * width
        for x in range(width):
            bit = (row[x >> 3] >> (7 - (x & 7))) & 1 if (x >> 3) < len(row) else 0
            black = bit if one_is_black else (1 - bit)
            out[base + x] = 0 if black else 255
    return bytes(out)


def cmyk_to_rgb(samples: bytes) -> bytes:
    out = bytearray(len(samples) // 4 * 3)
    j = 0
    for i in range(0, len(samples) - 3, 4):
        c, m, y, k = samples[i], samples[i + 1], samples[i + 2], samples[i + 3]
        out[j] = (255 - c) * (255 - k) // 255
        out[j + 1] = (255 - m) * (255 - k) // 255
        out[j + 2] = (255 - y) * (255 - k) // 255
        j += 3
    return bytes(out)


def take_high_bytes(samples: bytes, channels: int) -> bytes:
    """16-bit big-endian samples -> 8-bit."""
    return bytes(samples[i] for i in range(0, len(samples), 2))
