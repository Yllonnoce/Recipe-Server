"""Backup server: keep this library a copy of another Recipe Library.

The other server ("the primary") exposes a fresh backup at /api/backup/latest.
This server downloads it on a schedule and merges it in (backup wins on
conflicts), or replaces its library with it when mirror_mode = "replace".
Works over the LAN with no setup on the primary beyond being reachable.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import get_settings

log = logging.getLogger(__name__)
STATE = {"running": False, "last_run": None, "last_ok": None, "result": "", "source": None}


def normalize_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if u and "://" not in u:
        u = "http://" + u
    return u


def resolve_url(u: str) -> str:
    """A bare name like 'ubuntu' won't resolve on a home network; try 'ubuntu.local'."""
    import socket
    from urllib.parse import urlsplit, urlunsplit
    u = normalize_url(u)
    parts = urlsplit(u)
    host = parts.hostname or ""
    if not host or "." in host or host == "localhost":
        return u
    try:
        socket.gethostbyname(host)
        return u
    except OSError:
        pass
    try:
        socket.gethostbyname(host + ".local")
        netloc = host + ".local" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except OSError:
        return u


def explain(err: Exception) -> str:
    msg = str(err)
    if any(k in msg for k in ("nodename nor servname", "Name or service not known", "Temporary failure in name resolution",
                                "getaddrinfo", "Errno 8]", "Errno -2]", "Errno -3]", "gaierror")):
        return ("that name could not be looked up. Use the server's IP address (Settings → Network lists it), "
                "or its Bonjour name ending in .local, e.g. http://ubuntu.local:8000")
    if "Errno 65" in msg or "No route to host" in msg:
        return "macOS is blocking this program's access to the local network (System Settings → Privacy & Security → Local Network → allow python3)"
    if "Connection refused" in msg or "Errno 61" in msg or "Errno 111" in msg:
        return "nothing is listening at that address and port (is the other server running, and is the port right: 8000, or 80 with nginx?)"
    return f"{type(err).__name__}: {msg[:140]}"


def primary_info(url: str, timeout: float = 5.0) -> dict:
    import httpx
    r = httpx.get(resolve_url(url) + "/api/backup/info", timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("app") != "recipelib":
        raise ValueError("that address answered, but it is not a Recipe Library server")
    return data


def pull_backup(url: str, dest_dir: Path, timeout: float = 600.0) -> Path:
    """Download a fresh backup from the primary into dest_dir. Returns the zip path."""
    import httpx
    url = resolve_url(url)
    host = url.split("://", 1)[1].replace(":", "_").replace("/", "_")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"mirror-{host}-{stamp}.zip"
    part = dest.with_suffix(".zip.part")
    with httpx.stream("GET", url + "/api/backup/latest", timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        if "zip" not in r.headers.get("content-type", ""):
            raise ValueError("the address did not return a backup zip; is it a Recipe Library server?")
        with part.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
    part.replace(dest)
    return dest


def sync_once(url: str | None = None, mode: str | None = None) -> str:
    """Pull from the primary and restore. Returns a one-line result; raises on failure."""
    from . import backup as B
    cfg = get_settings()
    url = normalize_url(url or cfg.mirror_of)
    mode = mode or cfg.mirror_mode
    if not url:
        raise ValueError("no primary configured (mirror_of)")
    STATE.update(running=True, source=url)
    try:
        zip_path = pull_backup(url, B.backups_dir())
        stats = B.restore(zip_path, mode="replace" if mode == "replace" else "merge", overwrite=True)
        # keep only the newest mirror zips so the disk doesn't fill
        olds = sorted(B.backups_dir().glob("mirror-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[3:]
        for o in olds:
            o.unlink(missing_ok=True)
        msg = f"{datetime.now():%b %d %H:%M}: synced from {url} ({zip_path.stat().st_size // 1048576} MB): {stats.summary()}"
        STATE.update(last_ok=True, result=msg, last_run=time.time())
        log.info("mirror: %s", msg)
        return msg
    except Exception as e:  # noqa: BLE001
        msg = f"{datetime.now():%b %d %H:%M}: sync from {url} failed: {explain(e)}"
        STATE.update(last_ok=False, result=msg, last_run=time.time())
        log.warning("mirror: %s", msg)
        raise
    finally:
        STATE["running"] = False


def sync_async() -> bool:
    if STATE["running"]:
        return False

    def run():
        try:
            sync_once()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, name="mirror-sync", daemon=True).start()
    return True


class Mirror:
    """Scheduled pull from the primary (mirror_interval_hours; 0 = off)."""

    def __init__(self, url: str, interval_hours: float):
        self.url = url
        self.every = interval_hours * 3600
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def start(self) -> None:
        if not self.url or self.every <= 0:
            return
        self._t = threading.Thread(target=self._run, name="mirror", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self._stop.wait(180)                       # let both servers settle after boot
        while not self._stop.is_set():
            last = STATE.get("last_run") or 0
            if time.time() - last >= self.every:
                try:
                    sync_once()
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(1800)
