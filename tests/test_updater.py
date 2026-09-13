import subprocess
from pathlib import Path

import pytest

from recipelib import updater


def _git(cwd: Path, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@x", "PATH": "/usr/bin:/bin:/usr/local/bin"})


@pytest.fixture()
def repos(tmp_path, monkeypatch):
    """A bare 'origin', a clone that plays the installed app, and a second clone that pushes a new version."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    app = tmp_path / "app"
    _git(tmp_path, "clone", "-q", str(origin), str(app))
    (app / "pyproject.toml").write_text("[project]\nname='x'\n")
    (app / "VERSION").write_text("1\n")
    _git(app, "add", "-A"); _git(app, "commit", "-qm", "v1"); _git(app, "push", "-q", "-u", "origin", "main")
    dev = tmp_path / "dev"
    _git(tmp_path, "clone", "-q", str(origin), str(dev))
    monkeypatch.setattr(updater, "repo_root", lambda: app)
    return app, dev


def test_check_and_update(repos, monkeypatch):
    app, dev = repos
    assert updater.current().is_git and updater.current().branch == "main"
    r = updater.check()
    assert r["error"] is None and r["behind"] == 0
    # someone pushes a new version
    (dev / "VERSION").write_text("2\n")
    _git(dev, "commit", "-qam", "v2"); _git(dev, "push", "-q")
    r = updater.check()
    assert r["behind"] == 1 and updater.STATE["behind"] == 1
    # update without touching pip/playwright/db, no restart
    monkeypatch.setattr(updater, "migrate_db", lambda: None, raising=False)
    import recipelib.db.migrate as m
    monkeypatch.setattr(m, "migrate", lambda p: 1)
    ok, log = updater.update(deps=False, browser=False, restart=False)
    assert ok, log
    assert (app / "VERSION").read_text() == "2\n"
    assert updater.check()["behind"] == 0


def test_not_a_git_checkout(monkeypatch):
    monkeypatch.setattr(updater, "repo_root", lambda: None)
    assert not updater.current().is_git
    assert "git" in updater.check()["error"]
    ok, log = updater.update(deps=False, browser=False, restart=False)
    assert not ok


def test_status_partial_and_restart_marker(client, library, monkeypatch):
    import json, time
    from recipelib import updater
    html = client.get("/partials/update-status").text
    assert 'id="update-status"' in html and "Check for updates" in html
    # while checking, the partial polls
    monkeypatch.setitem(updater.STATE, "checking", True)
    assert 'hx-trigger="every 2s"' in client.get("/partials/update-status").text
    monkeypatch.setitem(updater.STATE, "checking", False)
    assert 'hx-trigger="every 2s"' not in client.get("/partials/update-status").text
    # a marker left by the previous process reads as "updated ... up to date" once, then is gone
    m = library.library_dir / "tmp" / "update-in-progress.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"started": time.time() - 100, "from": "abc1234"}))
    monkeypatch.setattr(updater, "BOOT_TIME", time.time())
    html = client.get("/partials/update-status").text
    assert "Updated" in html and "abc1234" in html and "Up to date" in html
    assert not m.exists()
    assert "Updated" not in client.get("/partials/update-status").text


def test_update_banner_on_every_page(client, monkeypatch):
    from recipelib import updater
    assert "A new version" not in client.get("/").text
    monkeypatch.setitem(updater.STATE, "behind", 2)
    monkeypatch.setitem(updater.STATE, "remote", "abc1234")
    html = client.get("/shopping").text
    assert "A new version of Recipe Library is available (2 changes)" in html and "Update now" in html
    monkeypatch.setitem(updater.STATE, "updating", True)
    assert "A new version" not in client.get("/").text
