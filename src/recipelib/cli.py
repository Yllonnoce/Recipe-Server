"""`recipes` command line: init, serve, doctor, import, backup, service-template."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import typer

from . import __version__
from .config import Settings, config_path, get_settings, write_default_config

cli = typer.Typer(add_completion=False, help="Recipe Library home server.")


@cli.command()
def init(force: bool = typer.Option(False, help="overwrite an existing config file")):
    """Write the config file and create the library folders."""
    p = write_default_config(force=force)
    cfg = get_settings(reload=True)
    cfg.ensure_dirs()
    from .db.engine import init_engine, session_scope
    from .db.migrate import migrate
    from .domain import recipes as R
    v = migrate(cfg.db_path)
    init_engine(cfg.db_path)
    with session_scope() as s:
        R.seed_categories(s)
        R.categorize_missing(s)
    typer.echo(f"config:  {p}")
    typer.echo(f"library: {cfg.library_dir}  (schema v{v})")
    typer.echo("next:    recipes serve")


@cli.command()
def serve(host: str | None = None, port: int | None = None, ipp_port: int | None = None,
          no_printer: bool = typer.Option(False, "--no-printer"), reload: bool = False,
          dump_ipp: Path | None = typer.Option(None, help="write raw IPP requests here (debugging)")):
    """Run the web app, capture workers and virtual printer."""
    if host:
        os.environ["RECIPELIB_HOST"] = host
    if port:
        os.environ["RECIPELIB_PORT"] = str(port)
    if ipp_port:
        os.environ["RECIPELIB_IPP_PORT"] = str(ipp_port)
    if no_printer:
        os.environ["RECIPELIB_PRINTER_ENABLED"] = "false"
    if dump_ipp:
        os.environ["RECIPELIB_IPP_DUMP_DIR"] = str(dump_ipp)
    cfg = get_settings(reload=True)
    cfg.ensure_dirs()
    import uvicorn
    ssl = {}
    if cfg.ssl_certfile and cfg.ssl_keyfile:
        ssl = {"ssl_certfile": str(cfg.ssl_certfile), "ssl_keyfile": str(cfg.ssl_keyfile)}
    # log_config=None: uvicorn writes through our root logger (file + console when there is one)
    uvicorn.run("recipelib.app:app", host=cfg.host, port=cfg.port, reload=reload,
                log_level=cfg.log_level.lower(), access_log=False, log_config=None, **ssl)


@cli.command()
def doctor():
    """Check Python, SQLite FTS5, PyMuPDF, OCR, Playwright, Ollama and ports."""
    from .doctor import run_checks
    bad = 0
    for c in run_checks():
        typer.echo(f"{'OK ' if c.ok else '!! '} {c.name:<20} {c.detail}")
        bad += 0 if c.ok else 1
    typer.echo(f"\nconfig: {config_path()}")
    raise typer.Exit(code=1 if bad else 0)


@cli.command("import")
def import_(paths: list[Path]):
    """Copy PDFs/images into the inbox folder so the running server ingests them."""
    cfg = get_settings()
    cfg.ensure_dirs()
    n = 0
    for p in paths:
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
        for f in files:
            if f.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"):
                shutil.copy2(f, cfg.inbox_dir / f.name)
                n += 1
    typer.echo(f"copied {n} file(s) into {cfg.inbox_dir}")


@cli.command()
def categorize(replace: bool = typer.Option(False, help="drop existing categories and start over")):
    """Auto-assign categories (Dinner, Dessert, Seafood…) to every recipe."""
    from .db.engine import init_engine, session_scope
    from .db.migrate import migrate
    from .domain import recipes as R
    cfg = get_settings()
    migrate(cfg.db_path)
    init_engine(cfg.db_path)
    with session_scope() as s:
        n = R.categorize_all(s, replace=replace)
    typer.echo(f"categorised {n} recipe(s)")


@cli.command()
def update(no_restart: bool = typer.Option(False, "--no-restart", help="don't restart the running server"),
           check_only: bool = typer.Option(False, "--check", help="only report whether an update exists")):
    """Pull the latest version from git, install it, migrate, restart the service."""
    from . import updater
    v = updater.current()
    typer.echo(f"installed: {v.version} {v.commit or ''} {('on ' + v.branch) if v.branch else ''}")
    r = updater.check()
    if r["error"]:
        typer.echo("!! " + r["error"])
        raise typer.Exit(code=1)
    typer.echo("up to date" if not r["behind"] else f"{r['behind']} change(s) available ({r['remote_commit']})")
    if check_only or not r["behind"]:
        return
    ok, out = updater.update(restart=False, logger=typer.echo)
    if not ok:
        raise typer.Exit(code=1)
    if not no_restart:
        _restart_service()


def _restart_service() -> None:
    import shutil
    import subprocess
    if sys.platform.startswith("linux") and SYSTEM_UNIT.exists():
        _sudo(["systemctl", "restart", "recipelib"]); typer.echo("restarted the system service"); return
    if sys.platform == "darwin" and SYSTEM_PLIST.exists():
        _sudo(["launchctl", "kickstart", "-k", "system/com.recipelib.server"]); typer.echo("restarted the system daemon"); return
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        if subprocess.run(["systemctl", "--user", "is-enabled", "recipelib"], capture_output=True).returncode == 0:
            subprocess.run(["systemctl", "--user", "restart", "recipelib"])
            typer.echo("restarted the systemd service")
            return
    elif sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/com.recipelib.server.plist"
        if plist.exists():
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.recipelib.server"])
            typer.echo("restarted the launchd agent")
            return
    elif sys.platform == "win32":
        r = subprocess.run(["schtasks", "/Query", "/TN", "Recipe Library"], capture_output=True)
        if r.returncode == 0:
            subprocess.run(["schtasks", "/End", "/TN", "Recipe Library"], capture_output=True)
            subprocess.run(["schtasks", "/Run", "/TN", "Recipe Library"], capture_output=True)
            typer.echo("restarted the scheduled task")
            return
    typer.echo("restart the server to load the new version (recipes serve)")


@cli.command()
def version():
    """Show the installed version and git commit."""
    from . import updater
    v = updater.current()
    typer.echo(f"{v.version}" + (f" ({v.commit}, {v.date}, {v.branch})" if v.commit else ""))


@cli.command()
def backup(out: Path | None = typer.Option(None, help="zip path (default: RecipeLibrary/backups/recipelib-<date>.zip)"),
           no_assets: bool = typer.Option(False, "--no-assets", help="database only, no PDFs/images")):
    """Zip the database and all PDFs/images."""
    from .backup import create_backup
    cfg = get_settings()
    cfg.ensure_dirs()
    p = create_backup(out, include_assets=not no_assets)
    typer.echo(f"wrote {p} ({p.stat().st_size // 1024 // 1024} MB)")


@cli.command()
def restore(zip_path: Path, replace: bool = typer.Option(False, help="wipe the library first instead of merging"),
            overwrite: bool = typer.Option(False, help="when merging, let backup recipes replace matching local ones")):
    """Merge a backup into the library (or replace the library with it)."""
    from .backup import restore as do_restore
    from .db.engine import init_engine
    from .db.migrate import migrate
    cfg = get_settings()
    cfg.ensure_dirs()
    migrate(cfg.db_path)
    init_engine(cfg.db_path)
    if replace and not typer.confirm("This deletes every recipe currently in the library first. Continue?"):
        raise typer.Abort()
    stats = do_restore(zip_path, mode="replace" if replace else "merge", overwrite=overwrite)
    typer.echo(stats.summary())


config_cli = typer.Typer(help="Read or change the config file.")
cli.add_typer(config_cli, name="config")


@config_cli.command("show")
def config_show():
    """Print the config file."""
    p = config_path()
    typer.echo(f"# {p}")
    typer.echo(p.read_text(encoding="utf-8") if p.exists() else "# (no config file yet; run: recipes init)")


@config_cli.command("set")
def config_set(key: str, value: str):
    """Set one key, e.g. `recipes config set port 80`. Keeps the rest of the file."""
    import re
    p = config_path()
    if not p.exists():
        write_default_config()
    text = p.read_text(encoding="utf-8")
    if key not in Settings.model_fields:
        raise typer.BadParameter(f"unknown key {key}; known: {', '.join(Settings.model_fields)}")
    if value.lower() in ("true", "false"):
        lit = value.lower()
    elif re.fullmatch(r"-?\d+(\.\d+)?", value):
        lit = value
    else:
        lit = '"' + value.replace('"', '\\"') + '"'
    line = f"{key} = {lit}"
    pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    text = pat.sub(line, text, count=1) if pat.search(text) else text.rstrip("\n") + f"\n{line}\n"
    p.write_text(text, encoding="utf-8")
    typer.echo(line)


service_cli = typer.Typer(help="Run the server as a background service that starts at login.")
cli.add_typer(service_cli, name="service")


def _service_paths() -> dict:
    exe = Path(sys.executable).parent / ("recipes.exe" if sys.platform == "win32" else "recipes")
    return {
        "exe": exe,
        "pythonw": Path(sys.executable).parent / "pythonw.exe",
        "unit": Path.home() / ".config/systemd/user/recipelib.service",
        "plist": Path.home() / "Library/LaunchAgents/com.recipelib.server.plist",
        "task": "Recipe Library",
    }


def _run(cmd: list[str], quiet: bool = False) -> int:
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not quiet and (r.stdout or r.stderr).strip():
        typer.echo((r.stdout + r.stderr).strip())
    return r.returncode


SYSTEM_UNIT = Path("/etc/systemd/system/recipelib.service")
SYSTEM_PLIST = Path("/Library/LaunchDaemons/com.recipelib.server.plist")
OLLAMA_PLIST = Path("/Library/LaunchDaemons/com.recipelib.ollama.plist")


def _ollama_daemon_macos(user: str, home: Path) -> None:
    """Ollama installed as the Mac app only runs after login. When the server
    runs at boot, run Ollama at boot too (as the user, so ~/.ollama models are reused)."""
    import subprocess
    import tempfile
    if subprocess.run(["sudo", "-n", "launchctl", "print", "system/homebrew.mxcl.ollama"], capture_output=True).returncode == 0:
        return                      # Homebrew's own daemon is already handling it
    binary = shutil.which("ollama") or next((str(c) for c in (
        Path("/Applications/Ollama.app/Contents/Resources/ollama"), Path("/usr/local/bin/ollama"),
        Path("/opt/homebrew/bin/ollama")) if c.exists()), None)
    if not binary:
        typer.echo("note: Ollama not found; install it from https://ollama.com and re-run to make it start at boot")
        return
    binary = os.path.realpath(binary)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.recipelib.ollama</string>
  <key>ProgramArguments</key><array><string>{binary}</string><string>serve</string></array>
  <key>UserName</key><string>{user}</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>{home}</string><key>OLLAMA_HOST</key><string>127.0.0.1:11434</string></dict>
  <key>WorkingDirectory</key><string>{home}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{home / 'RecipeLibrary/logs/ollama.log'}</string>
  <key>StandardErrorPath</key><string>{home / 'RecipeLibrary/logs/ollama.log'}</string>
</dict></plist>
"""
    tmp = Path(tempfile.mkstemp(suffix=".plist")[1]); tmp.write_text(plist)
    _sudo(["install", "-o", "root", "-g", "wheel", "-m", "644", str(tmp), str(OLLAMA_PLIST)]); tmp.unlink()
    _launchd_bootstrap_system(OLLAMA_PLIST, "com.recipelib.ollama")
    typer.echo(f"Ollama will start at boot too ({binary}); quit the menu-bar Ollama app to avoid running two copies")


