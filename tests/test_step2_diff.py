"""Version comparison: step2.dxf as the base, compared with two new versions.

Expected values come from samples/step2/expected_diff.md. Each test names the
part it checks. Elements are tracked by the IDs they had in the base version.

The API below does not exist yet. It is a provisional shape, following
SPEC.md section 8, and may change when it is implemented:

    building_db.versions.take_snapshot(db_path, snapshot_path)
    building_db.versions.diff_versions(base_db, new_db)
        -> .changes (each with .kind, .element_id, .field, .before, .after),
           .added, .missing
    building_db.versions.record_decision(db_path, target_id, choice,
                                         reason=None, decided_on=None)
    building_db.versions.decisions_for(db_path, target_id)
        -> records with .target_id, .choice, .reason, .basis_kind,
           .decided_on, .premise_valid
    building_db.versions.wall_status(db_path, wall_id)
    building_db.versions.drawing_fix_proposals(db_path)
        -> records with .wall_id, .position_mm
    building_db.versions.accepted_exceptions(db_path)
        -> records with .wall_id, .distance_mm
    ImportResult.wall_deviations
        -> records with .wall_id, .edge_id, .distance_mm, .expected_x_mm, .options

A new version is imported into the DB of the previous version with the
existing import_drawing().
"""

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV

STEP2 = Path(__file__).resolve().parent.parent / "samples" / "step2"
BASE = STEP2 / "step2.dxf"
MOVED = STEP2 / "step2_moved.dxf"
BOUNDARY_ONLY = STEP2 / "step2_boundary_only.dxf"

ALIGN = "align_to_boundary"
UNFOLLOW = "unfollow"
DECIDED_ON = "2026-11-04"

# Marks for behavior that is not implemented yet. strict=True makes an
# unexpected pass fail, so a mark cannot stay on a test that already works.
# Remove a mark only with the approval of the project owner.
NEEDS_VERSIONS = pytest.mark.xfail(strict=True, reason="building_db.versions is not implemented")

EDGE_NAMES = {
    frozenset({(0, 0), (2300, 0)}): "south_left",
    frozenset({(2300, 0), (6000, 0)}): "south_right",
    frozenset({(0, 4000), (2300, 4000)}): "north_left",
    frozenset({(2300, 4000), (6000, 4000)}): "north_right",
    frozenset({(6000, 0), (6000, 4000)}): "east",
    frozenset({(0, 0), (0, 4000)}): "west",
    frozenset({(2300, 0), (2300, 4000)}): "partition",
}
WALL_NAMES = {
    ((0, 0), (6000, 0)): "south",
    ((0, 4000), (6000, 4000)): "north",
    ((6000, 0), (6000, 4000)): "east",
    ((0, 0), (0, 4000)): "west",
    ((2300, 0), (2300, 4000)): "partition",
}


def import_version(db, dxf):
    from building_db.importer import import_drawing

    return import_drawing(
        db_path=db,
        dxf_path=dxf,
        wall_types_csv=WALL_TYPES_CSV,
        finish_schedule_csv=FINISH_SCHEDULE_CSV,
        default_wall_type=DEFAULT_WALL_TYPE,
    )


