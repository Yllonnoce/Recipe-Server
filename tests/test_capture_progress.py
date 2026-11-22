"""Progress for every kind of capture (printer, upload, URL, inbox), where it can be seen
without the recipe page open: the Add page's job list and the library card."""
import json
import re

FAST = ["fetch", "raster_to_pdf", "shrink", "hash_dedup", "store", "text", "ocr", "thumbnail", "structured"]


def _job(source: str, state: str, done: list[str], with_recipe=True, **kw) -> tuple[int, int | None]:
    from recipelib.db.engine import session_scope
    from recipelib.db.models import CaptureJob, Recipe, utcnow
    with session_scope() as s:
        rid = None
        if with_recipe:
            r = Recipe(title="Printed soup", status="processing")
            s.add(r)
            s.flush()
            rid = r.id
        log = [{"t": utcnow(), "stage": "queued", "msg": ""}] + [{"t": utcnow(), "stage": st, "msg": "ok"} for st in done]
        j = CaptureJob(source=source, state=state, stage=done[-1] if done else "queued", stage_log=json.dumps(log),
                       recipe_id=rid, title_hint="Printed soup", started_at=utcnow(), **kw)
        s.add(j)
        s.flush()
        return j.id, rid


def _row(html: str, title: str) -> str:
    m = re.search(r"<tr[^>]*>(?:(?!</tr>).)*" + re.escape(title) + r".*?</tr>", html, re.S)
    assert m, f"no job row for {title}"
    return m.group(0)


def test_a_printed_recipe_shows_its_current_step_on_the_add_page(client):
    _job("printer", "running", FAST)
    row = _row(client.get("/partials/jobs").text, "Printed soup")
    assert "Reading the recipe" in row
    assert "structured" not in row          # the last finished stage name meant nothing to anyone
    assert len(re.findall(r'<i class="(done|current|todo|waiting)"', row)) == 4


def test_a_job_shows_progress_before_its_recipe_exists(client):
    _job("printer", "running", ["fetch", "raster_to_pdf"], with_recipe=False)
    assert "Reading the PDF" in _row(client.get("/partials/jobs").text, "Printed soup")


def test_waiting_for_ollama_shows_on_the_add_page(client):
    _job("upload", "waiting_llm", FAST, next_attempt_at="2999-01-01T00:00:00+00:00", last_error="ConnectError")
    assert "Waiting for Ollama" in _row(client.get("/partials/jobs").text, "Printed soup")


def test_finished_jobs_show_no_progress(client):
    _job("upload", "done", FAST + ["llm", "normalize", "index"])
    row = _row(client.get("/partials/jobs").text, "Printed soup")
    assert "seg" not in row and "Reading" not in row


def test_the_library_card_shows_the_current_step(client):
    _job("printer", "running", FAST)
    card = re.search(r'<a class="card status-processing".*?</a>', client.get("/").text, re.S).group(0)
    assert "Reading recipe" in card


def test_a_processing_card_keeps_the_sort_order_when_it_refreshes(client):
    _job("printer", "running", FAST)
    html = client.get("/?sort=title").text
    poll = re.search(r'<a class="card status-processing"[^>]*hx-get="([^"]+)"', html).group(1)
    assert "sort=title" in poll