def _sudo(cmd: list[str], quiet: bool = False) -> int:
    import subprocess
    if quiet:
        return subprocess.run(["sudo", *cmd], capture_output=True).returncode
    return subprocess.run(["sudo", *cmd]).returncode


def _launchd_bootstrap_system(plist: Path, label: str) -> None:
    """bootout is asynchronous: launchd keeps the label until the old process
    has exited, and a bootstrap in that window fails with 'Input/output error'.
    Wait for the label to disappear, then bootstrap with a couple of retries."""
    import subprocess
    import time
    _sudo(["launchctl", "bootout", f"system/{label}"], quiet=True)
    for _ in range(30):
        if subprocess.run(["sudo", "-n", "launchctl", "print", f"system/{label}"], capture_output=True).returncode != 0:
            break
        time.sleep(0.5)
    _sudo(["launchctl", "enable", f"system/{label}"], quiet=True)
    for attempt in range(4):
        if _sudo(["launchctl", "bootstrap", "system", str(plist)], quiet=attempt < 3) == 0:
            return
        time.sleep(2)
    if _sudo(["launchctl", "load", "-w", str(plist)]) != 0:
        typer.echo(f"!! launchd would not load {plist}; try: sudo launchctl bootstrap system {plist}", err=True)


def _install_system() -> None:
    """A boot-time system service that runs the app as this user (no login needed).
    Needs sudo for the few privileged steps; asks in the terminal."""
    import subprocess
    import tempfile
    sp = _service_paths()
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    home = Path.home()
    if sys.platform.startswith("linux"):
        unit = f"""[Unit]
Description=Recipe Library
After=network-online.target
Wants=network-online.target

[Service]
User={user}
Environment=HOME={home}
WorkingDirectory={home}
ExecStart={sp['exe']} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        tmp = Path(tempfile.mkstemp(suffix=".service")[1]); tmp.write_text(unit)
        _sudo(["install", "-m", "644", str(tmp), str(SYSTEM_UNIT)]); tmp.unlink()
        _sudo(["systemctl", "daemon-reload"])
        _sudo(["systemctl", "enable", "--now", "recipelib"])
        typer.echo(f"installed {SYSTEM_UNIT} (starts at boot as {user})\nstatus: systemctl status recipelib")
    elif sys.platform == "darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.recipelib.server</string>
  <key>ProgramArguments</key><array><string>{sp['exe']}</string><string>serve</string></array>
  <key>UserName</key><string>{user}</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>{home}</string><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>WorkingDirectory</key><string>{home}</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{home / 'RecipeLibrary/logs/launchd.log'}</string>
  <key>StandardErrorPath</key><string>{home / 'RecipeLibrary/logs/launchd.log'}</string>
</dict></plist>
"""
        tmp = Path(tempfile.mkstemp(suffix=".plist")[1]); tmp.write_text(plist)
        # a login agent with the same label would fight the daemon; take it out
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/com.recipelib.server"], quiet=True)
        if sp["plist"].exists():
            sp["plist"].unlink()
        _sudo(["install", "-o", "root", "-g", "wheel", "-m", "644", str(tmp), str(SYSTEM_PLIST)]); tmp.unlink()
        _launchd_bootstrap_system(SYSTEM_PLIST, "com.recipelib.server")
        _ollama_daemon_macos(user, home)
        typer.echo(f"installed {SYSTEM_PLIST} (starts at boot, runs as {user}, no login needed)\nstatus: recipes service status")
    else:
        raise typer.Exit("--system is for Linux and macOS; on Windows the scheduled task already starts at logon")


