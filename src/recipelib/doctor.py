"""Environment checks shared by `recipes doctor` and the Settings page."""
from __future__ import annotations

import socket
import sqlite3
import sys
from dataclasses import dataclass

from .config import get_settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(quick: bool = False) -> list[Check]:
    cfg = get_settings()
    out: list[Check] = []
    out.append(Check("Python", sys.version_info[:2] >= (3, 11), sys.version.split()[0]))
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        out.append(Check("SQLite FTS5", True, sqlite3.sqlite_version))
    except sqlite3.OperationalError as e:
        out.append(Check("SQLite FTS5", False, str(e)))
    out.append(Check("Library folder", cfg.library_dir.is_dir(), str(cfg.library_dir)))
    try:
        import pymupdf
        out.append(Check("PyMuPDF", True, pymupdf.__doc__.split(":")[0]))
    except Exception as e:  # noqa: BLE001
        out.append(Check("PyMuPDF", False, str(e)))
    try:
        import rapidocr  # noqa: F401
        out.append(Check("OCR (rapidocr)", True, "installed"))
    except Exception:  # noqa: BLE001
        out.append(Check("OCR (rapidocr)", False, "not installed: pip install 'recipelib[ocr]'"))
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        import os
        from pathlib import Path
        cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / ".cache" / "ms-playwright"))
        has = cache.exists() and any(p.name.startswith("chromium") for p in cache.iterdir())
        out.append(Check("Playwright Chromium", has, str(cache) if has else "run: playwright install chromium"))
    except Exception as e:  # noqa: BLE001
        out.append(Check("Playwright", False, str(e)))
    if not quick:
        out.append(_ollama(cfg))
        out.append(_port(cfg.port, "Web port"))
        out.append(_port(cfg.ipp_port, "Printer port"))
    else:
        out.append(_ollama(cfg, timeout=1.5))
    return out


def _ollama(cfg, timeout: float = 5.0) -> Check:
    try:
        import httpx
        r = httpx.get(cfg.ollama_host.rstrip("/") + "/api/tags", timeout=timeout)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        want = cfg.ollama_model
        have = any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)
        return Check("Ollama", have, f"{cfg.ollama_host}: model {want} {'available' if have else 'missing (ollama pull ' + want + ')'}")
    except Exception as e:  # noqa: BLE001
        return Check("Ollama", False, f"{cfg.ollama_host} unreachable ({type(e).__name__})")


def _port(port: int, label: str) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.5)
        busy = sk.connect_ex(("127.0.0.1", port)) == 0
    return Check(label, not busy, f"{port} {'in use' if busy else 'free'}")
