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


def _png(seed=1, size=(300, 200)) -> bytes:
    """A photo-like (noisy) PNG, which JPEG really does shrink."""
    import random

    from PIL import Image
    rnd = random.Random(seed)
    buf = io.BytesIO()
    Image.frombytes("RGB", size, bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * 3))).save(buf, "PNG")
    return buf.getvalue()


def _asset(s, library, kind, data: bytes, ext: str) -> int:
    from recipelib.capture.pdf import sha256_bytes
    sha = sha256_bytes(data)
    rel = f"{kind}/{sha[:2]}/{sha}{ext}"
    (library.assets_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (library.assets_dir / rel).write_bytes(data)
    a = Asset(kind=kind, rel_path=rel, sha256=sha, bytes=len(data), mime="image/png" if ext == ".png" else "image/jpeg")
    s.add(a)
    s.flush()
    return a.id


def test_a_picture_that_shrinks_into_one_already_stored_is_merged_into_it(client, library):
    """An old PNG thumbnail whose JPEG is already stored (a later re-extract made it):
    shrinking it must not fail on the unique (sha256, kind) rule, nor lose a file."""
    from recipelib.db.engine import session_scope
    png = _png()
    with session_scope() as s:
        old = _asset(s, library, "thumb", png, ".png")
        new = _asset(s, library, "thumb", shrink.to_jpeg(png), ".jpg")
        other = _asset(s, library, "cover", _png(seed=2), ".png")   # processed after the clash
        r = Recipe(title="Stew", status="ready", thumb_asset_id=old, cover_asset_id=other)
        s.add(r)
        s.flush()
        rid = r.id
        old_rel = s.get(Asset, old).rel_path
    shrink.shrink_library()                         # used to raise IntegrityError here
    with session_scope() as s:
        r = s.get(Recipe, rid)
        assert r.thumb_asset_id == new and s.get(Asset, old) is None
        assert (library.assets_dir / s.get(Asset, new).rel_path).is_file()
        assert not (library.assets_dir / old_rel).exists()
        assert s.get(Asset, other).mime == "image/jpeg"            # the run carried on past the clash
    assert client.get(f"/assets/{new}").status_code == 200


def test_a_missing_thumbnail_is_redrawn_from_the_pdf(client, library, tmp_path):
    """The old bug deleted a thumbnail file and then failed; shrink puts it back."""
    from recipelib.db.engine import session_scope
    pdf = make_pdf(tmp_path / "soup.pdf", ["Tomato soup"])
    with pdf.open("rb") as fh:
        client.post("/capture/upload", files=[("files", ("soup.pdf", fh, "application/pdf"))])
    wait_for(lambda: "done" in client.get("/partials/jobs").text and "running" not in client.get("/partials/jobs").text)
    with session_scope() as s:
        r = s.scalars(select(Recipe)).first()
        rid, thumb = r.id, s.get(Asset, r.thumb_asset_id)
        (library.assets_dir / thumb.rel_path).unlink()
    t = shrink.shrink_library()
    assert t.get("repaired") == 1
    with session_scope() as s:
        r = s.get(Recipe, rid)
        assert (library.assets_dir / s.get(Asset, r.thumb_asset_id).rel_path).is_file()