@service_cli.command("install")
def service_install(system: bool = typer.Option(False, "--system", help="start at boot without anyone logging in (Linux/macOS, asks for sudo)")):
    """Install and start the service (systemd user unit / launchd agent / Task Scheduler)."""
    import subprocess
    sp = _service_paths()
    if sys.platform != "win32" and os.geteuid() == 0:
        typer.echo("!! Run this as your own user, not root/sudo (add --system for a boot-time service; it asks for sudo itself).", err=True)
        raise typer.Exit(code=1)
    if system:
        _install_system()
        return
    if sys.platform.startswith("linux"):
        import shutil
        if not shutil.which("systemctl"):
            raise typer.Exit("systemd not found; run `recipes serve` from your own service manager")
        sp["unit"].parent.mkdir(parents=True, exist_ok=True)
        sp["unit"].write_text(f"""[Unit]
Description=Recipe Library
After=network-online.target

[Service]
ExecStart={sp['exe']} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", "recipelib"])
        if _run(["loginctl", "enable-linger", os.environ.get("USER", "")], quiet=True) != 0:
            typer.echo("note: run `sudo loginctl enable-linger $USER` so the service keeps running after you log out")
        typer.echo(f"installed {sp['unit']}\nstatus: systemctl --user status recipelib   logs: journalctl --user -u recipelib -f")
    elif sys.platform == "darwin":
        sp["plist"].parent.mkdir(parents=True, exist_ok=True)
        sp["plist"].write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.recipelib.server</string>
  <key>ProgramArguments</key><array><string>{sp['exe']}</string><string>serve</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{Path.home() / 'RecipeLibrary/logs/launchd.log'}</string>
  <key>StandardErrorPath</key><string>{Path.home() / 'RecipeLibrary/logs/launchd.log'}</string>
</dict></plist>
""")
        uid = os.getuid()
        # launchd ignores agents the user does not own, and refuses ones on its disabled list
        try:
            os.chmod(sp["plist"], 0o644)
            if sp["plist"].stat().st_uid != uid:
                typer.echo(f"!! {sp['plist']} is not owned by you (installed with sudo?). Fix with:\n"
                           f"   sudo chown {os.environ.get('USER', '$USER')} \"{sp['plist']}\"", err=True)
        except OSError:
            pass
        _run(["launchctl", "bootout", f"gui/{uid}/com.recipelib.server"], quiet=True)
        for dom in ("user", "gui"):          # which domain accepts enable varies by macOS version
            _run(["launchctl", "enable", f"{dom}/{uid}/com.recipelib.server"], quiet=True)
        if _run(["launchctl", "bootstrap", f"gui/{uid}", str(sp["plist"])]) != 0:
            _run(["launchctl", "load", "-w", str(sp["plist"])])
        _run(["launchctl", "kickstart", f"gui/{uid}/com.recipelib.server"], quiet=True)
        typer.echo(f"installed {sp['plist']}\nstatus: recipes service status   stop: recipes service remove")
    elif sys.platform == "win32":
        launcher = sp["pythonw"] if sp["pythonw"].exists() else sp["exe"]
        tr = f'"{launcher}" -m recipelib.cli serve' if launcher == sp["pythonw"] else f'"{launcher}" serve'
        _run(["schtasks", "/Delete", "/TN", sp["task"], "/F"], quiet=True)
        rc = _run(["schtasks", "/Create", "/TN", sp["task"], "/SC", "ONLOGON", "/RL", "LIMITED", "/F", "/TR", tr])
        if rc != 0:
            raise typer.Exit(code=rc)
        _run(["schtasks", "/Run", "/TN", sp["task"]], quiet=True)
        typer.echo(f"installed scheduled task '{sp['task']}' (starts at login, no window)")
    else:
        raise typer.Exit("unsupported platform")


