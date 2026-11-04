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


@dataclass
class ImportResult:
    batch_id: int
    review_flags: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


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
            type_ids = _upsert_wall_types(conn, wall_type_rows)
            _upsert_grids(conn, drawing)
            node_ids = _import_nodes(conn, network, tol, flags)
            edge_ids = [_edge_id(conn, node_ids[e.start], node_ids[e.end]) for e in network.edges]
            room_ids = _import_rooms(
                conn, drawing, network, node_ids, edge_ids, finish_rows,
                Path(finish_schedule_csv).name, batch_id, flags,
            )
            wall_ids = _import_walls(
                conn, drawing, network, edge_ids, type_ids[default_wall_type],
                default_wall_type, batch_id, tol, flags,
            )
            conn.execute(
                "UPDATE import_batches SET review_flags = ? WHERE batch_id = ?",
                (json.dumps(flags, ensure_ascii=False), batch_id),
            )
    finally:
        conn.close()

    return ImportResult(
        batch_id=batch_id,
        review_flags=flags,
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


def _import_nodes(conn, network: Network, tol: float, flags: list[str]) -> list[str]:
    """Return the node ID of each network node, keeping the IDs of matched nodes."""
    prev_nodes = {node_id: (x, y) for node_id, x, y in conn.execute("SELECT node_id, x, y FROM nodes")}
    prev_edges = conn.execute("SELECT node_start, node_end FROM edges").fetchall()
    match = match_nodes(prev_nodes, prev_edges, network.nodes,
                        [(e.start, e.end) for e in network.edges], tol)
    flags.extend(match.problems)

    node_ids = []
    for i, (x, y) in enumerate(network.nodes):
        node_id = match.matched.get(i)
        if node_id is None:
            node_id = next_id(conn, "nodes", "node_id", "A-ND-", 4)
            conn.execute("INSERT INTO nodes (node_id, x, y) VALUES (?, ?, ?)", (node_id, x, y))
        elif match.reasons[i].rule != "same_position":
            conn.execute("UPDATE nodes SET x = ?, y = ? WHERE node_id = ?", (x, y, node_id))
        node_ids.append(node_id)
    return node_ids


def _edge_id(conn, start_id: str, end_id: str) -> str:
    found = conn.execute(
        "SELECT edge_id FROM edges WHERE node_start = ? AND node_end = ?", (start_id, end_id)
    ).fetchone()
    if found:
        return found[0]
    edge_id = next_id(conn, "edges", "edge_id", "A-BD-", 4)
    conn.execute("INSERT INTO edges (edge_id, node_start, node_end) VALUES (?, ?, ?)",
                 (edge_id, start_id, end_id))
    return edge_id


def _import_rooms(conn, drawing, network: Network, node_ids, edge_ids, finish_rows: list[FinishRow],
                  finish_name: str, batch_id, flags) -> list[str]:
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

    matched = _match_rooms(conn, contours, flags)

    for c in contours:
        face, polygon, face_edge_ids, room_no, name, label_source = (
            c.face, c.polygon, c.edge_ids, c.room_no, c.name, c.label_source)
        room_id = matched.get(id(c))
        if room_id is None:
            room_id = next_id(conn, "rooms", "room_id", "A-RM-", 3)
            conn.execute("INSERT INTO rooms (room_id) VALUES (?)", (room_id,))
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


def _match_rooms(conn, contours: list[_Contour], flags: list[str]) -> dict[int, str]:
    """Decide which previous room each contour is. Returns {id(contour): room_id}.

    1. A contour with the same boundary edges as a previous room is that room.
    2. A contour whose room number names a previous room not matched in step 1
       is that room, but only if no other contour claims the same number.
    Anything else is a new room. The result does not depend on contour order.
    """
    prev_rooms = _room_edges(conn)
    numbers = dict(conn.execute("SELECT room_id, room_no FROM rooms WHERE room_no IS NOT NULL"))
    matched: dict[int, str] = {}
    taken: set[str] = set()

    for c in contours:
        wanted = set(c.edge_ids)
        for room_id, edges in prev_rooms.items():
            if edges == wanted and room_id not in taken:
                matched[id(c)] = room_id
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
            continue
        (c,) = claimants
        free = [r for r in rooms_with_no if r not in taken]
        if free and len(free) == len(rooms_with_no) == 1:
            matched[id(c)] = free[0]
            taken.add(free[0])
            flags.append(f"Room {room_no}: boundary changed, {free[0]} keeps its ID by room number only "
                         "(possible split or merge)")
        elif rooms_with_no:
            held = [r for r in rooms_with_no if r in taken]
            flags.append(f"Room number {room_no} conflicts with {', '.join(held or rooms_with_no)}, "
                         "which is matched by its boundary; the contour becomes a new room")

    for c in contours:
        if id(c) in matched:
            continue
        sharing = sorted(r for r, edges in prev_rooms.items() if edges & set(c.edge_ids))
        if sharing:
            flags.append(f"Contour {c.polygon} is a new room, sharing boundary edges with "
                         f"{', '.join(sharing)} (possible split or merge)")
    return matched


def _import_walls(conn, drawing, network: Network, edge_ids, type_id, type_code, batch_id, tol,
                  flags) -> list[str]:
    wall_ids = []
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
        if not spans:
            flags.append(f"Wall center line {a}-{b} does not follow any boundary line")

        wall_id = match.matched.get(i)
        geometry = json.dumps([list(a), list(b)])
        if wall_id is None:
            wall_id = next_id(conn, "walls", "wall_id", "A-WL-", 4)
            conn.execute(
                "INSERT INTO walls (wall_id, follow_edge, wall_type_id, adopted_geometry, observed_geometry) "
                "VALUES (?, ?, ?, ?, ?)",
                (wall_id, 1 if spans else 0, type_id, geometry, geometry),
            )
        else:
            conn.execute(
                "UPDATE walls SET wall_type_id = ?, adopted_geometry = ?, observed_geometry = ? WHERE wall_id = ?",
                (type_id, geometry, geometry, wall_id),
            )
        _set_provenance(conn, "walls", wall_id, "wall_type_id", "designer",
                        f"default wall type {type_code} given on the import command (--default-wall-type)",
                        batch_id)
        _set_provenance(conn, "walls", wall_id, "adopted_geometry", "cad_import",
                        f"{drawing.path.name} layer {WALL_CENTER}", batch_id)
        _set_provenance(conn, "walls", wall_id, "observed_geometry", "cad_import",
                        f"{drawing.path.name} layer {WALL_CENTER}", batch_id)

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

    for eid, count in walls_per_edge.items():
        if count == 0:
            flags.append(f"Boundary edge {eid} has no wall (wall_policy default_wall)")
        elif count > 1:
            flags.append(f"Boundary edge {eid} is followed by {count} walls")
    return wall_ids


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