def node_position(conn, node_id):
    x, y = conn.execute("SELECT x, y FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
    return round(x), round(y)


def edge_points(conn, edge_id):
    start, end = conn.execute(
        "SELECT node_start, node_end FROM edges WHERE edge_id = ?", (edge_id,)
    ).fetchone()
    return node_position(conn, start), node_position(conn, end)


def edge_length_m(conn, edge_id):
    (x1, y1), (x2, y2) = edge_points(conn, edge_id)
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / 1000


def wall_points(conn, wall_id, column="adopted_geometry"):
    (geometry,) = conn.execute(
        f"SELECT {column} FROM walls WHERE wall_id = ?", (wall_id,)
    ).fetchone()
    (x1, y1), (x2, y2) = json.loads(geometry)
    return tuple(sorted([(round(x1), round(y1)), (round(x2), round(y2))]))


def ids(conn, table, column):
    return {r[0] for r in conn.execute(f"SELECT {column} FROM {table}")}


@dataclass
class BaseIds:
    """IDs of the base version, named by their position in step2.dxf."""

    grids: dict
    nodes: dict
    edges: dict
    rooms: dict
    walls: dict


def read_base_ids(conn):
    rooms = {}
    for (room_id,) in conn.execute("SELECT room_id FROM rooms").fetchall():
        (xmax,) = conn.execute(
            """SELECT MAX(n.x) FROM room_boundaries rb
               JOIN edges e ON e.edge_id = rb.edge_id
               JOIN nodes n ON n.node_id IN (e.node_start, e.node_end)
               WHERE rb.room_id = ?""",
            (room_id,),
        ).fetchone()
        rooms["left" if round(xmax) == 2300 else "right"] = room_id
    return BaseIds(
        grids=dict(conn.execute("SELECT label, grid_id FROM grids")),
        nodes={node_position(conn, r[0]): r[0] for r in conn.execute("SELECT node_id FROM nodes").fetchall()},
        edges={EDGE_NAMES[frozenset(edge_points(conn, r[0]))]: r[0]
               for r in conn.execute("SELECT edge_id FROM edges").fetchall()},
        rooms=rooms,
        walls={WALL_NAMES[wall_points(conn, r[0])]: r[0]
               for r in conn.execute("SELECT wall_id FROM walls").fetchall()},
    )


@dataclass
class Versions:
    db: Path
    base_db: Path  # a plain copy of the DB before the new version was imported
    dxf: Path
    base: BaseIds
    result: object
    conn: sqlite3.Connection


def open_versions(tmp_path, new_dxf):
    db = tmp_path / "building.sqlite"
    base_db = tmp_path / "base.sqlite"
    import_version(db, BASE)
    conn = sqlite3.connect(db)
    base = read_base_ids(conn)
    conn.close()
    shutil.copyfile(db, base_db)
    result = import_version(db, new_dxf)
    return Versions(db, base_db, new_dxf, base, result, sqlite3.connect(db))


@pytest.fixture
def moved(tmp_path):
    v = open_versions(tmp_path, MOVED)
    yield v
    v.conn.close()


@pytest.fixture
def boundary_only(tmp_path):
    v = open_versions(tmp_path, BOUNDARY_ONLY)
    yield v
    v.conn.close()


@pytest.fixture(params=[MOVED, BOUNDARY_ONLY], ids=["moved", "boundary_only"])
def either(request, tmp_path):
    v = open_versions(tmp_path, request.param)
    yield v
    v.conn.close()


def assert_walls_identified(v):
    """The new version has 5 walls, and its partition line is the base partition wall.

    Without this, a test can pass on the untouched row of the base version
    while the drawing's line was stored as another wall.
    """
    wall_ids = ids(v.conn, "walls", "wall_id")
    assert len(wall_ids) == 5
    partition_ids = []
    for wall_id in wall_ids:
        (x1, _), (x2, _) = wall_points(v.conn, wall_id, "observed_geometry")
        if x1 == x2 and x1 not in (0, 6000):
            partition_ids.append(wall_id)
    assert partition_ids == [v.base.walls["partition"]]


def changes_of(diff, kind, field):
    return {c.element_id: (c.before, c.after) for c in diff.changes if c.kind == kind and c.field == field}


# --- Taking a snapshot before importing ---------------------------------------


@NEEDS_VERSIONS
def test_snapshot_keeps_the_base_version(tmp_path):
    # SPEC.md 8: copy the DB into a snapshot before importing a new version
    from building_db.versions import take_snapshot

    db = tmp_path / "building.sqlite"
    snapshot = tmp_path / "snapshot.sqlite"
    import_version(db, BASE)
    take_snapshot(db, snapshot)
    import_version(db, MOVED)
    conn = sqlite3.connect(snapshot)
    try:
        rows = conn.execute("SELECT x FROM nodes").fetchall()
        assert sorted({round(x) for (x,) in rows}) == [0, 2300, 6000]
    finally:
        conn.close()


# --- Both new versions: boundaries and rooms ----------------------------------


def test_counts(either):
    # "変わらないもの": 4 grids, 6 nodes, 7 edges, 2 rooms, 5 walls
    conn = either.conn
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("grids", "nodes", "edges", "rooms", "walls")}
    assert counts == {"grids": 4, "nodes": 6, "edges": 7, "rooms": 2, "walls": 5}


