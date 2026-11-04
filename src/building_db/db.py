"""Open the SQLite database and create the tables if needed.

The shape of the tables is recorded in PRAGMA user_version. A DB of another
version is never changed or migrated: the program stops with SchemaVersionError.
Raise SCHEMA_VERSION whenever schema.sql changes.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 1


class SchemaVersionError(Exception):
    """The DB was made for another shape of the tables."""

    def __init__(self, found: int) -> None:
        super().__init__(
            f"This database uses schema version {found}, but this program needs version {SCHEMA_VERSION}.\n"
            "Nothing was changed. Import the drawings into a new database file."
        )
        self.found = found


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the DB for writing, creating the tables in a new one."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        if _has_tables(conn):
            _check_version(conn)
        else:
            schema = resources.files("building_db").joinpath("schema.sql").read_text(encoding="utf-8")
            conn.executescript(schema)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("PRAGMA foreign_keys = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def open_existing(db_path: Path) -> sqlite3.Connection:
    """Open an existing DB for reading. Never creates a file."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        _check_version(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _has_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0] > 0


def _check_version(conn: sqlite3.Connection) -> None:
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found != SCHEMA_VERSION:
        raise SchemaVersionError(found)


def next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, width: int) -> str:
    """Next ID such as A-RM-002. IDs are never reused or renumbered."""
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",))
    numbers = [int(r[0][len(prefix):]) for r in rows if r[0][len(prefix):].isdigit()]
    return f"{prefix}{(max(numbers, default=0) + 1):0{width}d}"
