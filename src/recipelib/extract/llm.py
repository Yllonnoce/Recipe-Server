"""Turn page text into a RecipeDraft with a local Ollama model.

Long documents are windowed to the most recipe-looking span; the model is
asked for JSON constrained to the RecipeDraft schema; the result is
validated and retried once with the validation error before giving up.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from ..config import get_settings
from .schema import SYSTEM_PROMPT, RecipeDraft

log = logging.getLogger(__name__)
MAX_CHARS = 24000
BLOCK = 3000
_RECIPE_WORDS = re.compile(r"\b(ingredients?|directions?|instructions?|method|preparation|serves|servings|yield|prep time|cook time|"
                           r"minutes?|cups?|tbsp|tsp|tablespoons?|teaspoons?|grams?|oz|ounces?|preheat|bake|simmer|stir|whisk)\b", re.I)
_QTY = re.compile(r"(^|\n)\s*(\d+[\d/.,\s]*|[½⅓⅔¼¾⅛])\s*(cups?|tbsp|tsp|g|kg|ml|l|oz|lb|cloves?|large|small|medium)?\b", re.I)


class LLMUnavailable(Exception):
    """Ollama is down, or the model is missing. The queue defers and retries."""


def clean_text(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            lines.append("")
            continue
        if re.match(r"^(https?://\S+|www\.\S+)$", s):
            continue
        if re.match(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", s, re.I):
            continue
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}", s):
            continue
        lines.append(s)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return out.strip()


def choose_window(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    blocks = [text[i:i + BLOCK] for i in range(0, len(text), BLOCK)]
    scores = [len(_RECIPE_WORDS.findall(b)) + 2 * len(_QTY.findall(b)) for b in blocks]
    n = max(1, limit // BLOCK)
    best_i, best = 0, -1
    for i in range(0, max(1, len(blocks) - n + 1)):
        sc = sum(scores[i:i + n])
        if sc > best:
            best, best_i = sc, i
    return "".join(blocks[best_i:best_i + n])


def _client():
    import ollama
    cfg = get_settings()
    return ollama.Client(host=cfg.ollama_host, timeout=240)


def extract_recipe(text: str, title_hint: str | None = None, source: str | None = None) -> dict | None:
    """Returns a RecipeDraft dict (plus method='llm'), or None when the model
    says the text is not a recipe. Raises LLMUnavailable when Ollama is
    unreachable or the model is missing."""
    import ollama
    cfg = get_settings()
    body = choose_window(clean_text(text))
    if len(body) < 40:
        return None
    user = f"TITLE HINT: {title_hint or '(none)'}\nSOURCE: {source or '(unknown)'}\n\nTEXT:\n{body}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    schema = RecipeDraft.model_json_schema()
    client = _client()
    last_err: str | None = None
    for attempt in range(2):
        if last_err:
            messages.append({"role": "user", "content": f"That JSON was invalid: {last_err}\nReturn corrected JSON only."})
        try:
            kwargs = dict(model=cfg.ollama_model, messages=messages, format=schema,
                          options={"temperature": 0, "num_ctx": 12288},
                          keep_alive=-1 if cfg.llm_keep_loaded else "30m", stream=False)
            try:
                resp = client.chat(think=False, **kwargs)
            except TypeError:
                resp = client.chat(**kwargs)
        except ollama.ResponseError as e:
            if getattr(e, "status_code", 0) == 404 or "not found" in str(e).lower():
                raise LLMUnavailable(f"model {cfg.ollama_model} is not pulled (ollama pull {cfg.ollama_model})") from e
            raise LLMUnavailable(f"ollama error: {e}") from e
        except Exception as e:  # noqa: BLE001  (connection refused, timeouts)
            raise LLMUnavailable(f"ollama unreachable at {cfg.ollama_host}: {type(e).__name__}") from e
        content = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
        try:
            data = json.loads(_strip_fences(content))
            draft = RecipeDraft.model_validate(data)
        except (ValueError, ValidationError) as e:
            last_err = str(e)[:500]
            log.warning("LLM output invalid (attempt %d): %s", attempt + 1, last_err[:200])
            messages.append({"role": "assistant", "content": content[:4000]})
            continue
        out = draft.model_dump()
        out["method"] = "llm"
        out["ingredients"] = [dict(i, group=(sec.get("heading") or None))
                              for sec in out.pop("ingredient_sections", []) for i in sec.get("ingredients", [])]
        out["steps"] = [dict(st, group=(sec.get("heading") or None))
                        for sec in out.pop("step_sections", []) for st in sec.get("steps", [])]
        if len({i["group"] for i in out["ingredients"]}) == 1:
            for i in out["ingredients"]:
                i["group"] = None
        if len({st["group"] for st in out["steps"]}) == 1:
            for st in out["steps"]:
                st["group"] = None
        return out
    return None


def _strip_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s


def warm_up(keep: bool = True) -> bool:
    """Load the model now (and pin it in memory with keep_alive=-1) so the
    first capture after a restart doesn't stall the machine. Returns True
    when the model responded."""
    cfg = get_settings()
    if not cfg.llm_enabled:
        return False
    try:
        client = _client()
        client.generate(model=cfg.ollama_model, prompt="", keep_alive=-1 if keep else "30m")
        log.info("LLM warmed up: %s is loaded%s", cfg.ollama_model, " and pinned in memory" if keep else "")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("LLM warm-up skipped: %s: %s", type(e).__name__, str(e)[:120])
        return False
