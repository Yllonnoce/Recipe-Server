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
    assert "not a reachable Recipe Library" in t and "nothing is listening" in t
    t = client.post("/settings/mirror/test", data={"mirror_of": "http://no-such-host-xyz"}).text
    assert "could not be looked up" in t
    monkeypatch.setattr(mirror, "primary_info", lambda url, timeout=5.0: {"version": "0.1.0", "recipes": 7})
    t = client.post("/settings/mirror/test", data={"mirror_of": "http://macm5.local"}).text
    assert "with 7 recipes" in t


def test_receive_endpoint_and_token(client, library, tmp_path, monkeypatch):
    _add(client, tmp_path, "Cherry Tart")
    zip_bytes = client.get("/api/backup/latest").content
    from recipelib.db.engine import session_scope
    with session_scope() as s:
        rid = s.scalars(select(Recipe).where(Recipe.title == "Cherry Tart")).first().id
    client.post(f"/recipes/{rid}/delete", follow_redirects=False)
    # a sender delivers the backup: the deleted recipe comes back
    r = client.post("/api/backup/receive", files={"file": ("from-mac.zip", zip_bytes, "application/zip")}, data={"mode": "merge"})
    assert r.status_code == 200 and r.json()["ok"] and "1 recipe added" in r.json()["summary"]
    with session_scope() as s:
        assert s.scalars(select(Recipe).where(Recipe.title == "Cherry Tart", Recipe.deleted_at.is_(None))).first() is not None
    assert "received a backup" in client.get("/partials/mirror").text
    # with a token set, senders must know it
    monkeypatch.setattr(library, "sync_token", "s3cret")
    r = client.post("/api/backup/receive", files={"file": ("x.zip", zip_bytes, "application/zip")})
    assert r.status_code == 403
    r = client.post("/api/backup/receive", files={"file": ("x.zip", zip_bytes, "application/zip")}, headers={"X-Recipelib-Token": "s3cret"})
    assert r.status_code == 200
    # garbage is rejected cleanly
    r = client.post("/api/backup/receive", files={"file": ("x.zip", b"not a zip", "application/zip")}, headers={"X-Recipelib-Token": "s3cret"})
    assert r.status_code == 400


def test_push_panel_and_push_once(client, library, monkeypatch):
    assert "other way round" in client.get("/settings").text
    assert "Backup server address" in client.get("/partials/push").text
    r = client.post("/settings/push", data={"push_to": "ubuntu.local:8000", "push_interval_hours": "6", "push_mode": "merge", "sync_token": "abc"}).text
    assert "saved" in r
    from recipelib.config import config_path, get_settings
    assert 'push_to = "http://ubuntu.local:8000"' in config_path().read_text() and get_settings().sync_token == "abc"
    t = client.post("/settings/push/test", data={"push_to": "http://127.0.0.1:1"}).text
    assert "nothing is listening" in t
    # push_once end to end with the HTTP calls stubbed
    from recipelib import mirror
    sent = {}
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "summary": "2 recipes added"}
    import httpx
    monkeypatch.setattr(mirror, "primary_info", lambda url, timeout=5.0: {"app": "recipelib", "version": "0.1.0", "recipes": 0})
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.update(url=url, headers=kw.get("headers")) or FakeResp())
    msg = mirror.push_once("http://backup.local:8000", "merge")
    assert "2 recipes added" in msg and sent["url"].endswith("/api/backup/receive") and sent["headers"]["X-Recipelib-Token"] == "abc"
    assert mirror.PUSH["last_ok"] is True