def test_grid_ids_kept(either):
    # Identification: grids are told apart by their label
    assert dict(either.conn.execute("SELECT label, grid_id FROM grids")) == either.base.grids


def test_node_ids_kept(either):
    # "変わらないもの": all node IDs are the same as in the base version
    assert ids(either.conn, "nodes", "node_id") == set(either.base.nodes.values())


def test_edge_ids_kept(either):
    assert ids(either.conn, "edges", "edge_id") == set(either.base.edges.values())


def test_room_ids_kept(either):
    assert ids(either.conn, "rooms", "room_id") == set(either.base.rooms.values())


def test_wall_ids_kept(either):
    assert ids(either.conn, "walls", "wall_id") == set(either.base.walls.values())


def test_t_junctions_moved(either):
    # "変わるもの": T-junction nodes move from x = 2300 to x = 2800
    base, conn = either.base, either.conn
    assert node_position(conn, base.nodes[(2300, 0)]) == (2800, 0)
    assert node_position(conn, base.nodes[(2300, 4000)]) == (2800, 4000)


def test_corner_nodes_not_moved(either):
    base, conn = either.base, either.conn
    for position in [(0, 0), (6000, 0), (6000, 4000), (0, 4000)]:
        assert node_position(conn, base.nodes[position]) == position


def test_edge_lengths(either):
    # "変わるもの": south and north edges change by 0.500 m
    base, conn = either.base, either.conn
    lengths = {name: edge_length_m(conn, edge_id) for name, edge_id in base.edges.items()}
    assert lengths == pytest.approx({
        "south_left": 2.8, "south_right": 3.2,
        "north_left": 2.8, "north_right": 3.2,
        "east": 4.0, "west": 4.0, "partition": 4.0,
    })


def test_partition_edge_position(either):
    # "変わるもの": the partition edge moves to x = 2800, length stays 4.000 m
    base, conn = either.base, either.conn
    assert {x for x, _ in edge_points(conn, base.edges["partition"])} == {2800}


def test_left_room(either):
    # "変わるもの": left room 11.20 m2, 13.600 m; no name, ceiling height not set
    from building_db.calc import room_area_m2, room_perimeter_m

    room_id = either.base.rooms["left"]
    assert room_area_m2(either.conn, room_id) == pytest.approx(11.20)
    assert room_perimeter_m(either.conn, room_id) == pytest.approx(13.600)
    assert either.conn.execute(
        "SELECT room_no, name, ceiling_height FROM rooms WHERE room_id = ?", (room_id,)
    ).fetchone() == (None, None, None)


def test_right_room(either):
    # "変わるもの": right room 12.80 m2, 14.400 m; 101 事務室, ceiling height 2700 mm
    from building_db.calc import room_area_m2, room_perimeter_m

    room_id = either.base.rooms["right"]
    assert room_area_m2(either.conn, room_id) == pytest.approx(12.80)
    assert room_perimeter_m(either.conn, room_id) == pytest.approx(14.400)
    assert either.conn.execute(
        "SELECT room_no, name, ceiling_height FROM rooms WHERE room_id = ?", (room_id,)
    ).fetchone() == ("101", "事務室", 2700)


