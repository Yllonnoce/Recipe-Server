"""What a recipe page shows while its capture job runs: a few plain steps, not the
dozen pipeline stages. Nearly all the time goes on the model, so it gets a step of its own."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import CaptureJob
from .pipeline import STAGES

ACTIVE = ("queued", "running", "waiting_llm")

# (key, label, pipeline stages it covers)
GROUPS = [
    ("queued", "Queued", []),
    ("read", "Reading the PDF", ["fetch", "raster_to_pdf", "shrink", "hash_dedup", "store", "text", "ocr", "thumbnail", "structured"]),
    ("llm", "Reading the recipe with {model}", ["llm"]),
    ("save", "Saving", ["normalize", "index"]),
]
SHORT = {"queued": "Queued", "read": "Reading PDF", "llm": "Reading recipe", "save": "Saving"}
assert [st for _, _, sts in GROUPS for st in sts] == STAGES, "progress groups out of step with the pipeline"


def _seconds_from_now(iso: str | None) -> int | None:
    """Positive for a time in the future, negative for one in the past."""
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return int((t - datetime.now(timezone.utc)).total_seconds())


def _fmt(sec: int | None) -> str | None:
    if sec is None:
        return None
    return f"{sec}s" if sec < 60 else f"{sec // 60}m {sec % 60:02d}s"


def for_job(j: CaptureJob) -> dict | None:
    """Progress of one capture job as {state, steps, now, short, elapsed, error, retry_in}, or None
    once it has finished."""
    if j.state not in ACTIVE:
        return None
    entries = j.log_entries
    done = {e["stage"] for e in entries if e.get("msg") in ("ok", "skipped")}
    model = get_settings().ollama_model
    steps, current_found = [], False
    for key, label, stages in GROUPS:
        if key == "queued":
            state = "current" if j.state == "queued" else "done"
            current_found = j.state == "queued"
        elif all(st in done for st in stages):
            state = "done"
        elif current_found:
            state = "todo"
        else:
            current_found = True
            state = "waiting" if j.state == "waiting_llm" else "current"
        steps.append({"key": key, "label": label.format(model=model), "short": SHORT[key], "state": state})
    now = next((st for st in steps if st["state"] in ("current", "waiting")), steps[-1])
    # time on this attempt: a job that waited for Ollama restarts its clock when it is picked up again
    restarts = [e["t"] for e in entries if e.get("stage") in ("retry", "waiting_llm")]
    started = _seconds_from_now(max([j.started_at or ""] + restarts) or None)
    retry = _seconds_from_now(j.next_attempt_at)
    waiting = j.state == "waiting_llm"
    return {
        "state": j.state,
        "steps": steps,
        "now": now["label"],
        "short": "Waiting for Ollama" if waiting else now["short"],
        "elapsed": _fmt(max(0, -started)) if j.state == "running" and started is not None else None,
        "error": j.last_error if waiting else None,
        "retry_in": _fmt(retry) if waiting and retry is not None and retry > 0 else None,
    }


def for_recipes(s: Session, recipe_ids: list[int]) -> dict[int, dict]:
    """{recipe_id: progress} for those of these recipes with an unfinished job (newest job wins)."""
    if not recipe_ids:
        return {}
    out: dict[int, dict] = {}
    for j in s.scalars(select(CaptureJob).where(CaptureJob.recipe_id.in_(recipe_ids), CaptureJob.state.in_(ACTIVE))
                       .order_by(CaptureJob.id.desc())):
        if j.recipe_id not in out:
            p = for_job(j)
            if p:
                out[j.recipe_id] = p
    return out


def for_recipe(s: Session, recipe_id: int) -> dict | None:
    return for_recipes(s, [recipe_id]).get(recipe_id)
