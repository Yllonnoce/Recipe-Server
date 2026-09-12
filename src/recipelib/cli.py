"""`recipes` command line: init, serve, doctor, import, backup, service-template."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer

from . import __version__
from .config import config_path, get_settings, write_default_config

cli = typer.Typer(add_completion=False, help="Recipe Library home server.")


@cli.command()
def init(force: bool = typer.Option(False, help="overwrite an existing config file")):
    """Write the config file and create the library folders."""
    p = write_default_config(force=force)
    cfg = get_settings(reload=True)
    cfg.ensure_dirs()
    from .db.migrate import migrate
    v = migrate(cfg.db_path)
    typer.echo(f"config:  {p}")
    typer.echo(f"library: {cfg.library_dir}  (schema v{v})")
    typer.echo("next:    recipes serve")


@cli.command()
def serve(host: str | None = None, port: int | None = None, ipp_port: int | None = None,
          no_printer: bool = typer.Option(False, "--no-printer"), reload: bool = False,
          dump_ipp: Path | None = typer.Option(None, help="write raw IPP requests here (debugging)")):
    """Run the web app, capture workers and virtual printer."""
    import os
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
def backup(out: Path | None = None):
    """Zip the database and assets into the backups folder."""
    import sqlite3
    import zipfile
    from datetime import datetime
    cfg = get_settings()
    cfg.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out or (cfg.library_dir / "backups" / f"recipelib-{stamp}.zip")
    snap = cfg.library_dir / "tmp" / f"recipes-{stamp}.db"
    src = sqlite3.connect(str(cfg.db_path))
    dst = sqlite3.connect(str(snap))
    src.backup(dst)
    dst.close()
    src.close()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "recipes.db")
        for f in cfg.assets_dir.rglob("*"):
            if f.is_file():
                z.write(f, Path("assets") / f.relative_to(cfg.assets_dir))
    snap.unlink(missing_ok=True)
    typer.echo(f"wrote {out}")


@cli.command("printer-test")
def printer_test(uri: str | None = typer.Option(None, help="ipp://host:port/ipp/print (default: this machine)"),
                 file: Path | None = typer.Option(None, help="PDF to send; default is a generated sample")):
    """Talk to the virtual printer like a client would: attributes, then a Print-Job."""
    from .ipp import client as IC
    from .ipp import codec as C
    cfg = get_settings()
    uri = uri or f"ipp://127.0.0.1:{cfg.ipp_port}/ipp/print"
    typer.echo(f"Get-Printer-Attributes {uri}")
    r = IC.get_printer_attributes(uri, ["printer-name", "printer-uuid", "document-format-supported", "urf-supported", "media-default"])
    pg = r.group(C.PRINTER_GROUP)
    for a in pg.attrs:
        typer.echo(f"  {a.name} = {', '.join(str(v) for v in a.values)}")
    if file is None:
        import pymupdf
        d = pymupdf.open()
        pg_ = d.new_page()
        pg_.insert_text((72, 72), "Recipe Library printer test\n2 cups flour\n1 tsp salt", fontsize=14)
        data = d.tobytes()
        name = "Printer test page"
    else:
        data = file.read_bytes()
        name = file.stem
    r = IC.print_job(uri, data, "application/pdf", name=name, user="recipes-cli")
    jg = r.group(C.JOB_GROUP)
    typer.echo(f"Print-Job -> status {r.code:#06x}, job-id {jg.get('job-id').value if jg else '?'}, "
               f"state {jg.get('job-state').value if jg else '?'} (9 = completed)")
    typer.echo("Check the Add page in the web UI; the job should appear within a few seconds.")


@cli.command("service-template")
def service_template(kind: str = typer.Argument(..., help="systemd | launchd | windows-task | avahi")):
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
        raise typer.BadParameter("kind must be systemd, launchd, windows-task or avahi")


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
