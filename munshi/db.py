"""Thin SQLite access layer. stdlib sqlite3, no ORM -- the schema is small enough
that an ORM would add more concepts than it removes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import settings

_SCHEMA = Path(__file__).parent / "schema.sql"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else settings().db_path
    conn = sqlite3.connect(
        p,
        isolation_level=None,   # autocommit; transactions are managed explicitly below
        # A connection is created per request and used by exactly one request, but
        # FastAPI hands a request between its threadpool and the event loop, so the
        # same connection legitimately crosses threads within one sequential request.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # A background batch writes while the dashboard reads. WAL (set in schema.sql)
    # allows that; the timeout covers the brief write-lock overlap instead of
    # surfacing "database is locked" to the UI.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA.read_text())


def reset(path: str | Path | None = None) -> sqlite3.Connection:
    """Drop and recreate. Used by the seeder, the eval harness and tests."""
    p = Path(path) if path else settings().db_path
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(p) + suffix)
        if f.exists():
            f.unlink()
    conn = connect(p)
    init(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, args).fetchone()


def scalar(conn: sqlite3.Connection, sql: str, args: tuple = (), default=0):
    r = conn.execute(sql, args).fetchone()
    if r is None or r[0] is None:
        return default
    return r[0]


def jload(value: str | None, default=None):
    if not value:
        return default
    return json.loads(value)


def jdump(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
