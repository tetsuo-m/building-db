"""Import a drawing and its tables into the SQLite database.

Everything runs in one transaction: if anything fails, the adopted values in
the DB stay as they were. Problems are returned as review flags and never
fixed silently.

On re-import, existing entities are matched and keep their IDs:
grids by label, nodes as decided in matching.py (position, then connections),
edges by their two nodes, rooms by their boundary edges (then by room number),
walls by the edges they follow (then as decided in matching.py), wall types by
type code.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from building_db.db import connect, next_id
from building_db.dxf_reader import Drawing, read_drawing
from building_db.layers import ROOM_BOUNDARY, ROOM_NAME, WALL_CENTER
from building_db.matching import match_nodes, match_walls, on_segment, position_along
from building_db.network import DEFAULT_TOLERANCE_MM, Network, build_network, point_in_polygon
from building_db.tables import FinishRow, read_finish_schedule, read_wall_types

# Room name text is "<room number> <room name>", e.g. "101 事務室".
_ROOM_LABEL = re.compile(r"^\s*(\S+)[\s　]+(.+?)\s*$")


ALIGN_TO_BOUNDARY = "align_to_boundary"
UNFOLLOW = "unfollow"


@dataclass(frozen=True)
class WallDeviation:
    """A wall whose center line on the drawing left the boundary edge it follows.

    The expected position is computed from the followed edges when needed and
    never stored. The adopted position is not changed until a person decides.
    """

    wall_id: str
    edge_ids: list[str]
    distance_mm: float
    expected_geometry: tuple  # ((x1, y1), (x2, y2)) along the followed edges
    options: tuple[str, ...] = (ALIGN_TO_BOUNDARY, UNFOLLOW)

    @property
    def edge_id(self) -> str | None:
        return self.edge_ids[0] if len(self.edge_ids) == 1 else None

    @property
    def expected_x_mm(self) -> float | None:
        (x1, _), (x2, _) = self.expected_geometry
        return x1 if abs(x1 - x2) <= 1e-6 else None


@dataclass
class ImportResult:
    batch_id: int
    review_flags: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    wall_deviations: list[WallDeviation] = field(default_factory=list)
    # Matching against the previous version: elements decided automatically,
    # and elements handed to a person (see match_log).
    match_counts: dict[str, int] = field(default_factory=lambda: {"auto": 0, "to_person": 0})


def import_drawing(
    db_path: Path,
    dxf_path: Path,
    wall_types_csv: Path,
    finish_schedule_csv: Path,
    default_wall_type: str,
    tol: float = DEFAULT_TOLERANCE_MM,
) -> ImportResult:
    """Import grids, rooms, walls and table values; return an ImportResult."""
    dxf_path = Path(dxf_path)
    drawing = read_drawing(dxf_path)
    wall_type_rows = read_wall_types(wall_types_csv)
    finish_rows = read_finish_schedule(finish_schedule_csv)

    if default_wall_type not in {r.type_code for r in wall_type_rows}:
        raise ValueError(f"Default wall type {default_wall_type} is not in {wall_types_csv}")

    flags: list[str] = list(drawing.warnings)
    network = build_network(drawing.lines_on(ROOM_BOUNDARY), tol)
    flags.extend(network.problems)

    conn = connect(db_path)
    try:
        with conn:  # one transaction
            batch_id = _insert_batch(conn, dxf_path, drawing)
            # Reasons are logged only when there is a previous version to match against.
            log = _Log(conn, batch_id) if _has_previous_import(conn, batch_id) else None
            type_ids = _upsert_wall_types(conn, wall_type_rows)
            _upsert_grids(conn, drawing)
            node_ids, node_moves = _import_nodes(conn, network, tol, flags, batch_id, log)
            edge_ids = _import_edges(conn, network, node_ids, node_moves, batch_id, log)
            room_ids = _import_rooms(
                conn, drawing, network, node_ids, edge_ids, finish_rows,
                Path(finish_schedule_csv).name, batch_id, flags, log,
            )
            deviations: list[WallDeviation] = []
            wall_ids = _import_walls(
                conn, drawing, network, edge_ids, type_ids[default_wall_type],
                default_wall_type, batch_id, tol, flags, log, deviations,
            )
            conn.execute(
                "UPDATE import_batches SET review_flags = ? WHERE batch_id = ?",
                (json.dumps(flags, ensure_ascii=False), batch_id),
            )
            match_counts = _match_counts(conn, batch_id)
    finally:
        conn.close()

    return ImportResult(
        batch_id=batch_id,
        review_flags=flags,
        match_counts=match_counts,
        wall_deviations=deviations,
        counts={
            "grids": len(drawing.grids),
            "nodes": len(node_ids),
            "edges": len(edge_ids),
            "rooms": len(room_ids),
            "walls": len(wall_ids),
        },
    )


def _insert_batch(conn: sqlite3.Connection, dxf_path: Path, drawing: Drawing) -> int:
    c = drawing.conditions
    cur = conn.execute(
        """INSERT INTO import_batches (
            source_name, source_path, sha256, imported_at, reader, recover_mode,
            dxf_version, encoding, encoding_assumed, repairs, duplicate_handles,
            has_duplicate_handles, unit, unit_assumed, origin_x, origin_y, review_flags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            dxf_path.name,
            str(dxf_path.resolve()),
            hashlib.sha256(dxf_path.read_bytes()).hexdigest(),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            c.reader,
            int(c.recover_mode),
            c.dxf_version,
            c.encoding,
            int(c.encoding_assumed),
            json.dumps(c.repairs, ensure_ascii=False),
            json.dumps(c.duplicate_handles),
            int(bool(c.duplicate_handles)),
            c.unit,
            int(c.unit_assumed),
            drawing.origin[0],
            drawing.origin[1],
            "[]",
        ),
    )
    return cur.lastrowid