def test_totals(either):
    # "変わらない合計": area 24.00 m2, perimeters 28.000 m, edges 24.000 m, walls 24.000 m
    from building_db.calc import room_area_m2, room_perimeter_m, wall_lengths_m

    conn = either.conn
    room_ids = ids(conn, "rooms", "room_id")
    assert sum(room_area_m2(conn, r) for r in room_ids) == pytest.approx(24.00)
    assert sum(room_perimeter_m(conn, r) for r in room_ids) == pytest.approx(28.000)
    assert sum(edge_length_m(conn, e) for e in ids(conn, "edges", "edge_id")) == pytest.approx(24.000)
    assert sum(wall_lengths_m(conn).values()) == pytest.approx(24.000)


def test_wall_types(either):
    # "変わらないもの": all 5 walls are WT-3
    rows = either.conn.execute(
        "SELECT t.type_code FROM walls w JOIN wall_types t ON w.wall_type_id = t.type_id"
    ).fetchall()
    assert rows == [("WT-3",)] * 5


def test_outer_walls_not_moved(either):
    # "比較する図面": the 4 outer walls do not move
    assert_walls_identified(either)
    base, conn = either.base, either.conn
    assert {name: wall_points(conn, base.walls[name]) for name in ("south", "north", "east", "west")} == {
        "south": ((0, 0), (6000, 0)),
        "north": ((0, 4000), (6000, 4000)),
        "east": ((6000, 0), (6000, 4000)),
        "west": ((0, 0), (0, 4000)),
    }


def test_calculated_values_are_not_stored(either):
    # "確かめたいこと": area and perimeter are recalculated, not stored values rewritten
    for table in ("rooms", "edges", "walls"):
        columns = [r[1] for r in either.conn.execute(f"PRAGMA table_info({table})")]
        assert not [c for c in columns if "area" in c or "perimeter" in c or "length" in c]


# --- step2_moved.dxf ----------------------------------------------------------


def test_moved_partition_wall(moved):
    # "変わるもの": the partition wall moves to x = 2800, both observed and adopted
    wall_id = moved.base.walls["partition"]
    expected = ((2800, 0), (2800, 4000))
    assert wall_points(moved.conn, wall_id, "observed_geometry") == expected
    assert wall_points(moved.conn, wall_id, "adopted_geometry") == expected


def test_moved_review_flags(moved):
    # "変わらないもの": 1 flag, "no room name" for the left room; no wall deviation
    assert len(moved.result.review_flags) == 1
    flag = moved.result.review_flags[0]
    assert "no room name" in flag
    assert "2800" in flag and "6000" not in flag


def test_moved_no_wall_deviation(moved):
    assert moved.result.wall_deviations == []


@NEEDS_VERSIONS
def test_moved_diff_nothing_added_or_missing(moved):
    from building_db.versions import diff_versions

    diff = diff_versions(moved.base_db, moved.db)
    assert list(diff.added) == []
    assert list(diff.missing) == []


@NEEDS_VERSIONS
def test_moved_diff_nodes(moved):
    # Diff table: only the two T-junction nodes change, x +500
    from building_db.versions import diff_versions

    nodes = moved.base.nodes
    assert changes_of(diff_versions(moved.base_db, moved.db), "node", "position") == {
        nodes[(2300, 0)]: ((2300, 0), (2800, 0)),
        nodes[(2300, 4000)]: ((2300, 4000), (2800, 4000)),
    }


@NEEDS_VERSIONS
def test_moved_diff_edge_lengths(moved):
    # Diff table: south and north edges change; the partition stays 4.000 m
    from building_db.versions import diff_versions

    edges = moved.base.edges
    changes = changes_of(diff_versions(moved.base_db, moved.db), "edge", "length_m")
    assert set(changes) == {edges["south_left"], edges["south_right"], edges["north_left"], edges["north_right"]}
    assert changes[edges["south_left"]] == pytest.approx((2.3, 2.8))
    assert changes[edges["south_right"]] == pytest.approx((3.7, 3.2))
    assert changes[edges["north_left"]] == pytest.approx((2.3, 2.8))
    assert changes[edges["north_right"]] == pytest.approx((3.7, 3.2))