@service_cli.command("remove")
def service_remove():
    """Stop and remove the service (user or system). The app and your recipes stay."""
    sp = _service_paths()
    if sys.platform.startswith("linux") and SYSTEM_UNIT.exists():
        _sudo(["systemctl", "disable", "--now", "recipelib"]); _sudo(["rm", "-f", str(SYSTEM_UNIT)]); _sudo(["systemctl", "daemon-reload"])
        typer.echo("removed the system service")
    if sys.platform == "darwin" and SYSTEM_PLIST.exists():
        _sudo(["launchctl", "bootout", "system/com.recipelib.server"]); _sudo(["rm", "-f", str(SYSTEM_PLIST)])
        typer.echo("removed the system daemon")
    if sys.platform == "darwin" and OLLAMA_PLIST.exists():
        _sudo(["launchctl", "bootout", "system/com.recipelib.ollama"]); _sudo(["rm", "-f", str(OLLAMA_PLIST)])
        typer.echo("removed the Ollama boot daemon (the Ollama app itself is untouched)")
    if sys.platform.startswith("linux"):
        _run(["systemctl", "--user", "disable", "--now", "recipelib"], quiet=True)
        if sp["unit"].exists():
            sp["unit"].unlink()
        _run(["systemctl", "--user", "daemon-reload"], quiet=True)
        typer.echo("removed the systemd user service")
    elif sys.platform == "darwin":
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/com.recipelib.server"], quiet=True)
        _run(["launchctl", "unload", str(sp["plist"])], quiet=True)
        if sp["plist"].exists():
            sp["plist"].unlink()
        typer.echo("removed the launchd agent")
    elif sys.platform == "win32":
        _run(["schtasks", "/End", "/TN", sp["task"]], quiet=True)
        _run(["schtasks", "/Delete", "/TN", sp["task"], "/F"], quiet=True)
        typer.echo("removed the scheduled task")


