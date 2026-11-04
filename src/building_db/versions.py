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

import datetime as dt

from building_db.calc import room_area_m2, room_perimeter_m
from building_db.db import connect, open_existing
from building_db.importer import (
    ALIGN_TO_BOUNDARY, UNFOLLOW, expected_wall_geometry, latest_decision, wall_deviation_now,
)

KINDS = ("node", "edge", "room", "wall")
CHOICES = (ALIGN_TO_BOUNDARY, UNFOLLOW)
STATUS_APPROVED_NOT_ON_DRAWING = "approved_not_on_drawing"
STATUS_APPROVED_EXCEPTION = "approved_exception"


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


# --- Decisions ---------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: int
    target_table: str
    target_id: str
    choice: str
    reason: str | None
    basis_kind: str
    decided_on: str
    batch_id: int | None
    premise: dict
    premise_valid: bool


@dataclass(frozen=True)
class DrawingFixProposal:
    """The drawing should be changed to show the adopted position."""

    wall_id: str
    adopted_geometry: tuple
    observed_geometry: tuple

    @property
    def position_mm(self) -> float | None:
        (x1, _), (x2, _) = self.adopted_geometry
        return x1 if abs(x1 - x2) <= 1e-6 else None


@dataclass(frozen=True)
class AcceptedException:
    """A wall that, by decision, no longer follows its boundary edge."""

    wall_id: str
    distance_mm: float
    edge_ids: list[str]


def record_decision(db_path: Path, target_id: str, choice: str, reason: str | None = None,
                    decided_on: str | None = None, tol: float = 1.0) -> DecisionRecord:
    """Record a person's decision on a wall deviation and apply it.

    align_to_boundary: the adopted position becomes the expected position (the
    drawing is not changed; see drawing_fix_proposals). unfollow: the wall stops
    following its edge and stays where it is.
    """
    if choice not in CHOICES:
        raise ValueError(f"Unknown choice {choice!r}; choose one of {', '.join(CHOICES)}")
    decided_on = decided_on or dt.date.today().isoformat()
    conn = connect(db_path)
    try:
        with conn:
            if conn.execute("SELECT 1 FROM walls WHERE wall_id = ?", (target_id,)).fetchone() is None:
                raise ValueError(f"Wall {target_id} is not in the database")
            deviation = wall_deviation_now(conn, target_id, tol, regardless_of_follow=True)
            if deviation is None:
                raise ValueError(f"Wall {target_id} has no deviation to decide on")
            (batch_id,) = conn.execute("SELECT MAX(batch_id) FROM import_batches").fetchone()
            premise = {
                "observed_geometry": deviation["observed"],
                "expected_geometry": deviation["expected"],
                "distance_mm": deviation["distance_mm"],
                "edge_ids": deviation["edge_ids"],
            }
            cur = conn.execute(
                "INSERT INTO decisions (target_table, target_id, choice, reason, basis_kind, decided_on, "
                "batch_id, premise, premise_valid) VALUES ('walls', ?, ?, ?, 'person', ?, ?, ?, 1)",
                (target_id, choice, reason, decided_on, batch_id, json.dumps(premise)),
            )
            decision_id = cur.lastrowid
            source = f"decision {decision_id} ({choice}) by a person on {decided_on}"
            if choice == ALIGN_TO_BOUNDARY:
                conn.execute("UPDATE walls SET adopted_geometry = ?, follow_edge = 1 WHERE wall_id = ?",
                             (json.dumps([list(p) for p in deviation["expected"]]), target_id))
                _provenance(conn, target_id, "adopted_geometry", source, batch_id)
            else:
                conn.execute("UPDATE walls SET follow_edge = 0 WHERE wall_id = ?", (target_id,))
            _provenance(conn, target_id, "follow_edge", source, batch_id)
        return decisions_for(db_path, target_id)[-1]
    finally:
        conn.close()


