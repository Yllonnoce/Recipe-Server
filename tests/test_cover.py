"""Cover candidates on flat pages (print jobs, scans): the photo is found inside the page picture."""
import random

import pymupdf
from PIL import Image, ImageDraw

from recipelib.capture import pdf as P

W, H, DPI = 850, 1100, 100          # a letter page at 100 dpi
PHOTO = (500, 80, 760, 260)         # where the photo sits, in pixels


def _page(photo: bool) -> Image.Image:
    rnd = random.Random(7)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for y in range(90, 1000, 22):                      # "text": short dark dashes on white
        for x in range(60, 440 if y < 280 else 780, 14):
            d.rectangle((x, y, x + 7, y + 8), fill=(30, 30, 30))
    d.rectangle((60, 300, 780, 304), fill="black")     # a heading rule
    if photo:
        x0, y0, x1, y1 = PHOTO
        for y in range(y0, y1, 4):                     # busy, colourful, edge to edge
            for x in range(x0, x1, 4):
                d.rectangle((x, y, x + 3, y + 3), fill=(rnd.randrange(60, 220), rnd.randrange(40, 160), rnd.randrange(20, 120)))
        d.ellipse((x0 + 20, y0 + 10, x0 + 90, y0 + 60), fill="white")   # icing / a plate
    return im


def _pdf(tmp_path, pages):
    f = tmp_path / "job.pdf"
    f.write_bytes(P.pixmaps_to_pdf([(W, H, 3, im.tobytes(), DPI) for im in pages]))
    return f


def test_photo_is_found_inside_a_printed_page(tmp_path):
    f = _pdf(tmp_path, [_page(True), _page(False)])
    cands = P.candidate_images(f, max_pages=2)
    assert len(cands) == 1                              # the photo: not the whole page, not page 2
    c = cands[0]
    assert c["page"] == 0 and c["key"].startswith("clip:0:")
    x0, y0, x1, y1 = (int(v) * DPI / 72 for v in c["key"].split(":")[2:])
    for got, want in zip((x0, y0, x1, y1), PHOTO):
        assert abs(got - want) <= 12                    # within a cell and a half
    pix = pymupdf.Pixmap(P.candidate_png(f, c["key"]))
    assert 600 <= pix.width <= P.COVER_WIDTH and abs(pix.width / pix.height - 260 / 180) < 0.1
    assert P.largest_image_on_page(f) is not None


def test_text_only_printed_page_has_no_candidates(tmp_path):
    f = _pdf(tmp_path, [_page(False)])
    assert P.candidate_images(f) == []


def test_candidate_png_rejects_bad_keys(tmp_path):
    f = _pdf(tmp_path, [_page(True)])
    for key in ("clip:9:0:0:100:100", "clip:0:1:2", "clip:0:a:b:c:d", "page:0", "clip:0:50:50:50:50"):
        assert P.candidate_png(f, key) is None