@NEEDS_VERSIONS
def test_moved_diff_wall(moved):
    # Diff table: only the partition wall changes position
    from building_db.versions import diff_versions

    assert changes_of(diff_versions(moved.base_db, moved.db), "wall", "adopted_geometry") == {
        moved.base.walls["partition"]: (((2300, 0), (2300, 4000)), ((2800, 0), (2800, 4000))),
    }


@NEEDS_VERSIONS
def test_moved_diff_rooms(moved):
    # Diff table: left +2.00 m2 and +1.000 m, right -2.00 m2 and -1.000 m
    from building_db.versions import diff_versions

    rooms = moved.base.rooms
    diff = diff_versions(moved.base_db, moved.db)
    areas = changes_of(diff, "room", "area_m2")
    perimeters = changes_of(diff, "room", "perimeter_m")
    assert areas[rooms["left"]] == pytest.approx((9.20, 11.20))
    assert areas[rooms["right"]] == pytest.approx((14.80, 12.80))
    assert perimeters[rooms["left"]] == pytest.approx((12.600, 13.600))
    assert perimeters[rooms["right"]] == pytest.approx((15.400, 14.400))


# --- step2_boundary_only.dxf: the wall does not move --------------------------


def test_boundary_only_observed_wall(boundary_only):
    # "壁は動かさない": observed position x = 2300
    assert_walls_identified(boundary_only)
    wall_id = boundary_only.base.walls["partition"]
    assert wall_points(boundary_only.conn, wall_id, "observed_geometry") == ((2300, 0), (2300, 4000))


def test_boundary_only_adopted_wall(boundary_only):
    # "壁は動かさない": adopted position stays at x = 2300 without approval
    assert_walls_identified(boundary_only)
    wall_id = boundary_only.base.walls["partition"]
    assert wall_points(boundary_only.conn, wall_id, "adopted_geometry") == ((2300, 0), (2300, 4000))


def test_boundary_only_wall_still_follows_the_edge(boundary_only):
    # "壁は動かさない": expected position comes from the edge the wall follows
    assert_walls_identified(boundary_only)
    base, conn = boundary_only.base, boundary_only.conn
    wall_id = base.walls["partition"]
    assert conn.execute("SELECT follow_edge FROM walls WHERE wall_id = ?", (wall_id,)).fetchone() == (1,)
    assert conn.execute(
        "SELECT edge_id FROM wall_edge_spans WHERE wall_id = ?", (wall_id,)
    ).fetchall() == [(base.edges["partition"],)]


def test_boundary_only_wall_length(boundary_only):
    # "壁は動かさない": the partition stays 4.000 m
    from building_db.calc import wall_lengths_m

    assert_walls_identified(boundary_only)
    lengths = wall_lengths_m(boundary_only.conn)
    assert lengths[boundary_only.base.walls["partition"]] == pytest.approx(4.000)


def test_boundary_only_two_review_flags(boundary_only):
    # "要確認は2件": no room name, and one wall deviation naming both IDs and 500 mm
    base = boundary_only.base
    flags = boundary_only.result.review_flags
    assert len(flags) == 2
    assert sum("no room name" in f for f in flags) == 1
    other = next(f for f in flags if "no room name" not in f)
    assert base.walls["partition"] in other
    assert base.edges["partition"] in other
    assert "500" in other


def test_boundary_only_deviation_not_split(boundary_only):
    # "バラバラには出さない": not reported as "edge without wall" + "line on no edge"
    flags = boundary_only.result.review_flags
    assert not [f for f in flags if "has no wall" in f or "does not follow any boundary" in f]


def test_boundary_only_deviation_record(boundary_only):
    # "要確認は2件": the deviation offers two choices
    base = boundary_only.base
    deviations = boundary_only.result.wall_deviations
    assert len(deviations) == 1
    d = deviations[0]
    assert d.wall_id == base.walls["partition"]
    assert d.edge_id == base.edges["partition"]
    assert d.distance_mm == pytest.approx(500)
    assert d.expected_x_mm == pytest.approx(2800)
    assert list(d.options) == [ALIGN, UNFOLLOW]


