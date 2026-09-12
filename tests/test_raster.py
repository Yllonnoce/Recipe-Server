import pytest

from recipelib.ipp.raster import pwg, urf
from recipelib.ipp.raster.rle import RasterError
from recipelib.ipp.raster.to_pdf import convert_bytes, sniff
from .tools import rasterenc as E


def test_pwg_gray_and_rgb_roundtrip():
    g = E.gradient(37, 11, 1)
    rgb = E.gradient(20, 9, 3)
    data = E.pwg([(37, 11, 1, g), (20, 9, 3, rgb)], dpi=200)
    pages = pwg.decode(data)
    assert len(pages) == 2
    assert (pages[0].width, pages[0].height, pages[0].channels, pages[0].dpi) == (37, 11, 1, 200)
    assert pages[0].samples == g
    assert pages[1].channels == 3 and pages[1].samples == rgb


def test_pwg_line_repeat_and_runs():
    # solid rows compress to line repeats + single runs
    w, h = 300, 40
    samples = bytes([200]) * (w * h)
    data = E.pwg([(w, h, 1, samples)])
    assert len(data) < 1796 + 4 + 50
    p = pwg.decode(data)[0]
    assert p.samples == samples


def test_pwg_black_1bit():
    # 16 px wide, 2 rows: first row all black, second row alternating
    rows = bytes([0xFF, 0xFF, 0xAA, 0xAA])
    data = E.pwg_1bit(16, 2, rows, cspace=3)
    p = pwg.decode(data)[0]
    assert p.channels == 1 and p.width == 16
    assert p.samples[:16] == bytes([0] * 16)
    assert p.samples[16:32] == bytes([0, 255] * 8)


def test_pwg_white_space_black_is_inverted():
    samples = bytes([0, 255]) * 8
    data = E.pwg([(16, 1, 1, samples)], cspace=3)     # K: 0 = no ink = white
    p = pwg.decode(data)[0]
    assert p.samples == bytes([255, 0]) * 8


def test_urf_roundtrip_and_eol_fill():
    rgb = E.gradient(33, 7, 3)
    data = E.urf([(33, 7, 3, rgb)], dpi=300)
    p = urf.decode(data)[0]
    assert (p.width, p.height, p.channels, p.dpi) == (33, 7, 3, 300)
    assert p.samples == rgb
    q = urf.decode(E.urf_with_eol(10, 5))[0]
    assert q.samples[:10] == bytes([0] + [255] * 9)
    assert q.samples[40:50] == bytes([0] + [255] * 9)


def test_sniff_and_convert_to_pdf():
    import pymupdf
    assert sniff(b"%PDF-1.7") == "application/pdf"
    assert sniff(b"RaS2" + b"\x00" * 12) == "image/pwg-raster"
    assert sniff(b"UNIRAST\x00\x00\x00\x00\x01") == "image/urf"
    data = E.urf([(120, 80, 1, E.gradient(120, 80, 1))], dpi=100)
    out = convert_bytes(data)
    assert out.startswith(b"%PDF")
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert doc.page_count == 1
    assert abs(doc[0].rect.width - 120 * 72 / 100) < 1
    with pytest.raises(RasterError):
        convert_bytes(b"garbage garbage")
