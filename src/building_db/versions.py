"""Snapshots of the database and diffs between versions (SPEC.md 8).

A new version is imported into the DB of the previous version, so IDs carry
over. Before that, the DB is copied into a snapshot. A diff compares the
elements last seen by the latest import of each DB, by ID: elements only in
the new DB are "added", elements only in the base are "missing" (they stay in
the DB). Calculated values (area, perimeter, length) are computed on the fly
from each DB and never stored.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from building_db.calc import room_area_m2, room_perimeter_m
from building_db.db import open_existing

KINDS = ("node", "edge", "room", "wall")


@dataclass(frozen=True)
class Element:
    kind: str
    element_id: str


@dataclass(frozen=True)
class Change:
    kind: str
    element_id: str
    field: str
    before: object
    after: object


@dataclass
class Diff:
    changes: list[Change] = field(default_factory=list)
    added: list[Element] = field(default_factory=list)
    missing: list[Element] = field(default_factory=list)


def take_snapshot(db_path: Path, snapshot_path: Path) -> Path:
    """Copy the DB file as it is now. Never overwrites an existing snapshot."""
    db_path, snapshot_path = Path(db_path), Path(snapshot_path)
    if snapshot_path.exists():
        raise FileExistsError(snapshot_path)
    source = open_existing(db_path)  # read-only; also checks the schema version
    try:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(snapshot_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return snapshot_path


def diff_versions(base_db: Path, new_db: Path) -> Diff:
    """Compare the latest version in base_db with the latest version in new_db."""
    base_conn = open_existing(base_db)
    try:
        base = _latest_elements(base_conn)
    finally:
        base_conn.close()
    new_conn = open_existing(new_db)
    try:
        new = _latest_elements(new_conn)
    finally:
        new_conn.close()

    diff = Diff()
    for kind in KINDS:
        before, after = base[kind], new[kind]
        for element_id in sorted(set(after) - set(before)):
            diff.added.append(Element(kind, element_id))
        for element_id in sorted(set(before) - set(after)):
            diff.missing.append(Element(kind, element_id))
        for element_id in sorted(set(before) & set(after)):
            for name, old_value in before[element_id].items():
                new_value = after[element_id][name]
                if not _same(old_value, new_value):
                    diff.changes.append(Change(kind, element_id, name, old_value, new_value))
    return diff


def _latest_elements(conn: sqlite3.Connection) -> dict[str, dict[str, dict]]:
    """Fields of every element seen by the latest import, keyed by kind and ID."""
    (batch,) = conn.execute("SELECT MAX(batch_id) FROM import_batches").fetchone()
    nodes_all = {i: (x, y) for i, x, y in conn.execute("SELECT node_id, x, y FROM nodes")}

    nodes = {
        i: {"position": nodes_all[i]}
        for (i,) in conn.execute("SELECT node_id FROM nodes WHERE last_seen_batch = ?", (batch,))
    }
    edges = {
        i: {"length_m": math.dist(nodes_all[s], nodes_all[e]) / 1000}
        for i, s, e in conn.execute(
            "SELECT edge_id, node_start, node_end FROM edges WHERE last_seen_batch = ?", (batch,))
    }
    rooms = {}
    for i, room_no, name, ceiling in conn.execute(
        "SELECT room_id, room_no, name, ceiling_height FROM rooms WHERE last_seen_batch = ?", (batch,)
    ):
        rooms[i] = {
            "room_no": room_no, "name": name, "ceiling_height": ceiling,
            "area_m2": room_area_m2(conn, i), "perimeter_m": room_perimeter_m(conn, i),
        }
    walls = {}
    for i, follow, type_id, adopted, observed in conn.execute(
        "SELECT wall_id, follow_edge, wall_type_id, adopted_geometry, observed_geometry "
        "FROM walls WHERE last_seen_batch = ?", (batch,)
    ):
        edge_ids = [e for (e,) in conn.execute(
            "SELECT edge_id FROM wall_edge_spans WHERE wall_id = ? ORDER BY seq", (i,))]
        walls[i] = {
            "adopted_geometry": _geometry(adopted), "observed_geometry": _geometry(observed),
            "follow_edge": follow, "wall_type_id": type_id, "edge_ids": edge_ids,
        }
    return {"node": nodes, "edge": edges, "room": rooms, "wall": walls}


def _geometry(stored: str) -> tuple:
    return tuple(sorted(tuple(p) for p in json.loads(stored)))


def _same(a, b, tol: float = 1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= tol
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return all(_same(x, y, tol) for x, y in zip(a, b))
    return a == b
