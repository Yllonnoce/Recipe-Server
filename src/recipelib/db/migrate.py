"""Ordered SQL migrations applied by number; PRAGMA user_version remembers the last one."""
from __future__ import annotations

import logging
import re
import sqlite3
from importlib import resources
from pathlib import Path

log = logging.getLogger(__name__)
_NAME = re.compile(r"^(\d{4})_.*\.sql$")


def _migration_files() -> list[tuple[int, str, str]]:
    out = []
    root = resources.files("recipelib.db") / "migrations"
    for entry in root.iterdir():
        m = _NAME.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    return sorted(out)


def migrate(db_path: Path) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        current = con.execute("PRAGMA user_version").fetchone()[0]
        for num, name, sql in _migration_files():
            if num <= current:
                continue
            log.info("applying migration %s", name)
            con.executescript("BEGIN;\n" + sql + f"\nPRAGMA user_version={num};\nCOMMIT;")
            current = num
        return current
    finally:
        con.close()
