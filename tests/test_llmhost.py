from recipelib import llmhost


def test_normalize_and_has_model():
    assert llmhost.normalize_host("") == "http://127.0.0.1:11434"
    assert llmhost.normalize_host("gpu-box") == "http://gpu-box:11434"
    assert llmhost.normalize_host("http://10.0.4.33:11434/") == "http://10.0.4.33:11434"
    assert llmhost.normalize_host("https://ai.example.com:8443") == "https://ai.example.com:8443"
    assert llmhost.has_model(["qwen3:8b", "llama3.1:8b"], "qwen3:8b")
    assert llmhost.has_model(["qwen3:latest"], "qwen3") and not llmhost.has_model(["qwen3:4b"], "qwen3:8b")


def test_test_host_unreachable_is_reported_not_raised():
    r = llmhost.test_host("http://127.0.0.1:1", timeout=0.5)
    assert r["ok"] is False and r["error"]


def test_save_writes_config_and_reloads(library):
    from recipelib.config import config_path, get_settings
    llmhost.save("gpu-box", "qwen3:4b")
    text = config_path().read_text()
    assert 'ollama_host = "http://gpu-box:11434"' in text and 'ollama_model = "qwen3:4b"' in text
    assert get_settings().ollama_host == "http://gpu-box:11434" and get_settings().ollama_model == "qwen3:4b"


def test_settings_panel_and_partial(client, monkeypatch):
    monkeypatch.setattr(llmhost, "test_host", lambda host, timeout=4.0: {"host": host, "ok": True, "version": "0.32", "models": ["qwen3:8b"], "error": None})
    assert "Recipe extraction (Ollama)" in client.get("/settings").text
    html = client.get("/partials/ollama").text
    assert "Ollama 0.32" in html and "ready" in html
    html = client.post("/settings/ollama/test", data={"host": "http://elsewhere:11434"}).text
    assert "elsewhere:11434" in html
    monkeypatch.setattr(llmhost, "scan_lan", lambda: [{"ip": "10.0.0.5", "name": "gpu-box", "host": "http://10.0.0.5:11434", "version": "0.32", "models": ["qwen3:8b"]}])
    html = client.post("/settings/ollama/scan").text
    assert "gpu-box" in html and "10.0.0.5:11434" in html


def test_network_scan_panel(client, monkeypatch):
    from recipelib import netscan
    monkeypatch.setattr(netscan, "scan", lambda ports=None, timeout=0.35, networks=None: [
        netscan.Found("10.0.4.30", 80, "recipe-web", "MacM5", "Recipe Library", "http://10.0.4.30:80"),
        netscan.Found("10.0.4.30", 8631, "recipe-printer", "MacM5", "prints into a Recipe Library", "ipp://10.0.4.30:8631/ipp/print"),
        netscan.Found("10.0.4.33", 11434, "ollama", "ubuntu", "Ollama 0.32", "http://10.0.4.33:11434", ["qwen3:8b"])])
    assert 'id="network"' in client.get("/settings").text
    html = client.post("/settings/scan", data={"port": ""}).text
    assert "MacM5" in html and "ipp://10.0.4.30:8631/ipp/print" in html and "use for extraction" in html and "open ↗" in html
    monkeypatch.setattr(netscan, "scan", lambda ports=None, timeout=0.35, networks=None: [])
    assert "Nothing answered on port 9999" in client.post("/settings/scan", data={"port": "9999"}).text
