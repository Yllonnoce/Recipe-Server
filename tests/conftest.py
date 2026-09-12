import os
import time
from pathlib import Path

import pytest


@pytest.fixture()
def library(tmp_path, monkeypatch):
    """A fresh library folder + settings for one test."""
    lib = tmp_path / "RecipeLibrary"
    monkeypatch.setenv("RECIPELIB_LIBRARY_DIR", str(lib))
    monkeypatch.setenv("RECIPELIB_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("RECIPELIB_PRINTER_ENABLED", "false")
    monkeypatch.setenv("RECIPELIB_LLM_ENABLED", "false")
    monkeypatch.setenv("RECIPELIB_OCR_ENABLED", "false")
    monkeypatch.setenv("RECIPELIB_WATCH_INTERVAL", "0.3")
    monkeypatch.setenv("RECIPELIB_WORKERS", "1")
    from recipelib.config import get_settings
    cfg = get_settings(reload=True)
    cfg.ensure_dirs()
    return cfg


@pytest.fixture()
def client(library):
    from fastapi.testclient import TestClient
    from recipelib.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def make_pdf(path: Path, pages: list[str]) -> Path:
    import pymupdf
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), text, fontsize=14)
    doc.save(path)
    doc.close()
    return path


def wait_for(pred, timeout=20.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out waiting")
