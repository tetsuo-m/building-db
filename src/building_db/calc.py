"""Calculated values. These are computed on demand and never stored.

Rooms are measured on their boundary edges (= wall centers in step 1).
Walls are measured on their adopted wall center (walls.adopted_geometry).
Coordinates in the DB are mm; results are m and m2.
"""

from __future__ import annotations

import json
import math
import sqlite3


def room_polygon(conn: sqlite3.Connection, room_id: str) -> list[tuple[float, float]]:
    """Corner points of a room in boundary order, counter-clockwise, in mm."""
    rows = conn.execute(
        """SELECT rb.side, ns.x, ns.y, ne.x, ne.y
           FROM room_boundaries rb
           JOIN edges e ON e.edge_id = rb.edge_id
           JOIN nodes ns ON ns.node_id = e.node_start
           JOIN nodes ne ON ne.node_id = e.node_end
           WHERE rb.room_id = ?
           ORDER BY rb.seq""",
        (room_id,),
    ).fetchall()
    if not rows:
        raise ValueError(f"Room {room_id} has no boundary")
    # Side A: the room is on the left of the edge, so walk start -> end.
    return [(sx, sy) if side == "A" else (ex, ey) for side, sx, sy, ex, ey in rows]


def room_area_m2(conn: sqlite3.Connection, room_id: str) -> float:
    """Area of a room measured on its boundary edges (wall centers), in m2."""
    points = room_polygon(conn, room_id)
    twice_area = sum(
        x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )
    return abs(twice_area) / 2 / 1_000_000


def room_perimeter_m(conn: sqlite3.Connection, room_id: str) -> float:
    """Perimeter of a room measured on its boundary edges, in m."""
    points = room_polygon(conn, room_id)
    return sum(math.dist(p, q) for p, q in zip(points, points[1:] + points[:1])) / 1000


def wall_lengths_m(conn: sqlite3.Connection) -> dict[str, float]:
    """Wall center length of every wall, in m, keyed by wall_id."""
    lengths = {}
    for wall_id, geometry in conn.execute("SELECT wall_id, adopted_geometry FROM walls ORDER BY wall_id"):
        (x1, y1), (x2, y2) = json.loads(geometry)
        lengths[wall_id] = math.dist((x1, y1), (x2, y2)) / 1000
    return lengths
