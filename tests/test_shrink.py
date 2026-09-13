import io

from sqlalchemy import select

from recipelib.capture import shrink
from recipelib.db.models import Asset, Recipe
from .conftest import make_pdf, wait_for


def _big_photo(w=4000, h=3000) -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (200, 120, 60))
    d = ImageDraw.Draw(im)
    for i in range(0, w, 37):
        d.line([(i, 0), (w - i, h)], fill=(i % 255, 90, 160), width=3)
    buf = io.BytesIO(); im.save(buf, format="PNG"); return buf.getvalue()


def test_shrink_image_bytes(library):
    data = _big_photo()
    out, mime = shrink.shrink_image_bytes(data)
    from PIL import Image
    im = Image.open(io.BytesIO(out))
    assert mime == "image/jpeg" and max(im.size) == 2000 and len(out) < len(data)
    small = _big_photo(300, 200)
    out2, _ = shrink.shrink_image_bytes(small)
    assert len(out2) <= len(small)


def test_shrink_pdf_with_big_image(library, tmp_path):
    import pymupdf
    doc = pymupdf.open(); page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(50, 50, 550, 425), stream=_big_photo())   # 4000 px across 500 pt = ~576 dpi
    p = tmp_path / "big.pdf"; doc.save(p); doc.close()
    before, after = shrink.shrink_pdf(p)
    assert after < before * 0.5
    assert pymupdf.open(p).page_count == 1


def test_upload_is_shrunk_and_cover_is_jpeg(client, library):
    r = client.post("/capture/upload", files=[("files", ("photo.png", _big_photo(), "image/png"))])
    assert r.status_code == 200
    wait_for(lambda: "done" in client.get("/partials/jobs").text and "running" not in client.get("/partials/jobs").text
             and "queued" not in client.get("/partials/jobs").text)
    from recipelib.db.engine import session_scope
    with session_scope() as s:
        rec = s.scalars(select(Recipe)).first()
        assert rec.pdf_asset.bytes < 1_500_000                    # 4000x3000 stored as a ~2000 px JPEG page
        cover = s.get(Asset, rec.cover_asset_id)
        assert cover.mime == "image/jpeg" and cover.rel_path.endswith(".jpg")
        assert client.get(f"/assets/{cover.id}").headers["content-type"] == "image/jpeg"


def test_shrink_library_renames_assets(client, library, tmp_path, monkeypatch):
    # store an un-shrunk PDF by turning shrinking off for the capture, then run the library pass
    monkeypatch.setattr(library, "shrink_files", False)
    import pymupdf
    doc = pymupdf.open(); page = doc.new_page(width=612, height=792)
    page.insert_image(pymupdf.Rect(50, 50, 550, 425), stream=_big_photo())
    p = tmp_path / "big.pdf"; doc.save(p); doc.close()
    with p.open("rb") as fh:
        client.post("/capture/upload", files=[("files", ("big.pdf", fh, "application/pdf"))])
    wait_for(lambda: "done" in client.get("/partials/jobs").text and "running" not in client.get("/partials/jobs").text)
    from recipelib.db.engine import session_scope
    with session_scope() as s:
        a = s.scalars(select(Asset).where(Asset.kind == "pdf")).first()
        old_rel, old_bytes = a.rel_path, a.bytes
    monkeypatch.setattr(library, "shrink_files", True)
    t = shrink.shrink_library()
    assert t["changed"] >= 1 and t["after"] < t["before"]
    with session_scope() as s:
        a = s.scalars(select(Asset).where(Asset.kind == "pdf")).first()
        assert a.rel_path != old_rel and a.bytes < old_bytes
        assert (library.assets_dir / a.rel_path).is_file() and not (library.assets_dir / old_rel).exists()
        rid = s.scalars(select(Recipe)).first().id
    assert client.get(f"/recipes/{rid}/file").status_code == 200      # the recipe still opens its PDF
