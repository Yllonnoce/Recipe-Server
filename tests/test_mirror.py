from pathlib import Path

from sqlalchemy import select

from recipelib import backup as B
from recipelib import mirror
from recipelib.db.models import Recipe
from .conftest import make_pdf, wait_for


def _add(client, tmp_path, title):
    pdf = make_pdf(tmp_path / f"{title}.pdf", [title])
    with pdf.open("rb") as fh:
        client.post("/capture/upload", files=[("files", (f"{title}.pdf", fh, "application/pdf"))])
    wait_for(lambda: "done" in client.get("/partials/jobs").text and "running" not in client.get("/partials/jobs").text
             and "queued" not in client.get("/partials/jobs").text)


def test_primary_api_and_sync(client, library, tmp_path, monkeypatch):
    _add(client, tmp_path, "Apple Pie")
    _add(client, tmp_path, "Banana Bread")
    info = client.get("/api/backup/info").json()
    assert info["app"] == "recipelib" and info["recipes"] == 2
    r = client.get("/api/backup/latest")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and r.content[:2] == b"PK"
    zips = list((library.library_dir / "backups").glob("recipelib-*.zip"))
    assert len(zips) == 1
    assert client.get("/api/backup/latest").content == r.content       # reused within 30 minutes

    # the "secondary" is this same library after losing a recipe; the pull is stubbed with the zip above
    from recipelib.db.engine import session_scope
    with session_scope() as s:
        rid = s.scalars(select(Recipe).where(Recipe.title == "Apple Pie")).first().id
    client.post(f"/recipes/{rid}/delete", follow_redirects=False)
    monkeypatch.setattr(mirror, "pull_backup", lambda url, dest_dir, timeout=600.0: zips[0])
    msg = mirror.sync_once("http://primary.local", "merge")
    assert "1 recipe added" in msg and mirror.STATE["last_ok"] is True
    with session_scope() as s:
        titles = sorted(r.title for r in s.scalars(select(Recipe).where(Recipe.deleted_at.is_(None))))
        assert titles == ["Apple Pie", "Banana Bread"]


def test_mirror_settings_panel(client, library, monkeypatch):
    html = client.get("/settings").text
    assert "Backup server" in html
    r = client.post("/settings/mirror", data={"mirror_of": "macm5.local", "mirror_interval_hours": "12", "mirror_mode": "merge"}).text
    assert "saved" in r
    from recipelib.config import config_path, get_settings
    assert 'mirror_of = "http://macm5.local"' in config_path().read_text()
    assert get_settings().mirror_interval_hours == 12.0
    t = client.post("/settings/mirror/test", data={"mirror_of": "http://127.0.0.1:1"}).text
    assert "not a reachable Recipe Library" in t
    monkeypatch.setattr(mirror, "primary_info", lambda url, timeout=5.0: {"version": "0.1.0", "recipes": 7})
    t = client.post("/settings/mirror/test", data={"mirror_of": "http://macm5.local"}).text
    assert "with 7 recipes" in t
