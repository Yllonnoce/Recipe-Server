"""Settings: a TOML file in the per-OS config dir, overridable by RECIPELIB_* env vars.

Static things (paths, ports, model) live here. Things a family member may
flip from the Settings page at runtime (printer name, OCR on/off) are stored
in the `settings` table and read through `domain.settings`.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "recipelib"


def config_path() -> Path:
    override = os.environ.get("RECIPELIB_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def _load_toml() -> dict[str, Any]:
    p = config_path()
    if not p.is_file():
        return {}
    with p.open("rb") as fh:
        return tomllib.load(fh)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECIPELIB_", extra="ignore")

    library_dir: Path = Field(default_factory=lambda: Path.home() / "RecipeLibrary")
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    auto_backup_days: float = 1.0       # 0 turns automatic backups off
    backup_keep: int = 7                # newest zips to keep in RecipeLibrary/backups
    ssl_certfile: Path | None = None    # set both to serve https (needed for the tablet wake lock)
    ssl_keyfile: Path | None = None

    # capture
    watch_interval: float = 5.0
    workers: int = 2
    paper: str = "letter"          # letter | a4 (default for URL rendering and the printer)
    ocr_enabled: bool = True
    shrink_files: bool = True       # recompress photos and downsample images inside PDFs on the way in
    image_max_px: int = 2000        # longest side of a stored photo
    jpeg_quality: int = 82
    pdf_image_dpi: int = 150        # images inside PDFs are downsampled to this (0 = leave PDFs alone)

    # virtual printer
    printer_enabled: bool = True
    printer_name: str = "Recipe Library"   # shown as "Recipe Library (<computer>)"; use {host} to place the name yourself
    printer_location: str = "Kitchen"
    ipp_port: int = 8631
    ipp_dump_dir: Path | None = None

    # LLM
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    llm_enabled: bool = True
    llm_keep_loaded: bool = True        # warm the model at startup and keep it in memory (faster captures, uses RAM)

    @property
    def db_path(self) -> Path:
        return self.library_dir / "recipes.db"

    @property
    def assets_dir(self) -> Path:
        return self.library_dir / "assets"

    @property
    def inbox_dir(self) -> Path:
        return self.library_dir / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.library_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (
            self.library_dir,
            self.assets_dir,
            self.inbox_dir,
            self.inbox_dir / "processed",
            self.inbox_dir / "failed",
            self.inbox_dir / "print",
            self.logs_dir,
            self.library_dir / "backups",
            self.library_dir / "tmp",
        ):
            d.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Env vars win over the TOML file, which wins over defaults."""
    global _settings
    if _settings is None or reload:
        data = _load_toml()
        # pydantic-settings gives init kwargs top priority, so drop any TOML
        # key that has an environment override
        for key in list(data):
            if f"RECIPELIB_{key.upper()}" in os.environ:
                del data[key]
        _settings = Settings(**data)
        _settings.library_dir = Path(_settings.library_dir).expanduser()
    return _settings


def write_default_config(force: bool = False) -> Path:
    p = config_path()
    if p.exists() and not force:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    s = Settings()
    p.write_text(
        "# Recipe Library configuration. Every key can also be set as an\n"
        "# environment variable with the RECIPELIB_ prefix, e.g. RECIPELIB_PORT=8080.\n\n"
        f'library_dir = "{Path(s.library_dir).expanduser().as_posix()}"\n'
        f'host = "{s.host}"\n'
        f"port = {s.port}\n\n"
        "# capture\n"
        f"workers = {s.workers}\n"
        f'paper = "{s.paper}"          # letter | a4\n'
        f"ocr_enabled = {str(s.ocr_enabled).lower()}\n\n"
        "# virtual printer (IPP + mDNS)\n"
        f"printer_enabled = {str(s.printer_enabled).lower()}\n"
        f'printer_name = "{s.printer_name}"   # devices see "Recipe Library (<computer name>)"; put {{host}} where you want the name\n'
        f'printer_location = "{s.printer_location}"\n'
        f"ipp_port = {s.ipp_port}\n\n"
        "# local LLM\n"
        f'ollama_host = "{s.ollama_host}"\n'
        f'ollama_model = "{s.ollama_model}"\n'
        f"llm_enabled = {str(s.llm_enabled).lower()}\n",
        encoding="utf-8",
    )
    return p
