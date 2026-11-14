from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session: sessionmaker | None = None


def init_engine(url: str | None = None):
    global _engine, _Session
    url = url or get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if path == ":memory:" or url.endswith(":memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _pragma(dbapi_conn, _):  # WAL: API y worker leen/escriben a la vez
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    from . import models  # noqa: F401

    Base.metadata.create_all(_engine)
    return _engine


_current: ContextVar[Session | None] = ContextVar("current_session", default=None)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Unidad de trabajo. Si ya hay una sesión activa en este contexto se reutiliza (sin commit propio):
    así la auditoría o los ajustes escritos dentro del motor van en la misma transacción y SQLite no se bloquea."""
    outer = _current.get()
    if outer is not None:
        yield outer
        return
    if _Session is None:
        init_engine()
    s = _Session()
    token = _current.set(s)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        _current.reset(token)
        s.close()
