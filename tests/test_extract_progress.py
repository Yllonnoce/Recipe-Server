"""Progress shown on a recipe page while it is being (re-)extracted."""
import json
import re

FAST = ["fetch", "raster_to_pdf", "shrink", "hash_dedup", "store", "text", "ocr", "thumbnail", "structured"]


def _recipe_with_job(state: str, done: list[str], next_attempt_at=None, last_error=None) -> int:
    from recipelib.db.engine import session_scope
    from recipelib.db.models import CaptureJob, Recipe, utcnow
    with session_scope() as s:
        r = Recipe(title="Soup", status="processing")
        s.add(r)
        s.flush()
        log = [{"t": utcnow(), "stage": "queued", "msg": ""}] + [{"t": utcnow(), "stage": st, "msg": "ok"} for st in done]
        s.add(CaptureJob(source="upload", state=state, stage=done[-1] if done else "queued", stage_log=json.dumps(log),
                         recipe_id=r.id, force=1, started_at=utcnow(), next_attempt_at=next_attempt_at, last_error=last_error))
        return r.id


def _steps(html: str) -> list[tuple[str, str]]:
    ol = re.search(r'<ol class="progress">(.*?)</ol>', html, re.S)
    assert ol, "no progress steps on the page"
    return [(cls, re.sub(r"<[^>]+>", "", label).strip()) for cls, label in re.findall(r'<li class="(\w+)">(.*?)</li>', ol.group(1), re.S)]


def test_the_page_shows_which_step_is_running(client, library):
    rid = _recipe_with_job("running", FAST)
    html = client.get(f"/recipes/{rid}").text
    steps = _steps(html)
    assert [c for c, _ in steps] == ["done", "done", "current", "todo"]
    assert steps[2][1] == f"Reading the recipe with {library.ollama_model}"
    assert '<div class="bar"><div class="fill"></div></div>' in html


def test_the_first_steps_show_before_the_model_starts(client, library):
    rid = _recipe_with_job("running", ["fetch", "raster_to_pdf"])
    assert [c for c, _ in _steps(client.get(f"/recipes/{rid}").text)] == ["done", "current", "todo", "todo"]


def test_waiting_for_ollama_is_said_plainly(client, library):
    rid = _recipe_with_job("waiting_llm", FAST, next_attempt_at="2999-01-01T00:00:00+00:00",
                           last_error="ConnectError: connection refused")
    html = client.get(f"/recipes/{rid}").text
    assert [c for c, _ in _steps(html)] == ["done", "done", "waiting", "todo"]
    assert "Waiting for Ollama" in html and "connection refused" in html


def test_reextract_links_the_job_to_the_recipe_when_it_is_queued(client, library, monkeypatch):
    """Linking it afterwards races the worker: a worker that starts first makes a second
    recipe and leaves this one stuck on 'processing'."""
    from recipelib.capture import queue as Q
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Asset, Recipe
    with session_scope() as s:
        a = Asset(kind="pdf", rel_path="pdf/aa/x.pdf", sha256="aa" * 32, bytes=1, mime="application/pdf", page_count=1)
        s.add(a)
        s.flush()
        r = Recipe(title="Stew", status="ready", pdf_asset_id=a.id)
        s.add(r)
        s.flush()
        rid = r.id
    seen = {}
    monkeypatch.setattr(Q.JobQueue, "enqueue", lambda self, source, **kw: seen.update(kw) or 999999)
    client.post(f"/recipes/{rid}/reextract", follow_redirects=False)
    assert seen.get("recipe_id") == rid


def test_enqueue_stores_the_recipe_id_on_the_job(client, library):
    from recipelib.capture.queue import get_queue
    from recipelib.db.engine import session_scope
    from recipelib.db.models import CaptureJob, Recipe
    with session_scope() as s:
        r = Recipe(title="Bread", status="processing")
        s.add(r)
        s.flush()
        rid = r.id
    jid = get_queue().enqueue("upload", pdf_path="/nonexistent.pdf", recipe_id=rid)
    with session_scope() as s:
        assert s.get(CaptureJob, jid).recipe_id == rid