def _has_previous_import(conn, batch_id: int) -> bool:
    return conn.execute("SELECT COUNT(*) FROM import_batches WHERE batch_id != ?", (batch_id,)).fetchone()[0] > 0


class _Log:
    """Writes match_log rows for one import batch."""

    def __init__(self, conn, batch_id: int) -> None:
        self.conn = conn
        self.batch_id = batch_id

    def write(self, table: str, target_id: str, classification: str, rule: str | None = None,
              anchors=None, moved_mm: float | None = None, candidates=None, review: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO match_log (batch_id, target_table, target_id, classification, rule, anchors, "
            "moved_mm, candidates, review) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.batch_id, table, target_id, classification, rule,
             json.dumps(sorted(anchors)) if anchors else None, moved_mm,
             json.dumps(sorted(candidates)) if candidates else None, int(review)),
        )

    def matched(self, table: str, target_id: str, rule: str, moved_mm: float, anchors=(), tol: float = 1.0,
                review: bool = False) -> None:
        self.write(table, target_id, "moved" if moved_mm > tol else "match", rule, anchors, moved_mm,
                   review=review)

    def missing(self, table: str, previous_ids, seen_ids) -> None:
        for target_id in sorted(set(previous_ids) - set(seen_ids)):
            self.write(table, target_id, "missing_candidate")


def _mark_seen(conn, table: str, id_column: str, ids, batch_id: int) -> None:
    conn.executemany(f"UPDATE {table} SET last_seen_batch = ? WHERE {id_column} = ?",
                     [(batch_id, i) for i in set(ids)])


def _match_counts(conn, batch_id: int) -> dict[str, int]:
    auto, to_person = conn.execute(
        "SELECT SUM(review = 0 AND classification IN ('match', 'moved')), SUM(review = 1) "
        "FROM match_log WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    return {"auto": auto or 0, "to_person": to_person or 0}


def _set_provenance(conn, table, target_id, column, origin, source, batch_id) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO field_provenance "
        "(target_table, target_id, field, origin, source, batch_id) VALUES (?, ?, ?, ?, ?, ?)",
        (table, target_id, column, origin, source, batch_id),
    )