def _provenance(conn, wall_id: str, column: str, source: str, batch_id) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO field_provenance (target_table, target_id, field, origin, source, batch_id) "
        "VALUES ('walls', ?, ?, 'designer', ?, ?)", (wall_id, column, source, batch_id),
    )


def decisions_for(db_path: Path, target_id: str) -> list[DecisionRecord]:
    """All decisions on an element, oldest first."""
    conn = open_existing(db_path)
    try:
        rows = conn.execute(
            "SELECT decision_id, target_table, target_id, choice, reason, basis_kind, decided_on, batch_id, "
            "premise, premise_valid FROM decisions WHERE target_id = ? ORDER BY decision_id", (target_id,)
        ).fetchall()
    finally:
        conn.close()
    records = []
    for d_id, table, t_id, choice, reason, basis, on, batch, premise, valid in rows:
        premise = json.loads(premise)
        for key in ("observed_geometry", "expected_geometry"):
            premise[key] = tuple(tuple(p) for p in premise[key])
        records.append(DecisionRecord(d_id, table, t_id, choice, reason, basis, on, batch, premise, bool(valid)))
    return records


def wall_status(db_path: Path, wall_id: str) -> str | None:
    """How a decided wall stands now, computed from the decision and the current positions."""
    conn = open_existing(db_path)
    try:
        return _wall_status(conn, wall_id)
    finally:
        conn.close()


def _wall_status(conn, wall_id: str, tol: float = 1.0) -> str | None:
    decision = latest_decision(conn, wall_id)
    if decision is None:
        return None
    choice, _ = decision
    row = conn.execute("SELECT follow_edge, adopted_geometry, observed_geometry FROM walls WHERE wall_id = ?",
                       (wall_id,)).fetchone()
    if row is None:
        return None
    follow, adopted, observed = row
    if choice == ALIGN_TO_BOUNDARY:
        return STATUS_APPROVED_NOT_ON_DRAWING if not _same(_geometry(adopted), _geometry(observed), tol) else None
    return STATUS_APPROVED_EXCEPTION if follow == 0 else None


def drawing_fix_proposals(db_path: Path) -> list[DrawingFixProposal]:
    """Walls approved to a position the drawing does not show yet."""
    conn = open_existing(db_path)
    try:
        return _drawing_fix_proposals(conn)
    finally:
        conn.close()


def _drawing_fix_proposals(conn) -> list[DrawingFixProposal]:
    proposals = []
    for wall_id, adopted, observed in conn.execute(
        "SELECT wall_id, adopted_geometry, observed_geometry FROM walls ORDER BY wall_id"
    ).fetchall():
        if _wall_status(conn, wall_id) == STATUS_APPROVED_NOT_ON_DRAWING:
            proposals.append(DrawingFixProposal(wall_id, _geometry(adopted), _geometry(observed)))
    return proposals


def accepted_exceptions(db_path: Path) -> list[AcceptedException]:
    """Walls that no longer follow their edge by decision, with the distance that remains."""
    conn = open_existing(db_path)
    try:
        return _accepted_exceptions(conn)
    finally:
        conn.close()


def _accepted_exceptions(conn, tol: float = 1.0) -> list[AcceptedException]:
    result = []
    for (wall_id,) in conn.execute("SELECT wall_id FROM walls ORDER BY wall_id").fetchall():
        if _wall_status(conn, wall_id) != STATUS_APPROVED_EXCEPTION:
            continue
        deviation = wall_deviation_now(conn, wall_id, tol, regardless_of_follow=True)
        edge_ids = [e for (e,) in conn.execute(
            "SELECT edge_id FROM wall_edge_spans WHERE wall_id = ? ORDER BY seq", (wall_id,))]
        result.append(AcceptedException(wall_id, deviation["distance_mm"] if deviation else 0.0, edge_ids))
    return result


def _same(a, b, tol: float = 1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= tol
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        return all(_same(x, y, tol) for x, y in zip(a, b))
    return a == b
