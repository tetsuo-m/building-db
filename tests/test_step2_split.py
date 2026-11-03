"""Step 2: one room split into two by a partition at x = 2300.

Expected values come from the expected-values table in
samples/step2/expected.md. Each test names the row it checks.
IDs are not checked; rooms are told apart by their x range
(left: x 0-2300, right: x 2300-6000).
"""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV

STEP2_DXF = Path(__file__).resolve().parent.parent / "samples" / "step2" / "step2.dxf"


def run_step2_import(db_path):
    from building_db.importer import import_drawing

    return import_drawing(
        db_path=db_path,
        dxf_path=STEP2_DXF,
        wall_types_csv=WALL_TYPES_CSV,
        finish_schedule_csv=FINISH_SCHEDULE_CSV,
        default_wall_type=DEFAULT_WALL_TYPE,
    )


@pytest.fixture
def step2(tmp_path):
    db = tmp_path / "step2.sqlite"
    result = run_step2_import(db)
    conn = sqlite3.connect(db)
    yield result, conn
    conn.close()


def rooms_by_side(conn):
    """{"left": room_id, "right": room_id} from the x range of each room."""
    rows = conn.execute(
        """SELECT rb.room_id, MIN(n.x), MAX(n.x)
           FROM room_boundaries rb
           JOIN edges e ON e.edge_id = rb.edge_id
           JOIN nodes n ON n.node_id IN (e.node_start, e.node_end)
           GROUP BY rb.room_id"""
    ).fetchall()
    sides = {}
    for room_id, xmin, xmax in rows:
        if (round(xmin), round(xmax)) == (0, 2300):
            sides["left"] = room_id
        elif (round(xmin), round(xmax)) == (2300, 6000):
            sides["right"] = room_id
    return sides


def edge_lengths_m(conn):
    rows = conn.execute(
        """SELECT e.edge_id, ns.x, ns.y, ne.x, ne.y FROM edges e
           JOIN nodes ns ON ns.node_id = e.node_start
           JOIN nodes ne ON ne.node_id = e.node_end"""
    ).fetchall()
    return {eid: ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 / 1000 for eid, x1, y1, x2, y2 in rows}


def test_grids(step2):
    # Row "通り芯": 4 grids, same as step 1
    _, conn = step2
    rows = conn.execute("SELECT label, direction, position FROM grids").fetchall()
    assert sorted((label, d, round(p, 3)) for label, d, p in rows) == [
        ("X1", "X", 0.0), ("X2", "X", 6000.0), ("Y1", "Y", 0.0), ("Y2", "Y", 4000.0),
    ]


def test_nodes(step2):
    # Row "節点": 4 corners and 2 T-junctions
    _, conn = step2
    rows = conn.execute("SELECT x, y FROM nodes").fetchall()
    assert sorted((round(x), round(y)) for x, y in rows) == [
        (0, 0), (0, 4000), (2300, 0), (2300, 4000), (6000, 0), (6000, 4000),
    ]


def test_t_junctions(step2):
    # Row "T字の接点": nodes where 3 boundary edges meet are (2300,0) and (2300,4000)
    _, conn = step2
    rows = conn.execute(
        """SELECT n.x, n.y, COUNT(*) FROM nodes n
           JOIN edges e ON n.node_id IN (e.node_start, e.node_end)
           GROUP BY n.node_id HAVING COUNT(*) = 3"""
    ).fetchall()
    assert sorted((round(x), round(y)) for x, y, _ in rows) == [(2300, 0), (2300, 4000)]


def test_boundary_edges(step2):
    # Row "部屋割り線": 7 edges, 2.3, 3.7, 3.7, 2.3, 4.0, 4.0, 4.0 m; total 24.000 m
    _, conn = step2
    lengths = edge_lengths_m(conn)
    assert sorted(lengths.values()) == pytest.approx([2.3, 2.3, 3.7, 3.7, 4.0, 4.0, 4.0])
    assert sum(lengths.values()) == pytest.approx(24.000)


def test_shared_edge(step2):
    # Row "共有する境界線": 1 edge (the partition), in both rooms with opposite sides
    _, conn = step2
    rows = conn.execute(
        """SELECT edge_id, GROUP_CONCAT(side, '') FROM room_boundaries
           GROUP BY edge_id HAVING COUNT(DISTINCT room_id) = 2"""
    ).fetchall()
    assert len(rows) == 1
    edge_id, sides = rows[0]
    assert sorted(sides) == ["A", "B"]
    xs = conn.execute(
        """SELECT ns.x, ne.x FROM edges e
           JOIN nodes ns ON ns.node_id = e.node_start
           JOIN nodes ne ON ne.node_id = e.node_end WHERE e.edge_id = ?""",
        (edge_id,),
    ).fetchone()
    assert [round(x) for x in xs] == [2300, 2300]


def test_two_closed_contours(step2):
    # Rows "閉じた輪郭" and "部屋": 2 contours (left x 0-2300, right x 2300-6000), 2 rooms
    _, conn = step2
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 2
    assert set(rooms_by_side(conn)) == {"left", "right"}


