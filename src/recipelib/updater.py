"""Self-update from the git checkout the app runs from.

check()   - fetches the remote and reports how many commits behind we are.
update()  - git pull --ff-only, reinstall the package (new dependencies,
            migrations), optionally the browser, then restart the server.
Everything is best-effort and logged; a failed step never leaves the app
in a worse state than before (pull is fast-forward only).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)
STATE = {"checked_at": 0.0, "behind": 0, "remote": None, "error": None, "updating": False, "last_log": "",
         "checking": False, "step": None, "result": None, "started_at": 0.0}
STEPS = [("pull", "Download"), ("deps", "Dependencies"), ("browser", "Browser"), ("migrate", "Database"), ("restart", "Restart")]
BOOT_TIME = time.time()


def _marker() -> Path:
    from .config import get_settings
    return get_settings().library_dir / "tmp" / "update-in-progress.json"


def finished_update() -> dict | None:
    """After a restart: the marker the previous process left tells the page
    the update it was watching has completed. Read once, then removed."""
    m = _marker()
    if not m.exists():
        return None
    try:
        import json
        data = json.loads(m.read_text())
    except Exception:  # noqa: BLE001
        data = {}
    if data.get("started", 0) > BOOT_TIME:
        return None                   # still the process that started the update
    try:
        m.unlink()
    except OSError:
        pass
    v = current()
    return {"from": data.get("from"), "to": v.commit, "ok": True}


def check_async() -> None:
    if STATE["checking"]:
        return
    STATE["checking"] = True

    def run():
        try:
            check()
        finally:
            STATE["checking"] = False
    threading.Thread(target=run, name="update-check-now", daemon=True).start()


def repo_root() -> Path | None:
    """The git checkout containing this package, if the app runs from one."""
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / ".git").exists() and (p / "pyproject.toml").exists():
            return p
    return None


def _git(args: list[str], timeout: int = 30) -> tuple[int, str]:
    root = repo_root()
    if root is None or shutil.which("git") is None:
        return 1, "not a git checkout"
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "git timed out"


@dataclass
class Version:
    version: str = __version__
    commit: str | None = None
    branch: str | None = None
    date: str | None = None
    remote: str | None = None
    is_git: bool = False


def current() -> Version:
    v = Version()
    if repo_root() is None:
        return v
    v.is_git = True
    rc, out = _git(["log", "-1", "--format=%h|%cs"])
    if rc == 0 and "|" in out:
        v.commit, v.date = out.split("|", 1)
    rc, out = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        v.branch = out
    rc, out = _git(["remote", "get-url", "origin"])
    if rc == 0:
        v.remote = out
    return v


def check(timeout: int = 15) -> dict:
    """Fetch and compare with the upstream branch. Stores the result in STATE."""
    result = {"behind": 0, "ahead": 0, "error": None, "remote_commit": None, "checked_at": time.time()}
    if repo_root() is None:
        result["error"] = "not installed from git (copy the folder from a git clone to get updates)"
    else:
        rc, out = _git(["remote", "get-url", "origin"], timeout=5)
        if rc != 0:
            result["error"] = "no git remote named origin; add one with: git remote add origin <url>"
        else:
            rc, out = _git(["fetch", "--quiet", "origin"], timeout=timeout)
            if rc != 0:
                result["error"] = f"could not reach the remote: {out[-200:]}"
            else:
                rc, out = _git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], timeout=10)
                if rc != 0:
                    result["error"] = "this branch has no upstream; run: git branch --set-upstream-to origin/<branch>"
                else:
                    ahead, behind = (int(x) for x in out.split())
                    result["ahead"], result["behind"] = ahead, behind
                    rc, out = _git(["rev-parse", "--short", "@{upstream}"], timeout=5)
                    result["remote_commit"] = out if rc == 0 else None
    STATE.update(checked_at=result["checked_at"], behind=result["behind"], remote=result["remote_commit"], error=result["error"])
    return result


def _uv() -> str | None:
    for cand in (shutil.which("uv"), str(Path.home() / ".local" / "bin" / "uv"),
                 str(Path.home() / ".local" / "bin" / "uv.exe")):
        if cand and Path(cand).exists():
            return cand
    return None


def update(deps: bool = True, browser: bool = True, restart: bool = True, logger=None) -> tuple[bool, str]:
    """Pull, reinstall, migrate, restart. Returns (ok, log text)."""
    lines: list[str] = []

    def say(msg: str) -> None:
        lines.append(msg)
        (logger or log.info)(msg) if logger is None else logger(msg)

    root = repo_root()
    if root is None:
        return False, "not a git checkout"
    STATE.update(updating=True, step="pull", result=None, started_at=time.time(), last_log="")
    from_commit = current().commit
    try:
        rc, out = _git(["status", "--porcelain", "--untracked-files=no"], timeout=10)
        if rc == 0 and out.strip():
            say("note: local changes present; they are kept (pull is fast-forward only)")
        say("git pull --ff-only")
        rc, out = _git(["pull", "--ff-only", "--quiet"], timeout=120)
        say(out or "up to date")
        if rc != 0:
            STATE["result"] = "failed"
            return False, "\n".join(lines)
        STATE["step"] = "deps"
        if deps:
            uv = _uv()
            if uv:
                cmd = [uv, "pip", "install", "--python", sys.executable, "-q", "-e", ".[ocr]"]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "-q", "-e", ".[ocr]"]
            say("installing dependencies: " + " ".join(cmd[-3:]))
            r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                say((r.stdout + r.stderr)[-1500:])
                STATE["result"] = "failed"
                return False, "\n".join(lines)
        STATE["step"] = "browser"
        if browser:
            pw = Path(sys.executable).parent / ("playwright.exe" if sys.platform == "win32" else "playwright")
            if pw.exists():
                say("checking the Chromium download")
                subprocess.run([str(pw), "install", "chromium"], cwd=root, capture_output=True, text=True, timeout=900)
        STATE["step"] = "migrate"
        say("running database migrations")
        from .config import get_settings
        from .db.migrate import migrate
        migrate(get_settings().db_path)
        say(f"now at {current().commit or '?'}")
        STATE.update(behind=0, result="ok")
        if restart:
            STATE["step"] = "restart"
            say("restarting the server")
            try:
                import json
                _marker().parent.mkdir(parents=True, exist_ok=True)
                _marker().write_text(json.dumps({"started": time.time(), "from": from_commit}))
            except OSError:
                pass
            STATE["last_log"] = "\n".join(lines)
            threading.Timer(1.0, restart_server).start()
            return True, "\n".join(lines)
        STATE["step"] = "done"
        return True, "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        say(f"update failed: {type(e).__name__}: {e}")
        STATE["result"] = "failed"
        return False, "\n".join(lines)
    finally:
        if STATE.get("step") != "restart":
            STATE["updating"] = False
        STATE["last_log"] = "\n".join(lines)


def restart_server() -> None:
    """Restart through the service manager when we run under one, otherwise
    re-exec this process so the new code loads."""
    try:
        if os.environ.get("INVOCATION_ID") and shutil.which("systemctl"):
            scope = [] if Path("/etc/systemd/system/recipelib.service").exists() else ["--user"]
            subprocess.Popen(["systemctl", *scope, "restart", "recipelib"])
            return
        if sys.platform == "darwin" and Path("/Library/LaunchDaemons/com.recipelib.server.plist").exists():
            # KeepAlive brings the daemon straight back after we exit
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return
        if sys.platform == "darwin" and os.environ.get("XPC_SERVICE_NAME", "").startswith("com.recipelib"):
            subprocess.Popen(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.recipelib.server"])
            return
    except Exception:  # noqa: BLE001
        log.exception("service restart failed; re-exec instead")
    log.info("re-executing: %s", " ".join([sys.executable, *sys.argv]))
    if sys.platform == "win32":
        subprocess.Popen([sys.executable, *sys.argv], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        os._exit(0)
    os.execv(sys.executable, [sys.executable, *sys.argv])


class Checker:
    """Background daily check so the header can show an 'update available' badge."""

    def __init__(self, interval: float = 6 * 3600):
        self.interval = interval
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def start(self) -> None:
        if repo_root() is None:
            return
        self._t = threading.Thread(target=self._run, name="update-check", daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self._stop.wait(60)              # let startup settle
        while not self._stop.is_set():
            try:
                check()
            except Exception:  # noqa: BLE001
                log.exception("update check failed")
            self._stop.wait(self.interval)
