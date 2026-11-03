"""Calculated values. These are computed on demand and never stored."""

import sqlite3


def room_area_m2(conn: sqlite3.Connection, room_id: str) -> float:
    """Area of a room measured on its boundary edges (wall centers), in m2."""
    raise NotImplementedError


def room_perimeter_m(conn: sqlite3.Connection, room_id: str) -> float:
    """Perimeter of a room measured on its boundary edges, in m."""
    raise NotImplementedError


def wall_lengths_m(conn: sqlite3.Connection) -> dict[str, float]:
    """Wall center length of every wall, in m, keyed by wall_id."""
    raise NotImplementedError