def _upsert_wall_types(conn, rows) -> dict[str, str]:
    """Return {type_code: type_id}."""
    ids = {}
    for r in rows:
        found = conn.execute("SELECT type_id FROM wall_types WHERE type_code = ?", (r.type_code,)).fetchone()
        type_id = found[0] if found else next_id(conn, "wall_types", "type_id", "A-WT-", 3)
        conn.execute(
            """INSERT OR REPLACE INTO wall_types (type_id, type_code, name, core, core_thickness_mm,
               board_thickness_mm, boards_per_side, total_thickness_mm, role)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (type_id, r.type_code, r.name, r.core, r.core_thickness_mm, r.board_thickness_mm,
             r.boards_per_side, r.total_thickness_mm, r.role),
        )
        ids[r.type_code] = type_id
    return ids


def _upsert_grids(conn, drawing: Drawing) -> None:
    for g in drawing.grids:
        found = conn.execute("SELECT grid_id FROM grids WHERE label = ?", (g.label,)).fetchone()
        if found:
            conn.execute("UPDATE grids SET direction = ?, position = ? WHERE grid_id = ?",
                         (g.direction, g.position, found[0]))
        else:
            conn.execute("INSERT INTO grids (grid_id, label, direction, position) VALUES (?, ?, ?, ?)",
                         (next_id(conn, "grids", "grid_id", "A-GR-", 3), g.label, g.direction, g.position))


def _import_nodes(conn, network: Network, tol: float, flags: list[str], batch_id: int,
                  log: _Log | None) -> tuple[list[str], dict[str, float]]:
    """Return the node ID of each network node and how far each matched node moved."""
    prev_nodes = {node_id: (x, y) for node_id, x, y in conn.execute("SELECT node_id, x, y FROM nodes")}
    prev_edges = conn.execute("SELECT node_start, node_end FROM edges").fetchall()
    match = match_nodes(prev_nodes, prev_edges, network.nodes,
                        [(e.start, e.end) for e in network.edges], tol)
    flags.extend(match.problems)

    node_ids = []
    moves: dict[str, float] = {}
    for i, (x, y) in enumerate(network.nodes):
        node_id = match.matched.get(i)
        if node_id is None:
            node_id = next_id(conn, "nodes", "node_id", "A-ND-", 4)
            conn.execute("INSERT INTO nodes (node_id, x, y) VALUES (?, ?, ?)", (node_id, x, y))
            if log:
                candidates = match.undecided.get(i)
                log.write("nodes", node_id, "undecided" if candidates else "added_candidate",
                          candidates=candidates, review=bool(candidates))
        else:
            reason = match.reasons[i]
            moves[node_id] = reason.moved_mm
            if reason.rule != "same_position":
                conn.execute("UPDATE nodes SET x = ?, y = ? WHERE node_id = ?", (x, y, node_id))
            if log:
                log.matched("nodes", node_id, reason.rule, reason.moved_mm, reason.anchors, tol)
        node_ids.append(node_id)
    if log:
        log.missing("nodes", prev_nodes, node_ids)
    _mark_seen(conn, "nodes", "node_id", node_ids, batch_id)
    return node_ids, moves


def _import_edges(conn, network: Network, node_ids, node_moves: dict[str, float], batch_id: int,
                  log: _Log | None) -> list[str]:
    """Return the edge ID of each network edge; an edge is the same if both its nodes are."""
    prev_edges = [r[0] for r in conn.execute("SELECT edge_id FROM edges")]
    edge_ids = []
    for e in network.edges:
        start_id, end_id = node_ids[e.start], node_ids[e.end]
        found = conn.execute(
            "SELECT edge_id FROM edges WHERE node_start = ? AND node_end = ?", (start_id, end_id)
        ).fetchone()
        if found:
            edge_id = found[0]
            if log:
                moved = max(node_moves.get(start_id, 0.0), node_moves.get(end_id, 0.0))
                log.matched("edges", edge_id, "both_end_nodes", moved, (start_id, end_id))
        else:
            edge_id = next_id(conn, "edges", "edge_id", "A-BD-", 4)
            conn.execute("INSERT INTO edges (edge_id, node_start, node_end) VALUES (?, ?, ?)",
                         (edge_id, start_id, end_id))
            if log:
                log.write("edges", edge_id, "added_candidate")
        edge_ids.append(edge_id)
    if log:
        log.missing("edges", prev_edges, edge_ids)
    _mark_seen(conn, "edges", "edge_id", edge_ids, batch_id)
    return edge_ids


def _import_rooms(conn, drawing, network: Network, node_ids, edge_ids, finish_rows: list[FinishRow],
                  finish_name: str, batch_id, flags, log: _Log | None = None) -> list[str]:
    texts = drawing.texts_on(ROOM_NAME)
    used_texts = set()
    finishes = {r.room_no: r for r in finish_rows}
    room_ids = []

    # Read every contour first, so that matching does not depend on their order.
    contours: list[_Contour] = []
    for face in network.faces:
        polygon = [network.nodes[n] for n in face.nodes]
        inside = [i for i, t in enumerate(texts) if point_in_polygon((t.x, t.y), polygon)]
        used_texts.update(inside)
        face_edge_ids = [edge_ids[i] for i in face.edges]

        room_no = name = None
        label_source = None
        if len(inside) == 1:
            t = texts[inside[0]]
            m = _ROOM_LABEL.match(t.text)
            if m:
                room_no, name = m.group(1), m.group(2)
            else:
                name = t.text.strip()
                flags.append(f"Room name text '{t.text}' has no room number")
            label_source = f"{drawing.path.name} layer {ROOM_NAME} text '{t.text}' inside the contour"
        elif not inside:
            flags.append(f"Contour {polygon} has no room name text")
        else:
            names = [texts[i].text for i in inside]
            flags.append(f"Contour {polygon} has {len(inside)} room name texts: {names}")
        contours.append(_Contour(face, polygon, face_edge_ids, room_no, name, label_source))

    prev_room_ids = [r[0] for r in conn.execute("SELECT room_id FROM rooms")]
    matched, decisions = _match_rooms(conn, contours, flags)

    for c in contours:
        face, polygon, face_edge_ids, room_no, name, label_source = (
            c.face, c.polygon, c.edge_ids, c.room_no, c.name, c.label_source)
        room_id = matched.get(id(c))
        if room_id is None:
            room_id = next_id(conn, "rooms", "room_id", "A-RM-", 3)
            conn.execute("INSERT INTO rooms (room_id) VALUES (?)", (room_id,))
        if log:
            classification, rule, candidates, review = decisions[id(c)]
            log.write("rooms", room_id, classification, rule, candidates=candidates, review=review)
        conn.execute("UPDATE rooms SET room_no = ?, name = ? WHERE room_id = ?", (room_no, name, room_id))
        if label_source:
            _set_provenance(conn, "rooms", room_id, "room_no", "cad_import", label_source, batch_id)
            _set_provenance(conn, "rooms", room_id, "name", "cad_import", label_source, batch_id)

        conn.execute("DELETE FROM room_boundaries WHERE room_id = ?", (room_id,))
        for seq, (eid, fwd) in enumerate(zip(face_edge_ids, face.forward), start=1):
            # The contour is counter-clockwise, so the room is on its left.
            conn.execute("INSERT INTO room_boundaries (room_id, edge_id, seq, side) VALUES (?, ?, ?, ?)",
                         (room_id, eid, seq, "A" if fwd else "B"))

        finish = finishes.get(room_no) if room_no else None
        if room_no and finish is None:
            flags.append(f"Room {room_no} is not in {finish_name}")
        if finish:
            if finish.room_name != name:
                flags.append(
                    f"Room {room_no}: name on drawing '{name}' differs from {finish_name} '{finish.room_name}'"
                )
            conn.execute("UPDATE rooms SET ceiling_height = ? WHERE room_id = ?",
                         (finish.ceiling_height_mm, room_id))
            _set_provenance(conn, "rooms", room_id, "ceiling_height", "cad_import",
                            f"{finish_name} room_no {room_no} column ceiling_height_mm", batch_id)
        room_ids.append(room_id)

    for i, t in enumerate(texts):
        if i not in used_texts:
            flags.append(f"Room name text '{t.text}' at ({t.x:.0f}, {t.y:.0f}) is not inside any contour")
    if log:
        log.missing("rooms", prev_room_ids, room_ids)
    _mark_seen(conn, "rooms", "room_id", room_ids, batch_id)
    return room_ids


def _room_edges(conn) -> dict[str, set[str]]:
    """The boundary edges of every room, before this import changes them."""
    rooms: dict[str, set[str]] = {r[0]: set() for r in conn.execute("SELECT room_id FROM rooms")}
    for room_id, edge_id in conn.execute("SELECT room_id, edge_id FROM room_boundaries"):
        rooms[room_id].add(edge_id)
    return rooms


@dataclass
class _Contour:
    """A closed contour of the new drawing, read before any room is matched."""

    face: object
    polygon: list
    edge_ids: list[str]
    room_no: str | None
    name: str | None
    label_source: str | None


def _match_rooms(conn, contours: list[_Contour], flags: list[str]):
    """Decide which previous room each contour is.

    Returns ({id(contour): room_id}, {id(contour): (classification, rule,
    candidates, review)}) — the second for match_log.

    1. A contour with the same boundary edges as a previous room is that room.
    2. A contour whose room number names a previous room not matched in step 1
       is that room, but only if no other contour claims the same number.
    Anything else is a new room. The result does not depend on contour order.
    """
    prev_rooms = _room_edges(conn)
    numbers = dict(conn.execute("SELECT room_id, room_no FROM rooms WHERE room_no IS NOT NULL"))
    matched: dict[int, str] = {}
    decisions: dict[int, tuple] = {}
    taken: set[str] = set()

    for c in contours:
        wanted = set(c.edge_ids)
        for room_id, edges in prev_rooms.items():
            if edges == wanted and room_id not in taken:
                matched[id(c)] = room_id
                decisions[id(c)] = ("match", "boundary_set", None, False)
                taken.add(room_id)
                break

    claims: dict[str, list[_Contour]] = {}
    for c in contours:
        if id(c) not in matched and c.room_no:
            claims.setdefault(c.room_no, []).append(c)
    for room_no, claimants in claims.items():
        rooms_with_no = sorted(r for r, no in numbers.items() if no == room_no)
        if len(claimants) > 1:
            flags.append(f"Room number {room_no} is inside {len(claimants)} contours; "
                         f"none of them keeps the ID of {', '.join(rooms_with_no) or 'a previous room'}")
            for c in claimants:
                decisions[id(c)] = ("undecided", "room_number", rooms_with_no or None, True)
            continue
        (c,) = claimants
        free = [r for r in rooms_with_no if r not in taken]
        if free and len(free) == len(rooms_with_no) == 1:
            matched[id(c)] = free[0]
            decisions[id(c)] = ("match", "room_number", None, True)
            taken.add(free[0])
            flags.append(f"Room {room_no}: boundary changed, {free[0]} keeps its ID by room number only "
                         "(possible split or merge)")
        elif rooms_with_no:
            held = [r for r in rooms_with_no if r in taken]
            flags.append(f"Room number {room_no} conflicts with {', '.join(held or rooms_with_no)}, "
                         "which is matched by its boundary; the contour becomes a new room")
            decisions[id(c)] = ("undecided", "room_number", rooms_with_no, True)

    for c in contours:
        if id(c) in matched or id(c) in decisions:
            continue
        sharing = sorted(r for r, edges in prev_rooms.items() if edges & set(c.edge_ids))
        if sharing:
            flags.append(f"Contour {c.polygon} is a new room, sharing boundary edges with "
                         f"{', '.join(sharing)} (possible split or merge)")
            decisions[id(c)] = ("undecided", None, sharing, True)
        else:
            decisions[id(c)] = ("added_candidate", None, None, False)
    return matched, decisions


def _import_walls(conn, drawing, network: Network, edge_ids, type_id, type_code, batch_id, tol,
                  flags, log: _Log | None = None, deviations: list | None = None) -> list[str]:
    deviations = [] if deviations is None else deviations
    wall_ids = []
    prev_wall_ids = [r[0] for r in conn.execute("SELECT wall_id FROM walls")]
    walls_per_edge: dict[str, int] = {eid: 0 for eid in edge_ids}

    lines = [tuple(sorted([(ln.x1, ln.y1), (ln.x2, ln.y2)])) for ln in drawing.lines_on(WALL_CENTER)]
    all_spans = []  # per line: (position along the wall, edge index, reversed)
    for a, b in lines:
        spans = []
        for idx, e in enumerate(network.edges):
            p, q = network.nodes[e.start], network.nodes[e.end]
            if on_segment(a, b, p, tol) and on_segment(a, b, q, tol):
                tp, tq = position_along(a, b, p), position_along(a, b, q)
                spans.append((min(tp, tq), idx, tp > tq))
        spans.sort()
        all_spans.append(spans)

    prev_spans = _wall_edges(conn)
    by_edges: dict[int, str] = {}
    for i, spans in enumerate(all_spans):
        wall_id = _find_wall(prev_spans, set(by_edges.values()), [edge_ids[idx] for _, idx, _ in spans])
        if wall_id is not None:
            by_edges[i] = wall_id
    match = match_walls(_previous_walls(conn), lines, by_edges, tol)
    flags.extend(match.problems)

    for i, (a, b) in enumerate(lines):
        spans = all_spans[i]
        wall_id = match.matched.get(i)
        geometry = json.dumps([list(a), list(b)])

        # A matched wall whose line no longer lies on the edges it follows has
        # deviated; the wall is not re-reported as a line on no edge.
        deviation = _wall_deviation(conn, wall_id, (a, b), tol) if wall_id and not spans else None
        if not spans and deviation is None:
            flags.append(f"Wall center line {a}-{b} does not follow any boundary line")

        if wall_id is None:
            wall_id = next_id(conn, "walls", "wall_id", "A-WL-", 4)
            conn.execute(
                "INSERT INTO walls (wall_id, follow_edge, wall_type_id, adopted_geometry, observed_geometry) "
                "VALUES (?, ?, ?, ?, ?)",
                (wall_id, 1 if spans else 0, type_id, geometry, geometry),
            )
            if log:
                candidates = match.undecided.get(i)
                log.write("walls", wall_id, "undecided" if candidates else "added_candidate",
                          candidates=candidates, review=bool(candidates))
        elif deviation is None:
            conn.execute(
                "UPDATE walls SET wall_type_id = ?, adopted_geometry = ?, observed_geometry = ? WHERE wall_id = ?",
                (type_id, geometry, geometry, wall_id),
            )
        else:
            # Observed changes; adopted (and its provenance) stays until a person decides.
            conn.execute("UPDATE walls SET wall_type_id = ?, observed_geometry = ? WHERE wall_id = ?",
                         (type_id, geometry, wall_id))
        if log and match.matched.get(i) is not None:
            reason = match.reasons[i]
            log.matched("walls", wall_id, reason.rule, reason.moved_mm, reason.anchors, tol)

        _set_provenance(conn, "walls", wall_id, "wall_type_id", "designer",
                        f"default wall type {type_code} given on the import command (--default-wall-type)",
                        batch_id)
        if deviation is None:
            _set_provenance(conn, "walls", wall_id, "adopted_geometry", "cad_import",
                            f"{drawing.path.name} layer {WALL_CENTER}", batch_id)
        _set_provenance(conn, "walls", wall_id, "observed_geometry", "cad_import",
                        f"{drawing.path.name} layer {WALL_CENTER}", batch_id)
        if deviation is not None:
            deviations.append(deviation)
            where = (f"x = {deviation.expected_x_mm:.0f}" if deviation.expected_x_mm is not None
                     else f"{deviation.expected_geometry}")
            flags.append(
                f"Wall {wall_id} is {deviation.distance_mm:.0f} mm away from boundary edge "
                f"{', '.join(deviation.edge_ids)} it follows (expected {where}); "
                f"choose {ALIGN_TO_BOUNDARY} or {UNFOLLOW}"
            )

        if spans:
            conn.execute("DELETE FROM wall_edge_spans WHERE wall_id = ?", (wall_id,))
            for seq, (_, idx, rev) in enumerate(spans, start=1):
                conn.execute("INSERT INTO wall_edge_spans (wall_id, edge_id, seq, reversed) VALUES (?, ?, ?, ?)",
                             (wall_id, edge_ids[idx], seq, int(rev)))
        # A wall matched directly keeps the edges it followed.
        for (eid,) in conn.execute("SELECT edge_id FROM wall_edge_spans WHERE wall_id = ?", (wall_id,)):
            if eid in walls_per_edge:
                walls_per_edge[eid] += 1
        wall_ids.append(wall_id)
    if log:
        log.missing("walls", prev_wall_ids, wall_ids)
    _mark_seen(conn, "walls", "wall_id", wall_ids, batch_id)

    for eid, count in walls_per_edge.items():
        if count == 0:
            flags.append(f"Boundary edge {eid} has no wall (wall_policy default_wall)")
        elif count > 1:
            flags.append(f"Boundary edge {eid} is followed by {count} walls")
    return wall_ids


def _wall_deviation(conn, wall_id: str, line, tol: float) -> WallDeviation | None:
    """How far the drawn line is from the edges the wall follows, if it follows any."""
    (follow,) = conn.execute("SELECT follow_edge FROM walls WHERE wall_id = ?", (wall_id,)).fetchone()
    if not follow:
        return None
    edge_ids = [e for (e,) in conn.execute(
        "SELECT edge_id FROM wall_edge_spans WHERE wall_id = ? ORDER BY seq", (wall_id,))]
    if not edge_ids:
        return None
    expected = expected_wall_geometry(conn, edge_ids)
    distance = max(math.dist(line[0], expected[0]), math.dist(line[1], expected[1]))
    if distance <= tol:
        return None
    return WallDeviation(wall_id, edge_ids, distance, expected)


def expected_wall_geometry(conn, edge_ids: list[str]) -> tuple:
    """Where a wall following these edges should be: the span of their nodes, ends sorted."""
    marks = ",".join("?" * len(edge_ids))
    points = [(x, y) for x, y in conn.execute(
        f"""SELECT n.x, n.y FROM edges e JOIN nodes n ON n.node_id IN (e.node_start, e.node_end)
            WHERE e.edge_id IN ({marks})""", edge_ids)]
    return (min(points), max(points))


def _wall_edges(conn) -> dict[str, set[str]]:
    """The edges every wall followed, before this import changes them."""
    walls: dict[str, set[str]] = {}
    for wall_id, edge_id in conn.execute("SELECT wall_id, edge_id FROM wall_edge_spans"):
        walls.setdefault(wall_id, set()).add(edge_id)
    return walls


def _previous_walls(conn) -> dict[str, tuple]:
    """Each wall's line as last seen on a drawing, with its ends sorted."""
    walls = {}
    for wall_id, geometry in conn.execute("SELECT wall_id, observed_geometry FROM walls"):
        walls[wall_id] = tuple(sorted(tuple(p) for p in json.loads(geometry)))
    return walls


def _find_wall(prev_spans, used, span_edge_ids: list[str]) -> str | None:
    if not span_edge_ids:
        return None
    wanted = set(span_edge_ids)
    for wall_id, edges in prev_spans.items():
        if edges == wanted and wall_id not in used:
            return wall_id
    return None
