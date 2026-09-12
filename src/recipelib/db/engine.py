"""SQLAlchemy engine + sessions for the single SQLite file.

WAL mode, a busy timeout and one session per unit of work keep the web
threads, the capture workers and the printer thread from tripping over each
other. Writers should keep transactions short.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def init_engine(db_path: Path) -> Engine:
    global _engine, _Session
    url = f"sqlite:///{db_path.as_posix()}"
    _engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 10},
        pool_pre_ping=True,
        future=True,
    )

    @event.listens_for(_engine, "connect")
    def _pragmas(dbapi_con, _rec):  # noqa: ANN001
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database not initialised; call init_engine() first")
    return _engine


def new_session() -> Session:
    if _Session is None:
        raise RuntimeError("database not initialised; call init_engine() first")
    return _Session()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
