"""The model picker on the Settings page."""
import re

import pytest

INSTALLED = ["llama3.1:8b", "qwen3.5:9b", "qwen3:8b"]


@pytest.fixture()
def ollama_up(monkeypatch):
    from recipelib import llmhost

    def fake_test_host(host, timeout=4.0):
        return {"host": llmhost.normalize_host(host), "ok": True, "version": "0.9", "models": list(INSTALLED), "error": None}
    monkeypatch.setattr(llmhost, "test_host", fake_test_host)


def _select(html: str) -> str:
    m = re.search(r'<select name="model".*?</select>', html, re.S)
    assert m, "the model is not a real drop-down"
    return m.group(0)


def _selected(html: str) -> str:
    return re.search(r'<option value="([^"]*)" selected', _select(html)).group(1)


def test_every_installed_model_can_be_picked_whatever_is_current(client, ollama_up):
    from recipelib import llmhost
    llmhost.save("127.0.0.1", "qwen3.5:9b")
    html = client.get("/partials/ollama").text
    for m in INSTALLED:
        assert f'<option value="{m}"' in _select(html)
    assert _selected(html) == "qwen3.5:9b"


def test_picking_another_model_saves_and_uses_it(client, ollama_up):
    from recipelib import llmhost
    from recipelib.config import get_settings
    llmhost.save("127.0.0.1", "qwen3.5:9b")
    html = client.post("/settings/ollama", data={"host": "127.0.0.1", "model": "qwen3:8b"}).text
    assert get_settings().ollama_model == "qwen3:8b"
    assert _selected(html) == "qwen3:8b"
    assert "model <strong>qwen3:8b</strong> ready" in html


def test_a_typed_model_name_wins_over_the_drop_down(client, ollama_up):
    from recipelib.config import get_settings
    client.post("/settings/ollama", data={"host": "127.0.0.1", "model": "qwen3:8b", "model_other": " mistral:7b "})
    assert get_settings().ollama_model == "mistral:7b"


def test_a_current_model_that_is_not_installed_still_shows_as_selected(client, ollama_up):
    from recipelib import llmhost
    llmhost.save("127.0.0.1", "gemma3:4b")
    html = client.get("/partials/ollama").text
    assert _selected(html) == "gemma3:4b"


def test_pull_uses_the_typed_model_name(client, ollama_up, monkeypatch):
    from recipelib import llmhost
    pulled = []
    monkeypatch.setattr(llmhost, "pull_async", lambda model, host=None: pulled.append(model) or True)
    client.post("/settings/ollama/pull", data={"model": "qwen3:8b", "model_other": "mistral:7b"})
    client.post("/settings/ollama/pull", data={"model": "qwen3:8b", "model_other": ""})
    assert pulled == ["mistral:7b", "qwen3:8b"]
