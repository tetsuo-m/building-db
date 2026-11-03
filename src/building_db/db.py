"""Open the SQLite database and create the tables if needed."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    schema = resources.files("building_db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    return conn


def next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, width: int) -> str:
    """Next ID such as A-RM-002. IDs are never reused or renumbered."""
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",))
    numbers = [int(r[0][len(prefix):]) for r in rows if r[0][len(prefix):].isdigit()]
    return f"{prefix}{(max(numbers, default=0) + 1):0{width}d}"
