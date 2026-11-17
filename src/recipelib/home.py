"""`recipes home`: the home server from one menu, started from a desktop icon.

Same shape as RoadTales' `rt home`: a table of what is running (checked for
real, not assumed), then a numbered menu: start and stop, logs, the browser,
update, backup, doctor. `recipes home install` puts the icon on the desktop.
The pure parts (the table, the menu's choices, the icon, the launcher) are
plain functions with tests; the rest runs commands.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import get_settings

UNIT = "recipelib"
SYSTEM_UNIT = Path("/etc/systemd/system/recipelib.service")
TITLE = "Recipe Library home server"


@dataclass
class Check:
    name: str
    state: str          # ok | down | unknown | info (info: not something that runs, so no state word)
    detail: str


_WORD = {"ok": "running", "down": "DOWN", "unknown": "?", "info": ""}


def status_table(checks: list[Check]) -> str:
    """The table: names lined up, the state in one word, the detail as it came."""
    width = max(len(c.name) for c in checks)
    return "\n".join(f"  {c.name.ljust(width)}  {_WORD[c.state].ljust(8)} {c.detail}" for c in checks)


MENU: list[tuple[str, str, str]] = [
    ("1", "start", "Start"),
    ("2", "stop", "Stop"),
    ("3", "restart", "Restart"),
    ("4", "status", "Status (refresh)"),
    ("5", "logs", "Logs (follow; Ctrl-C comes back here)"),
    ("6", "browser", "Open in the browser"),
    ("7", "update", "Update"),
    ("8", "backup", "Backup now"),
    ("9", "doctor", "Doctor"),
    ("0", "quit", "Quit"),
]


def parse_choice(text: str) -> str | None:
    """A number or a letter from the menu; None for anything else."""
    key = text.strip().lower()
    if key == "q":
        return "quit"
    return next((action for k, action, _label in MENU if k == key), None)


def menu_lines() -> list[str]:
    left, right = MENU[:5], MENU[5:]
    return [f"  {left[i][0]}  {left[i][2].ljust(40)}{right[i][0]}  {right[i][2]}" for i in range(len(left))]


def desktop_entry(script: Path | str) -> str:
    """The desktop icon: a terminal that runs the launcher script."""
    return "\n".join(["[Desktop Entry]", "Name=Recipe Library Server",
                      "Comment=The recipe server: what is running, start and stop, logs, update, backup",
                      f"Exec={script}", "Terminal=true", "Type=Application", "Icon=utilities-terminal", ""])


def launcher_script(recipes_exe: Path | str) -> str:
    """The launcher: the menu, and when anything fails the words stay on screen until Enter
    (a desktop session has no shell profile, so the program is named in full)."""
    return "\n".join([
        "#!/usr/bin/env bash",
        "# Recipe Library home server menu (made by `recipes home install`)",
        f'"{recipes_exe}" home',
        "status=$?",
        'if [ $status -ne 0 ]; then echo; read -r -p "Press Enter to close"; fi',
        "exit $status",
        "",
    ])


# ---- running things --------------------------------------------------------

def _scope() -> list[str]:
    return [] if SYSTEM_UNIT.exists() else ["--user"]


def systemctl(args: list[str]) -> str:
    """`systemctl [--user] ...`; the output, or the error's words."""
    try:
        r = subprocess.run(["systemctl", *_scope(), *args], capture_output=True, text=True)
    except OSError as e:
        return str(e)
    return (r.stdout or r.stderr).strip()


def _get(url: str, timeout: float = 3.0, own_server: bool = False) -> httpx.Response | None:
    """A liveness probe. Only our own server on 127.0.0.1 is asked without checking its
    certificate (a home server's certificate is not issued for that address)."""
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True, verify=not own_server)
        return r if r.status_code < 500 else None
    except Exception:  # noqa: BLE001
        return None


def base_url(cfg) -> str:
    scheme = "https" if cfg.ssl_certfile and cfg.ssl_keyfile else "http"
    return f"{scheme}://127.0.0.1:{cfg.port}"