@service_cli.command("status")
def service_status():
    """Is the service installed and running?"""
    sp = _service_paths()
    if sys.platform.startswith("linux") and SYSTEM_UNIT.exists():
        _run(["systemctl", "--no-pager", "status", "recipelib"])
        return
    if sys.platform == "darwin" and SYSTEM_PLIST.exists():
        import subprocess
        r = subprocess.run(["sudo", "-n", "launchctl", "print", "system/com.recipelib.server"], capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["launchctl", "print", "system/com.recipelib.server"], capture_output=True, text=True)
        if r.returncode != 0:
            typer.echo("system daemon installed but not loaded. Run: recipes service install --system")
            raise typer.Exit(code=1)
        pid = next((ln.split("=")[1].strip() for ln in r.stdout.splitlines() if ln.strip().startswith("pid =")), "?")
        typer.echo(f"system daemon {'running' if 'state = running' in r.stdout else 'loaded'}, pid {pid} (starts at boot)")
        typer.echo(f"log: {Path.home() / 'RecipeLibrary/logs/launchd.log'}")
        return
    if sys.platform.startswith("linux"):
        if not sp["unit"].exists():
            typer.echo("not installed (recipes service install)")
            raise typer.Exit(code=1)
        _run(["systemctl", "--user", "--no-pager", "status", "recipelib"])
    elif sys.platform == "darwin":
        if not sp["plist"].exists():
            typer.echo("not installed (recipes service install)")
            raise typer.Exit(code=1)
        import subprocess
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.recipelib.server"], capture_output=True, text=True)
        if r.returncode != 0:
            typer.echo("installed but NOT loaded into your session. Run: recipes service install\n"
                       "(a login agent only starts once someone logs in at the Mac; for a server that boots\n"
                       " to the login window or is managed over SSH use: recipes service install --system)")
            try:
                st = sp["plist"].stat()
                if st.st_uid != os.getuid():
                    typer.echo(f"cause: {sp['plist']} is owned by uid {st.st_uid}, not you. Fix: sudo chown $USER \"{sp['plist']}\"")
            except OSError:
                pass
            d = subprocess.run(["launchctl", "print-disabled", f"gui/{os.getuid()}"], capture_output=True, text=True).stdout
            if '"com.recipelib.server" => disabled' in d or '"com.recipelib.server" => true' in d:
                typer.echo("cause: the agent is on launchd's disabled list. Fix: launchctl enable user/$(id -u)/com.recipelib.server")
            raise typer.Exit(code=1)
        state = "running" if "state = running" in r.stdout else "loaded (not running)"
        pid = next((ln.split("=")[1].strip() for ln in r.stdout.splitlines() if ln.strip().startswith("pid =")), "?")
        typer.echo(f"{state}, pid {pid}")
        typer.echo(f"log: {Path.home() / 'RecipeLibrary/logs/launchd.log'}")
    elif sys.platform == "win32":
        _run(["schtasks", "/Query", "/TN", sp["task"], "/FO", "LIST"])


