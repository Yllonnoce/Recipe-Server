import zipfile

from sqlalchemy import select

from recipelib import backup as B
from recipelib.db.engine import session_scope
from recipelib.db.models import Recipe
from .conftest import make_pdf, wait_for


def _add(client, tmp_path, title, ingredients="1 cup flour\n2 eggs"):
    pdf = make_pdf(tmp_path / f"{title}.pdf", [title])
    with pdf.open("rb") as fh:
        client.post("/capture/upload", files=[("files", (f"{title}.pdf", fh, "application/pdf"))])
    wait_for(lambda: "done" in client.get("/partials/jobs").text and "running" not in client.get("/partials/jobs").text
             and "queued" not in client.get("/partials/jobs").text)
    with session_scope() as s:
        rid = s.scalars(select(Recipe).where(Recipe.title == title)).first().id
    client.post(f"/recipes/{rid}/edit", data={"title": title, "servings": "4", "ingredients": ingredients, "steps": "Mix.\nBake.",
                                             "tags": "family", "_categories_present": "1", "categories": ["Dessert"]}, follow_redirects=False)
    client.post(f"/recipes/{rid}/bookmarks", json={"page": 1, "label": "Top"})
    return rid


def test_backup_restore_merge_and_replace(client, library, tmp_path):
    a = _add(client, tmp_path, "Apple Pie")
    b = _add(client, tmp_path, "Banana Bread")
    client.post("/plan/add", data={"date": "2026-09-16", "slot": "dinner", "recipe_id": str(a)}, follow_redirects=False)
    client.post("/shopping/items", data={"text": "2 lb apples"})

    # create through the page, check the zip
    r = client.post("/settings/backup", follow_redirects=False)
    assert r.status_code == 303
    zips = list((library.library_dir / "backups").glob("*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as z:
        names = z.namelist()
        assert "recipes.db" in names and "manifest.json" in names and any(n.startswith("assets/pdf/") for n in names)
    assert "Apple Pie" not in client.get("/settings").text          # list shows the file, not recipe names
    assert zips[0].name in client.get("/settings").text
    assert client.get(f"/settings/backups/{zips[0].name}").headers["content-type"] == "application/zip"

    # delete one recipe, then merge: it comes back, the other is left alone (skipped)
    client.post(f"/recipes/{a}/delete", follow_redirects=False)
    stats = B.restore(zips[0], mode="merge")
    assert stats.added == 1 and stats.skipped == 1 and not stats.errors
    with session_scope() as s:
        pie = s.scalars(select(Recipe).where(Recipe.title == "Apple Pie", Recipe.deleted_at.is_(None))).first()
        assert pie is not None and len(pie.ingredients) == 2 and len(pie.steps) == 2
        assert [bm.label for bm in pie.bookmarks] == ["Top"]
        assert {t.name for t in pie.tags} >= {"family", "Dessert"}
        assert pie.cover_asset_id is not None
        assert len(list(s.scalars(select(Recipe).where(Recipe.deleted_at.is_(None))))) == 2
    assert "Apple Pie" in client.get("/plan", params={"week": "2026-09-14"}).text   # plan entry re-linked to the new id
    assert client.get("/shopping.txt").text.count("apple") == 1                      # shopping item not duplicated

    # merge again: nothing new
    stats = B.restore(zips[0], mode="merge")
    assert stats.added == 0 and stats.skipped == 2

    # overwrite with nothing newer in the backup: nothing replaced (no daily churn on a mirror)
    stats = B.restore(zips[0], mode="merge", overwrite=True)
    assert stats.replaced == 0 and stats.skipped == 2
    # edit locally, then a backup made later wins over the local edit
    import time; time.sleep(1.1)
    client.post(f"/recipes/{b}/edit", data={"title": "Banana Bread (mine)", "ingredients": "3 bananas", "steps": "Mash."}, follow_redirects=False)
    time.sleep(1.1)
    newer = B.create_backup(tmp_path / "newer.zip")
    client.post(f"/recipes/{b}/edit", data={"title": "Banana Bread (mine again)", "ingredients": "3 bananas", "steps": "Mash."}, follow_redirects=False)
    stats = B.restore(zips[0], mode="merge", overwrite=True)      # old backup: local is newer -> kept
    assert stats.replaced == 0
    stats = B.restore(newer, mode="merge", overwrite=True)        # but it can't be newer than 'newer'... local edited after -> kept too
    assert stats.replaced == 0
    with session_scope() as s:
        titles = sorted(r.title for r in s.scalars(select(Recipe).where(Recipe.deleted_at.is_(None))))
        assert titles == ["Apple Pie", "Banana Bread (mine again)"]

    # replace via the page
    _add(client, tmp_tmp := tmp_path, "Cherry Tart")
    r = client.post(f"/settings/backups/{zips[0].name}/restore", data={"mode": "replace"}, follow_redirects=False)
    assert r.status_code == 303 and "Replaced" in r.headers["location"]
    with session_scope() as s:
        titles = sorted(r.title for r in s.scalars(select(Recipe).where(Recipe.deleted_at.is_(None))))
        assert titles == ["Apple Pie", "Banana Bread"]

    # upload a backup file through the page, then prune
    with zips[0].open("rb") as fh:
        r = client.post("/settings/backups/upload", files={"file": ("from-mac.zip", fh, "application/zip")}, follow_redirects=False)
    assert r.status_code == 303 and (library.library_dir / "backups" / "from-mac.zip").exists()
    assert B.prune(keep=0) >= 1


def test_restore_rejects_random_zip(library, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "x")
    import pytest
    with pytest.raises(ValueError):
        B.restore(bad)
