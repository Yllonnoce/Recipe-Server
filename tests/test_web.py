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
    assert "Fluffy Pancakes" in client.get("/partials/cards", params={"tag": "course:breakfast"}).text

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


def test_settings_and_capture_pages(client):
    assert client.get("/capture").status_code == 200
    assert client.get("/settings").status_code == 200
    r = client.post("/capture/url", data={"url": "example.com/recipe"}, follow_redirects=False)
    assert r.status_code == 303
    # the url job fails fast without playwright fetch in tests? it runs; just make sure the page lists it
    assert "example.com" in client.get("/partials/jobs").text