def test_left_room(step2):
    # Row "左の部屋": no name, 9.20 m2, 12.600 m, ceiling height not set
    from building_db.calc import room_area_m2, room_perimeter_m

    _, conn = step2
    room_id = rooms_by_side(conn)["left"]
    row = conn.execute(
        "SELECT room_no, name, ceiling_height FROM rooms WHERE room_id = ?", (room_id,)
    ).fetchone()
    assert row == (None, None, None)
    assert room_area_m2(conn, room_id) == pytest.approx(9.20)
    assert room_perimeter_m(conn, room_id) == pytest.approx(12.600)


def test_right_room(step2):
    # Row "右の部屋": 101 事務室, 14.80 m2, 15.400 m, ceiling height 2700 mm
    from building_db.calc import room_area_m2, room_perimeter_m

    _, conn = step2
    room_id = rooms_by_side(conn)["right"]
    row = conn.execute(
        "SELECT room_no, name, ceiling_height FROM rooms WHERE room_id = ?", (room_id,)
    ).fetchone()
    assert row == ("101", "事務室", 2700)
    assert room_area_m2(conn, room_id) == pytest.approx(14.80)
    assert room_perimeter_m(conn, room_id) == pytest.approx(15.400)


def test_totals(step2):
    # Rows "面積の合計" 24.00 m2 and "周長の合計" 28.000 m
    from building_db.calc import room_area_m2, room_perimeter_m

    _, conn = step2
    room_ids = [r[0] for r in conn.execute("SELECT room_id FROM rooms")]
    assert sum(room_area_m2(conn, r) for r in room_ids) == pytest.approx(24.00)
    assert sum(room_perimeter_m(conn, r) for r in room_ids) == pytest.approx(28.000)


def test_walls(step2):
    # Row "壁": 5 walls, 6.000, 4.000, 6.000, 4.000 and 4.000; total 24.000 m
    from building_db.calc import wall_lengths_m

    _, conn = step2
    lengths = wall_lengths_m(conn)
    assert sorted(lengths.values()) == pytest.approx([4.0, 4.0, 4.0, 6.0, 6.0])
    assert sum(lengths.values()) == pytest.approx(24.000)


def test_walls_span_edges(step2):
    # Row "壁と境界線の対応": south and north walls each stay one wall over two
    # edges; east, west and the partition follow one edge each
    _, conn = step2
    spans = dict(conn.execute("SELECT wall_id, COUNT(*) FROM wall_edge_spans GROUP BY wall_id"))
    by_position = {}
    for wall_id, geometry in conn.execute("SELECT wall_id, adopted_geometry FROM walls"):
        (x1, y1), (x2, y2) = json.loads(geometry)
        if round(y1) == round(y2) == 0:
            by_position["south"] = wall_id
        elif round(y1) == round(y2) == 4000:
            by_position["north"] = wall_id
        elif round(x1) == round(x2) == 0:
            by_position["west"] = wall_id
        elif round(x1) == round(x2) == 6000:
            by_position["east"] = wall_id
        elif round(x1) == round(x2) == 2300:
            by_position["partition"] = wall_id
    assert {k: spans.get(v, 0) for k, v in by_position.items()} == {
        "south": 2, "north": 2, "east": 1, "west": 1, "partition": 1,
    }


def test_wall_types(step2):
    # Row "壁の型": all 5 walls are WT-3
    _, conn = step2
    rows = conn.execute(
        "SELECT t.type_code FROM walls w JOIN wall_types t ON w.wall_type_id = t.type_id"
    ).fetchall()
    assert rows == [("WT-3",)] * 5


def test_review_flags(step2):
    # Row "要確認": 1 flag, "no room name" for the left room only
    result, _ = step2
    assert len(result.review_flags) == 1
    flag = result.review_flags[0]
    assert "no room name" in flag
    assert "2300" in flag and "6000" not in flag  # the left contour, x 0-2300


def test_reimport_keeps_ids(tmp_path):
    # Row "取り込み直し": importing the same file twice keeps every ID (T23)
    db = tmp_path / "step2.sqlite"
    tables = {"grids": "grid_id", "nodes": "node_id", "edges": "edge_id",
              "rooms": "room_id", "walls": "wall_id"}

    def snapshot():
        conn = sqlite3.connect(db)
        try:
            return {t: sorted(r[0] for r in conn.execute(f"SELECT {c} FROM {t}"))
                    for t, c in tables.items()}
        finally:
            conn.close()

    run_step2_import(db)
    first = snapshot()
    run_step2_import(db)
    assert snapshot() == first


def test_cli_report(tmp_path, capsys):
    # "実行方法": the import and report commands print both rooms
    from building_db.cli import main

    db = tmp_path / "cli.sqlite"
    assert main([
        "import", str(STEP2_DXF), "--db", str(db),
        "--wall-types", str(WALL_TYPES_CSV),
        "--finish-schedule", str(FINISH_SCHEDULE_CSV),
        "--default-wall-type", DEFAULT_WALL_TYPE,
    ]) == 0
    capsys.readouterr()
    assert main(["report", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "101 事務室" in out
    assert "9.20" in out and "14.80" in out
    assert "24.000" in out  # wall total
