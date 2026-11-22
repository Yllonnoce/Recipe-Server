"""Long jobs started from Settings run in the background, one at a time, with progress."""
import re
import threading

import pytest

from .conftest import make_pdf, wait_for


@pytest.fixture()
def tasks():
    from recipelib import tasks as T
    yield T
    assert T.current() is None, "a test left a job running"


def _hold(T, kind="restore", label="Restoring a backup", blocks_writes=True):
    """Start a job that runs until released."""
    go = threading.Event()

    def fn(report):
        report("Merging recipes", 3, 10)
        go.wait(10)
        return "held job finished"
    T.start(kind, label, fn, blocks_writes=blocks_writes)
    wait_for(lambda: (T.status(kind) or {}).get("step") == "Merging recipes", timeout=5)
    return go


def test_a_job_reports_its_step_and_count_then_its_result(tasks):
    go = _hold(tasks, "shrink", "Shrinking stored files", blocks_writes=False)
    st = tasks.status("shrink")
    assert st["running"] and st["step"] == "Merging recipes" and (st["done"], st["total"]) == (3, 10) and st["pct"] == 30
    go.set()
    wait_for(lambda: not tasks.status("shrink")["running"], timeout=5)
    st = tasks.status("shrink")
    assert st["ok"] and st["message"] == "held job finished"


def test_a_failed_job_says_why(tasks):
    def boom(report):
        raise ValueError("not a Recipe Library backup")
    tasks.start("restore", "Restoring a backup", boom)
    wait_for(lambda: not tasks.status("restore")["running"], timeout=5)
    st = tasks.status("restore")
    assert st["ok"] is False and "not a Recipe Library backup" in st["message"]


def test_only_one_job_runs_at_a_time(tasks):
    go = _hold(tasks)
    with pytest.raises(tasks.Busy) as e:
        tasks.start("backup", "Creating a backup", lambda report: "x")
    assert "Restoring a backup" in str(e.value)
    with pytest.raises(tasks.Busy):
        with tasks.exclusive("mirror", "Syncing from the backup server"):
            pass
    go.set()
    wait_for(lambda: tasks.current() is None, timeout=5)


def test_changes_are_refused_while_a_restore_runs(client, tasks):
    go = _hold(tasks)
    try:
        r = client.post("/shopping/items", data={"name": "milk"}, follow_redirects=False)
        assert r.status_code == 503 and "being restored" in r.text
        assert client.get("/").status_code == 200            # reading still works
        assert client.get("/partials/task/restore").status_code == 200
    finally:
        go.set()
    wait_for(lambda: tasks.current() is None, timeout=5)
    assert client.post("/shopping/items", data={"name": "milk"}, follow_redirects=False).status_code != 503


def test_the_capture_queue_waits_while_a_restore_runs(client, tasks, tmp_path):
    from recipelib.capture.queue import get_queue
    from recipelib.db.engine import session_scope
    from recipelib.db.models import CaptureJob
    go = _hold(tasks)
    try:
        jid = get_queue().enqueue("upload", pdf_path=str(tmp_path / "gone.pdf"))
        import time
        time.sleep(1.0)
        with session_scope() as s:
            assert s.get(CaptureJob, jid).state == "queued"
    finally:
        go.set()
    get_queue().wake()
    wait_for(lambda: _state(jid) != "queued", timeout=10)


def _state(jid):
    from recipelib.db.engine import session_scope
    from recipelib.db.models import CaptureJob
    with session_scope() as s:
        return s.get(CaptureJob, jid).state


def test_backup_and_restore_from_settings_run_in_the_background(client, library, tasks, tmp_path):
    from recipelib import backup as B
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe
    with session_scope() as s:
        s.add(Recipe(title="Grandma's stew", status="ready"))
    html = client.post("/settings/backup", headers={"HX-Request": "true"}).text
    assert 'id="task-backup"' in html
    wait_for(lambda: not tasks.status("backup")["running"], timeout=20)
    assert tasks.status("backup")["ok"], tasks.status("backup")
    name = B.list_backups()[0]["name"]
    with session_scope() as s:
        s.query(Recipe).delete()
    html = client.post(f"/settings/backups/{name}/restore", data={"mode": "merge"}, headers={"HX-Request": "true"}).text
    assert 'id="task-restore"' in html
    wait_for(lambda: not tasks.status("restore")["running"], timeout=20)
    st = tasks.status("restore")
    assert st["ok"] and "1 recipe added" in st["message"], st
    with session_scope() as s:
        assert [r.title for r in s.query(Recipe)] == ["Grandma's stew"]


def test_restore_reports_progress(client, library, tasks):
    from recipelib import backup as B
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe
    with session_scope() as s:
        for t in ("A", "B", "C"):
            s.add(Recipe(title=t, status="ready"))
    zp = B.create_backup(progress=lambda *a: None)
    seen = []
    B.restore(zp, mode="replace", progress=lambda step, done=None, total=None: seen.append((step, done, total)))
    steps = [s for s, _, _ in seen]
    assert "Merging recipes" in steps
    assert ("Merging recipes", 3, 3) in seen


def test_maintenance_buttons_report_progress_in_place(client):
    html = client.get("/settings").text
    for kind in ("backup", "restore", "shrink", "reindex", "categorize"):
        assert f'id="task-{kind}"' in html, kind
    assert 'id="uploadBar"' in html          # the backup upload shows how much has been sent


def test_the_restoring_message_escapes_the_job_label(client, tasks):
    go = _hold(tasks, label='Merging <img src=x onerror=alert(1)>.zip')
    try:
        r = client.post("/shopping/items", data={"text": "milk"}, headers={"HX-Request": "true"})
        assert r.status_code == 503 and "<img" not in r.text and "&lt;img" in r.text
    finally:
        go.set()
    wait_for(lambda: tasks.current() is None, timeout=5)