# --- Recording a decision -----------------------------------------------------


@NEEDS_VERSIONS
def test_decision_fields(boundary_only):
    # "判断の記録": target ID, choice, reason, basis (a person), date, premise valid
    from building_db.versions import decisions_for, record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, reason="Follow the plan", decided_on=DECIDED_ON)
    records = decisions_for(boundary_only.db, wall_id)
    assert len(records) == 1
    r = records[0]
    assert (r.target_id, r.choice, r.reason, r.basis_kind, r.decided_on, r.premise_valid) == (
        wall_id, ALIGN, "Follow the plan", "person", DECIDED_ON, True,
    )


@NEEDS_VERSIONS
def test_decision_reason_is_optional(boundary_only):
    # "理由を一言添えられる（省略もできる）"
    from building_db.versions import decisions_for, record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, UNFOLLOW, decided_on=DECIDED_ON)
    assert decisions_for(boundary_only.db, wall_id)[0].reason is None


@NEEDS_VERSIONS
def test_align_moves_adopted_only(boundary_only):
    # "境界線に合わせる": adopted becomes x = 2800; observed stays x = 2300
    from building_db.versions import record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, decided_on=DECIDED_ON)
    conn = sqlite3.connect(boundary_only.db)
    try:
        assert wall_points(conn, wall_id, "adopted_geometry") == ((2800, 0), (2800, 4000))
        assert wall_points(conn, wall_id, "observed_geometry") == ((2300, 0), (2300, 4000))
    finally:
        conn.close()


@NEEDS_VERSIONS
def test_align_proposes_drawing_fix(boundary_only):
    # "境界線に合わせる": proposes moving the wall center line to x = 2800
    from building_db.versions import drawing_fix_proposals, record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, decided_on=DECIDED_ON)
    proposals = drawing_fix_proposals(boundary_only.db)
    assert [(p.wall_id, p.position_mm) for p in proposals] == [(wall_id, 2800)]


@NEEDS_VERSIONS
def test_align_does_not_rewrite_the_drawing(boundary_only):
    # "図面は自動で書き換えない"
    from building_db.versions import record_decision

    before = hashlib.sha256(boundary_only.dxf.read_bytes()).hexdigest()
    record_decision(boundary_only.db, boundary_only.base.walls["partition"], ALIGN, decided_on=DECIDED_ON)
    assert hashlib.sha256(boundary_only.dxf.read_bytes()).hexdigest() == before


@NEEDS_VERSIONS
def test_align_status_until_the_drawing_is_fixed(boundary_only):
    # "承認済み・図面未反映" until the drawing is fixed, also after re-importing
    from building_db.versions import record_decision, wall_status

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, decided_on=DECIDED_ON)
    assert wall_status(boundary_only.db, wall_id) == "approved_not_on_drawing"
    import_version(boundary_only.db, BOUNDARY_ONLY)
    assert wall_status(boundary_only.db, wall_id) == "approved_not_on_drawing"


@NEEDS_VERSIONS
def test_align_survives_reimport(boundary_only):
    # Importing the same drawing again does not undo the approved position
    from building_db.versions import record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, decided_on=DECIDED_ON)
    import_version(boundary_only.db, BOUNDARY_ONLY)
    conn = sqlite3.connect(boundary_only.db)
    try:
        assert wall_points(conn, wall_id, "adopted_geometry") == ((2800, 0), (2800, 4000))
        assert wall_points(conn, wall_id, "observed_geometry") == ((2300, 0), (2300, 4000))
    finally:
        conn.close()