@service_cli.command("restart")
def service_restart():
    """Restart the running service."""
    _restart_service()


@service_cli.command("logs")
def service_logs(lines: int = 50):
    """Show the last lines of the server log."""
    cfg = get_settings()
    log = cfg.logs_dir / "recipelib.log"
    if not log.exists():
        typer.echo(f"no log yet at {log}")
        return
    from collections import deque
    with log.open(encoding="utf-8", errors="replace") as fh:
        for ln in deque(fh, maxlen=lines):
            typer.echo(ln.rstrip())


@cli.command("service-template")
def service_template(kind: str = typer.Argument(..., help="systemd | launchd | windows-task | nginx | avahi")):
    """Print a service definition for this machine's paths."""
    cfg = get_settings()
    exe = Path(sys.executable).parent / ("recipes.exe" if sys.platform == "win32" else "recipes")
    if kind == "systemd":
        typer.echo(f"""[Unit]
Description=Recipe Library
After=network-online.target

[Service]
ExecStart={exe} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target

# save as ~/.config/systemd/user/recipelib.service then:
#   systemctl --user daemon-reload && systemctl --user enable --now recipelib
#   loginctl enable-linger $USER   # keep it running after logout""")
    elif kind == "launchd":
        typer.echo(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.recipelib.server</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>serve</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
<!-- save as ~/Library/LaunchAgents/com.recipelib.server.plist; launchctl load it -->""")
    elif kind == "windows-task":
        typer.echo(f"""schtasks /Create /TN "Recipe Library" /SC ONSTART /RU "%USERNAME%" /RL LIMITED ^
  /TR "\\"{exe}\\" serve" /F
rem Or install as a true service with NSSM:  nssm install RecipeLibrary "{exe}" serve""")
    elif kind == "nginx":
        typer.echo(f"""# Recipe Library behind nginx on port 80 -> the app on 127.0.0.1:{cfg.port}
server {{
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # big PDF and photo uploads (the app itself caps at 300 MB) and slow captures
    client_max_body_size 500m;
    proxy_request_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {{
        proxy_pass http://127.0.0.1:{cfg.port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }}
}}
# Debian/Ubuntu: /etc/nginx/sites-available/recipelib (+ symlink in sites-enabled, remove 'default')
# Fedora/RHEL:   /etc/nginx/conf.d/recipelib.conf
# macOS (brew):  $(brew --prefix)/etc/nginx/servers/recipelib.conf
# then: sudo nginx -t && sudo systemctl reload nginx""")
    elif kind == "avahi":
        typer.echo(f"""<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{cfg.printer_name}</name>
  <service><type>_ipp._tcp</type><subtype>_universal._sub._ipp._tcp</subtype><port>{cfg.ipp_port}</port>
    <txt-record>txtvers=1</txt-record><txt-record>qtotal=1</txt-record><txt-record>rp=ipp/print</txt-record>
    <txt-record>ty={cfg.printer_name}</txt-record><txt-record>pdl=application/pdf,image/urf,image/pwg-raster,image/jpeg</txt-record>
    <txt-record>URF=W8,SRGB24,CP1,RS300,V1.4,DM1,IS1</txt-record><txt-record>Color=T</txt-record><txt-record>Duplex=F</txt-record>
  </service>
</service-group>
<!-- save as /etc/avahi/services/recipelib.service if python-zeroconf and avahi conflict -->""")
    else:
        raise typer.BadParameter("kind must be systemd, launchd, windows-task, nginx or avahi")


@cli.callback(invoke_without_command=True)
def _root(ctx: typer.Context, version: bool = typer.Option(False, "--version")):
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