def _library(cfg) -> tuple[int | None, int, int]:
    """(recipes, capture jobs under way, capture jobs failed); recipes None when the database can't be read."""
    try:
        con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=2)
        try:
            n = con.execute("select count(*) from recipes where deleted_at is null").fetchone()[0]
            states = dict(con.execute("select state, count(*) from capture_jobs group by state").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        return None, 0, 0
    busy = sum(v for k, v in states.items() if k in ("queued", "running", "deferred"))
    return n, busy, states.get("failed", 0)


def gather_checks(cfg=None) -> list[Check]:
    """What is running, checked for real: the service, the ports it should answer on, Ollama, the library."""
    cfg = cfg or get_settings()
    url = base_url(cfg)
    web = _get(url + "/", own_server=True)
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        active = systemctl(["is-active", UNIT])
        at = systemctl(["show", UNIT, "-p", "ActiveEnterTimestamp", "--value"])
        since = f" · since {at}" if at else ""
        answering = f"{url} answering" if web else f"{url} NOT answering"
        server = Check("Server", "ok", answering + since) if active == "active" else Check("Server", "down", f"{active or 'not installed'} · {answering}")
    else:
        server = Check("Server", "ok" if web else "down", f"{url} {'answering' if web else 'NOT answering'}")
    checks = [server]

    if cfg.printer_enabled:
        try:
            from .ipp import client as IC
            from .ipp import codec as C
            r = IC.get_printer_attributes(f"ipp://127.0.0.1:{cfg.ipp_port}/ipp/print", ["printer-name"])
            name = r.group(C.PRINTER_GROUP).get("printer-name").value
            checks.append(Check("Printer", "ok", f"'{name}' on :{cfg.ipp_port}"))
        except Exception:  # noqa: BLE001
            checks.append(Check("Printer", "down", f":{cfg.ipp_port} not answering"))
    else:
        checks.append(Check("Printer", "info", "turned off in the config"))

    tags = _get(cfg.ollama_host.rstrip("/") + "/api/tags")
    if tags is None:
        checks.append(Check("Ollama", "down", f"{cfg.ollama_host} not answering"))
    else:
        try:
            models = [m["name"] for m in tags.json().get("models", [])]
        except Exception:  # noqa: BLE001
            models = []
        have = cfg.ollama_model in models or f"{cfg.ollama_model}:latest" in models
        checks.append(Check("Ollama", "ok" if have else "unknown",
                            cfg.ollama_model if have else f"{cfg.ollama_model} is not pulled ({len(models)} other model(s))"))

    n, busy, failed = _library(cfg)
    if n is None:
        checks.append(Check("Library", "unknown", f"{cfg.db_path} could not be read"))
    else:
        more = (f" · {busy} being captured" if busy else "") + (f" · {failed} capture(s) failed" if failed else "")
        checks.append(Check("Library", "info", f"{n} recipe{'' if n == 1 else 's'} in {cfg.library_dir}{more}"))

    if cfg.mirror_of:
        checks.append(Check("Mirror", "info", f"copies from {cfg.mirror_of} every {cfg.mirror_interval_hours:g} h ({cfg.mirror_mode})"))
    try:
        from . import updater
        v = updater.current()
        checks.append(Check("Version", "info", f"{v.version}" + (f" ({v.commit}, {v.date})" if v.commit else "")))
    except Exception:  # noqa: BLE001
        pass
    return checks


def control(verb: str, say=print, ask=input) -> None:
    """Start, stop or restart the service; it is not stopped while a recipe is being captured unless confirmed."""
    cfg = get_settings()
    if not (sys.platform.startswith("linux") and shutil.which("systemctl")):
        say(f"  {verb} from this menu needs systemd; here use: recipes service {'restart' if verb == 'restart' else 'install / remove'}")
        return
    if verb != "start":
        _n, busy, _failed = _library(cfg)
        if busy and systemctl(["is-active", UNIT]) == "active":
            answer = ask(f"  {busy} recipe{' is' if busy == 1 else 's are'} being captured. {verb} anyway? [y/N] ")
            if not answer.strip().lower().startswith("y"):
                say("  the server was left alone")
                return
    cmd = ["systemctl", *_scope(), verb, UNIT]
    r = subprocess.run((["sudo"] if not _scope() else []) + cmd, capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip()
    if r.returncode == 0 and verb != "stop":
        for _ in range(20):                         # the web app takes a few seconds to answer
            if _get(base_url(cfg) + "/", timeout=1.0, own_server=True):
                break
            time.sleep(0.5)
    say(f"  {verb} {UNIT}: {out or systemctl(['is-active', UNIT])}")


def follow_log(lines: int = 40) -> None:
    """The server log, followed, in this terminal; Ctrl-C ends it."""
    log = get_settings().logs_dir / "recipelib.log"
    if not log.exists():
        print(f"  no log yet at {log}")
        return
    from collections import deque
    try:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for ln in deque(fh, maxlen=lines):
                print(ln.rstrip())
            while True:
                ln = fh.readline()
                if ln:
                    print(ln.rstrip(), flush=True)
                else:
                    time.sleep(0.4)
    except KeyboardInterrupt:
        print()


def _in_terminal(args: list[str]) -> None:
    """A `recipes` command in this terminal, back here when it ends. Ctrl-C ends the command, not the menu."""
    exe = Path(sys.executable).parent / ("recipes.exe" if sys.platform == "win32" else "recipes")
    cmd = [str(exe), *args] if exe.exists() else [sys.executable, "-m", "recipelib.cli", *args]
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


def open_browser(cfg=None) -> str:
    import webbrowser
    cfg = cfg or get_settings()
    url = base_url(cfg).replace("127.0.0.1", "localhost") + "/"
    webbrowser.open(url)
    return url


def install(say=print) -> None:
    """Put the launcher and its icon in place."""
    exe = Path(sys.executable).parent / ("recipes.exe" if sys.platform == "win32" else "recipes")
    desktop = Path.home() / "Desktop"
    if sys.platform == "win32":
        target = (desktop if desktop.is_dir() else Path.home()) / "Recipe Library Server.bat"
        target.write_text(f'@echo off\r\n"{exe}" home\r\nif errorlevel 1 pause\r\n', encoding="utf-8")
        say(f"  icon: {target}")
        return
    if sys.platform == "darwin":
        target = (desktop if desktop.is_dir() else Path.home()) / "Recipe Library Server.command"
        target.write_text(launcher_script(exe), encoding="utf-8")
        target.chmod(0o755)
        say(f"  icon: {target}")
        return
    share = Path.home() / ".local" / "share" / "recipelib"
    share.mkdir(parents=True, exist_ok=True)
    script = share / "recipes-home.sh"
    script.write_text(launcher_script(exe), encoding="utf-8")
    script.chmod(0o755)
    say(f"  wrote {script}")
    if desktop.is_dir():
        icon = desktop / "Recipe Library Server.desktop"
        icon.write_text(desktop_entry(script), encoding="utf-8")
        icon.chmod(0o755)
        say(f"  icon: {icon}")
    else:
        say(f"  no Desktop folder; the launcher is {script}")


def menu(say=print, ask=input) -> None:
    """The menu: the table, a choice, the action, again."""
    while True:
        say("")
        say(TITLE)
        say("")
        say(status_table(gather_checks()))
        say("")
        for line in menu_lines():
            say(line)
        say("")
        try:
            choice = parse_choice(ask("> "))
        except (EOFError, KeyboardInterrupt):
            say("")
            return
        if choice is None or choice == "status":
            continue
        if choice == "quit":
            return
        if choice in ("start", "stop", "restart"):
            control(choice, say, ask)
        elif choice == "logs":
            follow_log()
        elif choice == "browser":
            say(f"  opened {open_browser()}")
        elif choice == "update":
            _in_terminal(["update"])
        elif choice == "backup":
            _in_terminal(["backup"])
        elif choice == "doctor":
            _in_terminal(["doctor"])