@NEEDS_VERSIONS
def test_align_reimport_one_review_flag(boundary_only):
    # Owner's decision (2026-11-04): after "境界線に合わせる", re-importing gives
    # 1 flag (no room name); the wall stays shown as approved, not on the drawing
    from building_db.versions import record_decision, wall_status

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, decided_on=DECIDED_ON)
    result = import_version(boundary_only.db, BOUNDARY_ONLY)
    assert len(result.review_flags) == 1
    assert "no room name" in result.review_flags[0]
    assert result.wall_deviations == []
    assert wall_status(boundary_only.db, wall_id) == "approved_not_on_drawing"


@NEEDS_VERSIONS
def test_align_status_clears_when_the_drawing_is_fixed(boundary_only):
    # "図面が直るまで": step2_moved.dxf has the wall center line at x = 2800
    from building_db.versions import drawing_fix_proposals, record_decision, wall_status

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, ALIGN, decided_on=DECIDED_ON)
    import_version(boundary_only.db, MOVED)
    assert wall_status(boundary_only.db, wall_id) is None
    assert list(drawing_fix_proposals(boundary_only.db)) == []


@NEEDS_VERSIONS
def test_unfollow_keeps_the_wall(boundary_only):
    # "追従を外す": follow_edge is off and the wall stays at x = 2300
    from building_db.versions import record_decision, wall_status

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, UNFOLLOW, decided_on=DECIDED_ON)
    conn = sqlite3.connect(boundary_only.db)
    try:
        assert conn.execute("SELECT follow_edge FROM walls WHERE wall_id = ?", (wall_id,)).fetchone() == (0,)
        assert wall_points(conn, wall_id, "adopted_geometry") == ((2300, 0), (2300, 4000))
    finally:
        conn.close()
    assert wall_status(boundary_only.db, wall_id) == "approved_exception"


@NEEDS_VERSIONS
def test_unfollow_reimport_keeps_the_exception(boundary_only):
    # "追従を外す", re-imported: 1 flag (no room name); the deviation stays as an
    # approved exception
    from building_db.versions import accepted_exceptions, record_decision, wall_status

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, UNFOLLOW, decided_on=DECIDED_ON)
    result = import_version(boundary_only.db, BOUNDARY_ONLY)
    assert len(result.review_flags) == 1
    assert "no room name" in result.review_flags[0]
    assert result.wall_deviations == []
    assert wall_status(boundary_only.db, wall_id) == "approved_exception"
    exceptions = accepted_exceptions(boundary_only.db)
    assert [(e.wall_id, e.distance_mm) for e in exceptions] == [(wall_id, pytest.approx(500))]


@NEEDS_VERSIONS
def test_unfollow_stays_in_report(boundary_only, capsys):
    # "レポートには残る"
    from building_db.cli import main
    from building_db.versions import record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, UNFOLLOW, decided_on=DECIDED_ON)
    import_version(boundary_only.db, BOUNDARY_ONLY)
    capsys.readouterr()
    assert main(["report", "--db", str(boundary_only.db)]) == 0
    out = capsys.readouterr().out
    assert "exception" in out.lower()
    assert wall_id in out


@NEEDS_VERSIONS
@pytest.mark.parametrize("choice", [ALIGN, UNFOLLOW])
def test_decision_stays_on_the_wall_after_reimport(boundary_only, choice):
    # "記録は、次に同じ図面を取り込んでも、同じ壁に付いたまま残る"
    from building_db.versions import decisions_for, record_decision

    wall_id = boundary_only.base.walls["partition"]
    record_decision(boundary_only.db, wall_id, choice, reason="Checked", decided_on=DECIDED_ON)
    import_version(boundary_only.db, BOUNDARY_ONLY)
    assert [(r.target_id, r.choice, r.reason) for r in decisions_for(boundary_only.db, wall_id)] == [
        (wall_id, choice, "Checked"),
    ]
    assert wall_id in ids_of_walls(boundary_only.db)


def ids_of_walls(db):
    conn = sqlite3.connect(db)
    try:
        return ids(conn, "walls", "wall_id")
    finally:
        conn.close()
