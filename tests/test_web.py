import json

from sqlalchemy import select

from .conftest import make_pdf, wait_for

PANCAKES = ["Fluffy Pancakes\n\nIngredients\n2 cups flour\n1 tbsp sugar\n2 eggs\n\nDirections\nMix and fry for 3 minutes per side.",
            "Page two: serve with maple syrup."]


def _wait_done(client):
    def pred():
        html = client.get("/partials/jobs").text
        return "done" in html and "running" not in html and "queued" not in html
    wait_for(pred)


def test_upload_to_library(client, library, tmp_path):
    pdf = make_pdf(tmp_path / "pancakes.pdf", PANCAKES)
    with pdf.open("rb") as fh:
        r = client.post("/capture/upload", files=[("files", ("Fluffy_Pancakes.pdf", fh, "application/pdf"))])
    assert r.status_code == 200
    _wait_done(client)

    home = client.get("/").text
    assert "Fluffy Pancakes" in home
    # search hits the PDF body text via FTS
    assert "Fluffy Pancakes" in client.get("/partials/cards", params={"q": "maple"}).text
    assert "Fluffy Pancakes" not in client.get("/partials/cards", params={"q": "lasagna"}).text

    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe
    with session_scope() as s:
        rec = s.scalars(select(Recipe)).first()
        rid = rec.id
        assert rec.page_count == 2
        assert rec.status == "needs_review"      # no LLM in tests
        assert rec.thumb_asset_id is not None
        assert (library.assets_dir / rec.pdf_asset.rel_path).is_file()

    detail = client.get(f"/recipes/{rid}").text
    assert "Read PDF" in detail

    # reader page + range-capable file route
    assert "RecipeReader" in client.get(f"/recipes/{rid}/read").text
    f = client.get(f"/recipes/{rid}/file", headers={"Range": "bytes=0-9"})
    assert f.status_code == 206 and f.content.startswith(b"%PDF")
    full = client.get(f"/recipes/{rid}/file")
    assert full.headers["content-type"] == "application/pdf"
    assert "accept-ranges" in {k.lower() for k in full.headers}
    assert "attachment" in client.get(f"/recipes/{rid}/file?dl=1").headers["content-disposition"]

    # progress + bookmarks
    p = client.post(f"/recipes/{rid}/progress", json={"page": 2, "page_count": 2}).json()
    assert p["last_page"] == 2
    assert client.post(f"/recipes/{rid}/progress", json={"page": 99}).json()["last_page"] == 2
    b = client.post(f"/recipes/{rid}/bookmarks", json={"page": 2, "label": "Syrup"}).json()
    assert b["created"] and b["bookmarks"][0]["label"] == "Syrup"
    bid = b["bookmark"]["id"]
    b2 = client.post(f"/recipes/{rid}/bookmarks", json={"page": 2}).json()
    assert not b2["created"]
    assert client.post(f"/recipes/{rid}/bookmarks/{bid}/rename", json={"label": "Toppings"}).json()["bookmarks"][0]["label"] == "Toppings"
    assert "Toppings" in client.get(f"/recipes/{rid}").text
    assert client.post(f"/recipes/{rid}/bookmarks/{bid}/delete").json()["bookmarks"] == []

    # edit form -> structured recipe, status ready
    r = client.post(f"/recipes/{rid}/edit", data={
        "title": "Fluffy Pancakes", "servings": "4", "prep_min": "10", "cook_min": "15",
        "ingredients": "2 cups flour\n1 tbsp sugar\n2 eggs\nTo serve:\nmaple syrup",
        "steps": "Mix everything.\nFry 3 minutes per side.", "course": "breakfast", "cuisine": "american",
        "tags": "kids, weekend", "source_url": "https://www.example.com/pancakes?utm_source=x",
    }, follow_redirects=False)
    assert r.status_code == 303
    detail = client.get(f"/recipes/{rid}").text
    assert "Ingredients" in detail and "maple syrup" in detail and "breakfast" in detail
    with session_scope() as s:
        rec = s.get(Recipe, rid)
        assert rec.status == "ready" and rec.total_min == 25
        assert rec.source_url_norm == "https://example.com/pancakes"
        assert [i.group_name for i in rec.ingredients] == [None, None, None, "To serve"]
        assert rec.steps[1].minutes == 3
    assert "Fluffy Pancakes" in client.get("/partials/cards", params={"q": "weekend"}).text
    # categories: ticked on the edit page, auto-assigned via settings, browsable in the sidebar
    r = client.post(f"/recipes/{rid}/edit", data={"title": "Fluffy Pancakes", "ingredients": "2 cups flour\n2 eggs", "steps": "Fry.",
                                                  "course": "breakfast", "cuisine": "american", "tags": "kids, weekend",
                                                  "_categories_present": "1", "categories": ["Breakfast", "Vegetarian"]}, follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        assert sorted(t.name for t in s.get(Recipe, rid).tags_of("category")) == ["Breakfast", "Vegetarian"]
    assert "Fluffy Pancakes" in client.get("/partials/cards", params={"tag": "category:Breakfast"}).text
    assert "Fluffy Pancakes" not in client.get("/partials/cards", params={"tag": "category:Seafood"}).text
    home = client.get("/").text
    assert "category:Seafood" in home and "category:Dessert" in home
    assert client.post("/settings/categorize", follow_redirects=False).status_code == 303
    assert "Fluffy Pancakes" in client.get("/partials/cards", params={"tag": "course:breakfast"}).text

    # cover picker: page render, no photo, upload
    assert client.get(f"/recipes/{rid}/page-image/0").headers["content-type"] == "image/png"
    assert client.get(f"/recipes/{rid}/page-image/9").status_code == 404
    assert "Photo" in client.get(f"/recipes/{rid}/edit").text
    r = client.post(f"/recipes/{rid}/cover", data={"choice": "page:0"}, follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        rec = s.get(Recipe, rid)
        assert rec.cover_asset_id is not None
        cover_a = rec.cover_asset_id
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False); pix.clear_with(90)
    r = client.post(f"/recipes/{rid}/cover", data={"choice": "upload"}, files={"file": ("me.png", pix.tobytes("png"), "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        rec = s.get(Recipe, rid)
        assert rec.cover_asset_id is not None and rec.cover_asset_id != cover_a
    client.post(f"/recipes/{rid}/cover", data={"choice": "none"}, follow_redirects=False)
    with session_scope() as s:
        assert s.get(Recipe, rid).cover_asset_id is None

    # favorite toggle (htmx) and duplicate upload
    assert "♥" in client.post(f"/recipes/{rid}/favorite", headers={"HX-Request": "true"}).text
    with pdf.open("rb") as fh:
        client.post("/capture/upload", files=[("files", ("again.pdf", fh, "application/pdf"))])
    _wait_done(client)
    assert "already in library" in client.get("/partials/jobs").text
    with session_scope() as s:
        assert len(list(s.scalars(select(Recipe)))) == 1

    # delete hides it
    client.post(f"/recipes/{rid}/delete", follow_redirects=False)
    assert client.get(f"/recipes/{rid}").status_code == 404
    assert "Fluffy Pancakes" not in client.get("/").text


def test_inbox_watch_and_image_upload(client, library, tmp_path):
    import pymupdf
    pdf = make_pdf(tmp_path / "Grandma_Soup.pdf", ["Grandma's Soup\n1 onion\n2 carrots"])
    (library.inbox_dir / "Grandma_Soup.pdf").write_bytes(pdf.read_bytes())
    _wait_done(client)
    assert "Grandma" in client.get("/").text
    assert not (library.inbox_dir / "Grandma_Soup.pdf").exists()
    assert (library.inbox_dir / "processed" / "Grandma_Soup.pdf").exists()

    # a photo becomes a one-page PDF
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300), False)
    pix.clear_with(200)
    png = pix.tobytes("png")
    r = client.post("/capture/upload", files=[("files", ("scan.png", png, "image/png"))])
    assert r.status_code == 200
    _wait_done(client)
    assert client.get("/partials/jobs").text.count("done") >= 2


def test_startup_seeds_and_backfills_categories(library, tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import select as sel
    from recipelib.app import create_app
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe, Tag
    with TestClient(create_app()) as c:
        with session_scope() as s:
            assert s.scalar(sel(Tag).where(Tag.kind == "category", Tag.name == "Seafood")) is not None
        pdf = make_pdf(tmp_path / "s.pdf", ["Shrimp Tacos"])
        with pdf.open("rb") as fh:
            c.post("/capture/upload", files=[("files", ("Shrimp_Tacos.pdf", fh, "application/pdf"))])
        wait_for(lambda: "done" in c.get("/partials/jobs").text and "running" not in c.get("/partials/jobs").text)
        with session_scope() as s:
            r = s.scalars(sel(Recipe)).first()
            rid = r.id
            r.tags = [t for t in r.tags if t.kind != "category"]     # pretend it predates categories
            from recipelib.domain.recipes import replace_ingredients
            from recipelib.extract.normalize import parse_ingredient_block
            replace_ingredients(s, r, parse_ingredient_block("1 lb shrimp\n8 tortillas"))
    # a second start (same library) backfills it
    with TestClient(create_app()) as c:
        with session_scope() as s:
            names = sorted(t.name for t in s.get(Recipe, rid).tags_of("category"))
        assert "Seafood" in names and "Dinner" in names


def test_missing_upload_fails_cleanly(client, library):
    from recipelib.capture.queue import get_queue
    get_queue().enqueue("upload", pdf_path=str(library.library_dir / "tmp" / "gone.pdf"), title_hint="Gone")
    wait_for(lambda: "failed" in client.get("/partials/jobs").text)
    html = client.get("/partials/jobs").text
    assert "no longer on disk" in html and "Retry" not in html


def test_pwa_files(client):
    assert client.get("/sw.js").headers["content-type"].startswith("application/javascript")
    m = client.get("/static/manifest.webmanifest")
    assert m.status_code == 200 and "Recipe Library" in m.text
    assert client.get("/static/icons/icon-512.png").headers["content-type"] == "image/png"
    assert 'rel="manifest"' in client.get("/").text


def test_settings_and_capture_pages(client):
    assert client.get("/capture").status_code == 200
    assert client.get("/settings").status_code == 200
    r = client.post("/capture/url", data={"url": "example.com/recipe"}, follow_redirects=False)
    assert r.status_code == 303
    # the url job fails fast without playwright fetch in tests? it runs; just make sure the page lists it
    assert "example.com" in client.get("/partials/jobs").text
