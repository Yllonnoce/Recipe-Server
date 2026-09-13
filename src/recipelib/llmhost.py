"""Ollama host management: test, list models, pull with progress, LAN scan,
and saving the choice to the config file (applied live, no restart)."""
from __future__ import annotations

import concurrent.futures
import logging
import re
import socket
import threading
import time

from .config import config_path, get_settings

log = logging.getLogger(__name__)
PULL = {"running": False, "model": None, "status": "", "percent": 0, "done": None, "error": None}


def normalize_host(h: str) -> str:
    h = (h or "").strip().rstrip("/")
    if not h:
        return "http://127.0.0.1:11434"
    if "://" not in h:
        h = "http://" + h
    if not re.search(r":\d+$", h.split("://", 1)[1]):
        h += ":11434"
    return h


def test_host(host: str, timeout: float = 4.0) -> dict:
    """{ok, version, models: [...], error}"""
    import httpx
    host = normalize_host(host)
    out = {"host": host, "ok": False, "version": None, "models": [], "error": None}
    try:
        v = httpx.get(host + "/api/version", timeout=timeout).json()
        out["version"] = v.get("version")
        tags = httpx.get(host + "/api/tags", timeout=timeout).json()
        out["models"] = sorted(m.get("name", "") for m in tags.get("models", []))
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}" if str(e) else type(e).__name__
        out["hint"] = connection_hint(str(e))
    return out


def connection_hint(err: str) -> str | None:
    """Turn the classic macOS 'Local Network' refusal into advice."""
    import sys
    if sys.platform == "darwin" and ("Errno 65" in err or "No route to host" in err):
        return ("macOS is blocking this program from talking to other devices on the network. "
                "Open System Settings → Privacy & Security → Local Network and switch on 'python3' (or 'Python'). "
                "If it is not listed, run in Terminal: sudo tccutil reset LocalNetwork, then try again and click Allow on the prompt.")
    return None


def has_model(models: list[str], want: str) -> bool:
    base = want.split(":")[0]
    return any(m == want or (":" not in want and m.split(":")[0] == base) for m in models)


def save(host: str, model: str) -> None:
    """Write host/model to the config file and reload settings so the next
    extraction uses them."""
    import re as _re
    p = config_path()
    if not p.exists():
        from .config import write_default_config
        write_default_config()
    text = p.read_text(encoding="utf-8")
    for key, val in (("ollama_host", normalize_host(host)), ("ollama_model", model.strip())):
        line = f'{key} = "{val}"'
        pat = _re.compile(rf"^{key}\s*=.*$", _re.M)
        text = pat.sub(line, text, count=1) if pat.search(text) else text.rstrip("\n") + f"\n{line}\n"
    p.write_text(text, encoding="utf-8")
    get_settings(reload=True)


def pull_async(model: str, host: str | None = None) -> bool:
    if PULL["running"]:
        return False
    PULL.update(running=True, model=model, status="starting", percent=0, done=None, error=None)

    def run():
        try:
            import ollama
            client = ollama.Client(host=normalize_host(host or get_settings().ollama_host), timeout=None)
            for ev in client.pull(model, stream=True):
                st = ev.get("status", "") if isinstance(ev, dict) else getattr(ev, "status", "")
                tot = (ev.get("total") if isinstance(ev, dict) else getattr(ev, "total", None)) or 0
                comp = (ev.get("completed") if isinstance(ev, dict) else getattr(ev, "completed", None)) or 0
                PULL["status"] = st
                if tot:
                    PULL["percent"] = int(comp * 100 / tot)
            PULL.update(done=True, percent=100, status="ready")
        except Exception as e:  # noqa: BLE001
            PULL.update(done=False, error=f"{type(e).__name__}: {str(e)[:160]}")
        finally:
            PULL["running"] = False
    threading.Thread(target=run, name="ollama-pull", daemon=True).start()
    return True


def _local_networks() -> list[str]:
    """/24 prefixes of this machine's IPv4 addresses."""
    prefixes: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        prefixes.add(ip.rsplit(".", 1)[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                prefixes.add(ip.rsplit(".", 1)[0])
    except OSError:
        pass
    return sorted(prefixes)


def scan_lan(port: int = 11434, timeout: float = 0.4) -> list[dict]:
    """Every host on the local /24 networks that answers on the Ollama port."""
    targets = [f"{p}.{i}" for p in _local_networks() for i in range(1, 255)]

    def probe(ip: str) -> str | None:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return ip
        except OSError:
            return None
    found: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for ip in ex.map(probe, targets):
            if ip:
                info = test_host(f"http://{ip}:{port}", timeout=2.0)
                try:
                    name = socket.gethostbyaddr(ip)[0].split(".")[0]
                except OSError:
                    name = ""
                found.append({"ip": ip, "name": name, "host": info["host"], "version": info.get("version"), "models": info.get("models", [])})
    return found
